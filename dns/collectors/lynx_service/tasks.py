import os
import subprocess
import logging
import re
import time
from datetime import datetime, timezone
from pymongo import MongoClient
from celery import Celery

# Configuración
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL")
MONGO_URI = os.environ.get("MONGO_URI")

# Configurar logging
logging.basicConfig(level=logging.ERROR, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('lynx_worker')

# Reducir logs de librerías externas
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("pymongo").setLevel(logging.CRITICAL)
logging.getLogger("celery").setLevel(logging.ERROR)

# Inicializar Celery
app = Celery('lynx_tasks', broker=CELERY_BROKER_URL)
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.broker_connection_retry_on_startup = True
app.conf.task_routes = {
    'tasks.analizar_enlaces': {'queue': 'lynx_tasks'}
}

def get_mongo_connection():
    """Crea una conexión a MongoDB"""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        return client, client["dominios_db"]
    except Exception as e:
        logger.error(f"Error al conectar a MongoDB: {str(e)}")
        raise

@app.task(bind=True, max_retries=2, default_retry_delay=60)
def analizar_enlaces(self, dominio):
    """Analiza enlaces de un dominio usando lynx y actualiza ambas colecciones"""
    # Crear conexión a MongoDB
    try:
        client, db = get_mongo_connection()
        col_lynx = db["dominios_lynx"]
        col_pendientes = db["dominios_pendientes"]
    except Exception as e:
        logger.error(f"Error conectando a MongoDB para {dominio}: {str(e)}")
        return {"error": str(e)}
    
    resultado = {
        "dominio": dominio,
        "fecha_analisis": datetime.utcnow(),
        "enlaces": [],
        "dominios_relacionados": [],
        "errores": []
    }
    
    try:
        urls_to_try = [f"https://{dominio}", f"http://{dominio}"]
        if not dominio.startswith("www."):
            urls_to_try.append(f"https://www.{dominio}")
            urls_to_try.append(f"http://www.{dominio}")
        
        success = False
        
        # Intentar cada variación de URL
        for url in urls_to_try:
            try:
                command = ["lynx", "-listonly", "-dump", url]
                proc = subprocess.run(command, capture_output=True, text=True, timeout=45)
                
                if proc.returncode == 0:
                    success = True
                    
                    # Procesar enlaces
                    lines = proc.stdout.splitlines()
                    dominios_unicos = set()
                    enlaces_unicos = set()
                    
                    for line in lines:
                        match = re.search(r'\s*\d+\.\s+(https?://\S+)', line)
                        if match:
                            url_encontrada = match.group(1)
                            enlaces_unicos.add(url_encontrada)
                            
                            # Extraer dominio de la URL sin el protocolo
                            domain_match = re.search(r'https?://([^/]+)', url_encontrada)
                            if domain_match:
                                dominio_relacionado = domain_match.group(1)
                                dominios_unicos.add(dominio_relacionado)
                    
                    resultado["enlaces"] = [re.sub(r'^https?://', '', url) for url in enlaces_unicos]
                    resultado["dominios_relacionados"] = list(dominios_unicos)
                    
                    break
                
            except Exception as e:
                continue
    
    except Exception as e:
        resultado["errores"].append(f"Error: {str(e)}")
        logger.error(f"Error analizando {dominio}: {str(e)}")
    
    # Actualizar ambas colecciones
    try:
        # Guardar resultados en dominios_lynx
        col_lynx.update_one(
            {"dominio": dominio},
            {"$set": resultado},
            upsert=True
        )
        
        # Marcar como procesado en dominios_pendientes
        col_pendientes.update_one(
            {"dominio": dominio},
            {"$set": {"procesado_por.lynx": True}}
        )
        
    except Exception as e:
        logger.error(f"Error guardando en MongoDB: {str(e)}")
    
    # Cerrar conexión
    client.close()
    
    return resultado

@app.task
def worker_loop():
    """Tarea que busca dominios pendientes para analizar"""
    
    while True:
        try:
            # Usar conexión local
            client, db = get_mongo_connection()
            col_pendientes = db["dominios_pendientes"]
            
            # Buscar dominio pendiente
            dominio_doc = col_pendientes.find_one_and_update(
                {"procesado_por.lynx": False, "procesado_por.lynx_iniciado": {"$exists": False}},
                {"$set": {"procesado_por.lynx_iniciado": datetime.now(timezone.utc)}},
                sort=[("_id", 1)]
            )
            
            if dominio_doc:
                dominio = dominio_doc["dominio"]
                analizar_enlaces.delay(dominio)
            else:
                time.sleep(60)
            
            # Cerrar conexión
            client.close()
                
        except Exception as e:
            logger.error(f"Error en worker_loop: {str(e)}")
            time.sleep(30)