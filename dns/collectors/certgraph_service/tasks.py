import os
import subprocess
import logging
import json
import time
import re
import base64
from datetime import datetime, timezone
from pymongo import MongoClient
from celery import Celery

#test6
# Configuración
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL")
MONGO_URI = os.environ.get("MONGO_URI")

# Configurar logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('certgraph_worker')

# Inicializar Celery
app = Celery('certgraph_tasks', broker=CELERY_BROKER_URL)
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.broker_connection_retry_on_startup = True
app.conf.task_routes = {
    'tasks.analizar_certificados': {'queue': 'certgraph_tasks'}
}

def get_mongo_db():
    client = MongoClient(MONGO_URI)
    return client, client["dominios_db"]

def extract_certificate_details(cert_output):
    """Extrae información detallada del certificado desde la salida de OpenSSL"""
    cert_data = {}
    
    # Extraer información básica
    match = re.search(r'subject=(.+?)\n', cert_output)
    if match:
        cert_data["subject"] = match.group(1).strip()
    
    match = re.search(r'issuer=(.+?)\n', cert_output)
    if match:
        cert_data["issuer"] = match.group(1).strip()
    
    # Extraer fechas de validez
    match = re.search(r'notBefore=(.+?)\n', cert_output)
    if match:
        cert_data["valid_from"] = match.group(1).strip()
    
    match = re.search(r'notAfter=(.+?)\n', cert_output)
    if match:
        cert_data["valid_until"] = match.group(1).strip()
    
    # Obtener nombres alternativos de sujeto (SAN)
    match = re.search(r'X509v3 Subject Alternative Name:\s*\n(.*?)(?:\n\n|\n[A-Z])', cert_output, re.DOTALL)
    if match:
        sans = match.group(1).strip()
        domains = re.findall(r'DNS:([^,\s]+)', sans)
        cert_data["alternative_names"] = domains
    
    # Obtener algoritmo y longitud de clave
    match = re.search(r'Public Key Algorithm: ([^\n]+)', cert_output)
    if match:
        cert_data["key_algorithm"] = match.group(1).strip()
    
    match = re.search(r'RSA Public-Key: \((\d+) bit\)', cert_output)
    if match:
        cert_data["key_size"] = int(match.group(1))
    
    # Extraer huella digital - MODIFICADO para eliminar los dos puntos
    match = re.search(r'SHA1 Fingerprint=([0-9A-F:]+)', cert_output)
    if match:
        cert_data["fingerprint"] = match.group(1).strip().replace(':', '')
    
    # Extraer versión
    match = re.search(r'Version: (\d+)', cert_output)
    if match:
        cert_data["version"] = int(match.group(1))
    
    # Extraer número de serie
    match = re.search(r'Serial Number:[\s\n]+([\S\s]+?)(?:\n\s*Signature)', cert_output)
    if match:
        cert_data["serial_number"] = match.group(1).replace(':', '').strip()
    
    return cert_data

@app.task(bind=True, max_retries=2, default_retry_delay=60)
def analizar_certificados(self, dominio):
    """Analiza certificados SSL con información detallada"""

    
    # Crear conexión local a MongoDB
    try:
        client, db = get_mongo_db()
        col_certgraph = db["dominios_certgraph"]
        col_pendientes = db["dominios_pendientes"]
    except Exception as e:
        logger.error(f"❌ Error conectando a MongoDB: {str(e)}")
        return {"error": str(e)}
    
    # Estructura básica del resultado
    resultado = {
        "dominio": dominio,
        "fecha_analisis": datetime.utcnow(),
        "certificados": [],
        "dominios_relacionados": set(),
        "errores": []
    }
    
    # Probar tanto la versión con www como sin www
    dominios_a_probar = [dominio]
    if not dominio.startswith("www."):
        dominios_a_probar.append(f"www.{dominio}")
    else:
        dominios_a_probar.append(dominio[4:])
    
    # Intentar obtener certificados con OpenSSL
    success = False
    for dom_to_try in dominios_a_probar:
        try:
            # Verificar primero si el dominio responde a HTTPS
            check_cmd = ["timeout", "10", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"https://{dom_to_try}"]
            proc = subprocess.run(check_cmd, capture_output=True, text=True)
            status_code = proc.stdout.strip()
            
            # Si responde, intentar obtener certificado detallado
            if status_code and status_code not in ["000", ""]:
                
                # Comando mejorado para obtener el certificado completo con detalles
                ssl_cmd = ["openssl", "s_client", "-connect", f"{dom_to_try}:443", 
                          "-servername", dom_to_try, "-showcerts"]
                ssl_proc = subprocess.run(ssl_cmd, input="", capture_output=True, text=True, timeout=20)
                
                # Comando adicional para obtener más detalles del certificado
                if "BEGIN CERTIFICATE" in ssl_proc.stdout:
                    # Extraer el certificado y guardarlo temporalmente
                    cert_text = re.search(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', 
                                        ssl_proc.stdout, re.DOTALL)
                    if cert_text:
                        cert_content = cert_text.group(0)
                        with open(f"/tmp/{dom_to_try}.crt", "w") as f:
                            f.write(cert_content)
                        
                        # Obtener información detallada con x509
                        x509_cmd = ["openssl", "x509", "-in", f"/tmp/{dom_to_try}.crt", 
                                   "-text", "-noout", "-fingerprint"]
                        x509_proc = subprocess.run(x509_cmd, capture_output=True, text=True)
                        
                        if x509_proc.returncode == 0:
                            # Extraer detalles completos del certificado
                            cert_data = extract_certificate_details(x509_proc.stdout)
                            
                            # Añadir al resultado
                            resultado["certificados"].append(cert_data)
                            
                            # Añadir dominios relacionados
                            if "alternative_names" in cert_data:
                                resultado["dominios_relacionados"].update(cert_data["alternative_names"])
                            
                            success = True
        
        except Exception as e:
            continue
    
    # Si no tuvimos éxito con ningún dominio
    if not success:
        resultado["errores"].append(f"No se pudo obtener información SSL para {dominio}")
    
    # Convertir set a lista para MongoDB
    resultado["dominios_relacionados"] = list(resultado["dominios_relacionados"])
    
    # Guardar resultado en MongoDB
    try:
        col_certgraph.update_one(
            {"dominio": dominio}, 
            {"$set": resultado}, 
            upsert=True
        )
        
        # Marcar como procesado
        col_pendientes.update_one(
            {"dominio": dominio},
            {"$set": {"procesado_por.certgraph": True}}
        )
    except Exception as e:
        logger.error(f"❌ Error al guardar en MongoDB: {str(e)}")
    
    # Cerrar conexión
    client.close()
    
    return {
        "dominio": dominio,
        "estado": "completado",
        "certificados_encontrados": len(resultado["certificados"]),
        "dominios_relacionados": len(resultado["dominios_relacionados"]),
        "errores": resultado["errores"]
    }

@app.task
def worker_loop():
    """Tarea que busca constantemente dominios pendientes para analizar con certgraph"""
    # Changed from INFO to WARNING for startup message - only appears once
    logger.warning("🚀 Iniciando worker loop del servicio certgraph")
    
    while True:
        try:
            # Crear conexión local a MongoDB
            client, db = get_mongo_db()
            col_pendientes = db["dominios_pendientes"]
            
            # Buscar dominio pendiente
            dominio_doc = col_pendientes.find_one_and_update(
                {"procesado_por.certgraph": False, "procesado_por.certgraph_iniciado": {"$exists": False}},
                {"$set": {"procesado_por.certgraph_iniciado": datetime.now(timezone.utc)}},
                sort=[("_id", 1)]
            )
            
            # Si encontramos un dominio, procesarlo
            if dominio_doc:
                dominio = dominio_doc["dominio"]
                
                # Enviar tarea a la cola
                analizar_certificados.delay(dominio)
            else:
                # Removed the "No hay dominios pendientes" log - unnecessary noise
                time.sleep(60)
            
            client.close()
                
        except Exception as e:
            logger.error(f"❌ Error en worker_loop: {str(e)}")
            time.sleep(30)

if __name__ == "__main__":
    worker_loop()