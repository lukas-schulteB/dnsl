import os
import re
import json
import logging
import time
import random
from datetime import datetime, timezone, timedelta
import requests
from pymongo import MongoClient
from celery import Celery

# Configuración
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL")
MONGO_URI = os.environ.get("MONGO_URI")

# Configurar logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('opendata_worker')

# Inicializar Celery
app = Celery('opendata_tasks', broker=CELERY_BROKER_URL)
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.broker_connection_retry_on_startup = True

# Configurar rutas de tareas
app.conf.task_routes = {
    'tasks.obtener_info_empresa_task': {'queue': 'opendata_tasks'},
    'tasks.procesar_dominio_empresa': {'queue': 'opendata_tasks'}
}

# Función para conexión a MongoDB
def get_mongo_db():
    client = MongoClient(MONGO_URI)
    return client, client["dominios_db"]

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def obtener_info_empresa_task(self, term: str, dominio: str = None):
    """Tarea Celery para obtener información de empresas"""
    try:
        resultado = obtener_info_empresa(term)
        
        # Si se especificó un dominio, guardar directamente el resultado
        if dominio:
            procesar_y_guardar(dominio, term, resultado)
        
        return resultado
    except Exception as e:
        logger.error(f"Error en obtener_info_empresa_task: {str(e)}")
        # Reintentar en caso de error
        if self.request.retries < self.max_retries:
            logger.info(f"Reintentando consulta para '{term}', intento {self.request.retries + 1}")
            raise self.retry(exc=e)
        return {"error": str(e), "data": []}

@app.task
def procesar_dominio_empresa(dominio: str, term: str):
    """Procesa un dominio para obtener su información empresarial"""
    logger.info(f"Procesando información empresarial para dominio: {dominio} (término: {term})")
    
    try:
        resultado = obtener_info_empresa(term)
        procesar_y_guardar(dominio, term, resultado)
        return {"dominio": dominio, "estado": "completado"}
    except Exception as e:
        logger.error(f"Error procesando {dominio}: {str(e)}")
        return {"dominio": dominio, "estado": "error", "error": str(e)}

def procesar_y_guardar(dominio: str, term: str, resultado: dict):
    """Procesa el resultado y lo guarda en MongoDB"""
    try:
        client, db = get_mongo_db()
        col_empresa = db["dominios_empresa"]
        col_pendientes = db["dominios_pendientes"]
        
        # Preparar datos de empresa
        datos = resultado.get("data", [])
        info_empresa = {
            "dominio": dominio,
            "termino_busqueda": term,
            "fecha_consulta": datetime.now(timezone.utc)
            # Quitado resultado_raw como solicitado
        }
        
        if datos:
            primera = datos[0]
            info_empresa["empresa"] = {
                "nif": primera.get("nif"),
                "denominacion": primera.get("denominacion"),
                "estado": primera.get("estado"),
                "domicilio": {
                    "direccion": primera.get("domicilioSocial", {}).get("direccion"),
                    "poblacion": primera.get("domicilioSocial", {}).get("poblacion"),
                    "provincia": primera.get("domicilioSocial", {}).get("provincia")
                },
                "actividad_economica": {
                    "cnae_codigo": primera.get("actividadEconomica", {}).get("principal", {}).get("cnae", {}).get("codigo"),
                    "cnae_descripcion": primera.get("actividadEconomica", {}).get("principal", {}).get("cnae", {}).get("descripcion")
                },
                "forma_social": primera.get("formaSocial", {}).get("nacional", {}).get("descripcion"),
                "registro": primera.get("registro", {}).get("nombre"),
                "euid": primera.get("euid", {}).get("valor")
            }
        else:
            info_empresa["empresa_error"] = f"Sin datos para term='{term}'"
        
        # Guardar en colección específica para empresas
        col_empresa.update_one(
            {"dominio": dominio},
            {"$set": info_empresa},
            upsert=True
        )
        
        # Marcar como procesado en colección pendientes
        col_pendientes.update_one(
            {"dominio": dominio},
            {"$set": {"procesado_por.opendata": True}}
        )
        
        logger.info(f"Información de empresa guardada para {dominio}")
        client.close()
    except Exception as e:
        logger.error(f"Error guardando información de empresa para {dominio}: {str(e)}")

def obtener_info_empresa(term: str) -> dict:
    """Obtiene información de empresas desde el OpenData de Registradores"""
    logger.info(f"Consultando OpenData para: '{term}'")
    
    # URL y parámetros
    url = "https://opendata.registradores.org/directorio"
    params = {
        "p_p_id": "org_registradores_opendata_portlet_BuscadorSociedadesPortlet",
        "p_p_lifecycle": "2",
        "p_p_state": "normal",
        "p_p_mode": "view",
        "p_p_resource_id": "/opendata/sociedades",
        "p_p_cacheability": "cacheLevelPage",
        "_org_registradores_opendata_portlet_BuscadorSociedadesPortlet_term": term
    }
    
    # Rotar User-Agent para simular diferentes navegadores
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ]
    
    headers = {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Referer": "https://opendata.registradores.org/directorio",
        "Origin": "https://opendata.registradores.org",
        "X-Requested-With": "XMLHttpRequest",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    
    try:
        # Usamos POST en lugar de GET con una sesión nueva cada vez
        session = requests.Session()
        
        # 1. Visitar la página de inicio para obtener cookies y sesión
        home_resp = session.get(
            "https://opendata.registradores.org/directorio", 
            timeout=15,
            headers={"User-Agent": headers["User-Agent"]}
        )
        
        # Pequeña pausa para simular comportamiento humano
        time.sleep(random.uniform(1, 3))
        
        # 2. Hacer la búsqueda usando POST
        resp = session.post(
            url, 
            params=params,
            data=params,
            headers=headers,
            timeout=30  
        )
        resp.raise_for_status()
        
        # Buscar patrones de JSON en la respuesta
        content_type = resp.headers.get('Content-Type', '')
        
        # Imprimir primeros 200 caracteres para depuración
        logger.debug(f"Respuesta (primeros 200 chars): {resp.text[:200]}")
        
        if "application/json" in content_type:
            return resp.json()
        elif "text/html" in content_type:
            # Intentar varios patrones para extraer JSON
            patterns = [
                r'(\{"data":\s*\[.+?\]\})',  # Patrón original
                r'("data":\s*\[.+?\])',      # Patrón alternativo sin llaves
                r'(\[\s*\{.*?"nif":.+?\}\s*\])'  # Array directo de objetos
            ]
            
            for pattern in patterns:
                try:
                    matches = re.search(pattern, resp.text, re.DOTALL)
                    if matches:
                        json_str = matches.group(1)
                        # Asegurarse de que es un JSON válido
                        if not json_str.startswith('{'):
                            json_str = '{"data": ' + json_str + '}'
                        return json.loads(json_str)
                except Exception as e:
                    logger.debug(f"Error con patrón {pattern}: {str(e)}")
                    continue
            
            # Verificar si hay una respuesta vacía pero válida
            if "sin resultados" in resp.text.lower() or "no se encontraron" in resp.text.lower():
                logger.info(f"Respuesta vacía para '{term}', probablemente sin datos")
                return {"data": []}
                
            logger.error(f"No se encontró JSON en la respuesta HTML para: {term}")
        
        return {"error": "Formato de respuesta no reconocido", "data": []}
        
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            logger.warning(f"Rate limit excedido, esperando 60 segundos antes de reintentar...")
            time.sleep(60)
        
        logger.error(f"Error de conexión: {str(e)}")
        return {"error": str(e), "data": []}
    except Exception as e:
        logger.error(f"Error inesperado: {str(e)}")
        return {"error": str(e), "data": []}

@app.task
def worker_loop():
    """Tarea que busca constantemente dominios pendientes para consultar en OpenData"""
    logger.info("🚀 Iniciando worker loop del servicio OpenData")
    
    while True:
        try:
            # Crear conexión local a MongoDB
            client, db = get_mongo_db()
            col_pendientes = db["dominios_pendientes"]
            
            # Buscar UN SOLO dominio pendiente que tenga titular o identificación
            dominio_doc = col_pendientes.find_one_and_update(
                {
                    "procesado_por.opendata": {"$ne": True}, 
                    "procesado_por.opendata_iniciado": {"$exists": False},
                    "$or": [
                        {"titular": {"$exists": True, "$ne": ""}},
                        {"identificacion": {"$exists": True, "$ne": ""}}
                    ]
                },
                {"$set": {"procesado_por.opendata_iniciado": datetime.now(timezone.utc)}},
                sort=[("_id", 1)]
            )
            
            if dominio_doc:
                dominio = dominio_doc["dominio"]
                term = dominio_doc.get("identificacion") or dominio_doc.get("titular")
                
                if term:
                    # Procesar directamente en este mismo hilo en lugar de usar .delay()
                    # Esto evita que se encolen múltiples tareas simultáneas
                    logger.info(f"Procesando dominio con OpenData: {dominio} (término: {term})")
                    try:
                        resultado = obtener_info_empresa(term)
                        procesar_y_guardar(dominio, term, resultado)
                    except Exception as e:
                        col_pendientes.update_one(
                            {"dominio": dominio},
                            {"$set": {"procesado_por.opendata": True}}
                        )
                    
                    # Esperar 2 segundos antes de procesar el siguiente dominio
                    time.sleep(2)
                else:
                    # Si llegamos aquí, no hay término válido a pesar del filtro
                    logger.warning(f"Dominio {dominio} sin término válido para búsqueda")
                    col_pendientes.update_one(
                        {"dominio": dominio},
                        {"$set": {"procesado_por.opendata": True}}
                    )
            else:
                logger.info("No hay dominios pendientes para OpenData, esperando...")
                time.sleep(30)
            
            client.close()
                
        except Exception as e:
            logger.error(f"Error en worker_loop: {str(e)}")
            time.sleep(30)

if __name__ == "__main__":
    worker_loop()