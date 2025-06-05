import sys
import re
from pymongo import MongoClient
import os
import logging

# Configuración de logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('importador')

# MongoDB connection
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://RISP1:Ye4WtTk3NHxrbr@mongodb:27017/")
client = MongoClient(MONGO_URI)
db = client["dominios_db"]
dominios_pendientes = db["dominios_pendientes"]

def import_domain(dominio, titular="", identificacion=""):
    """Importar dominio a MongoDB en lugar de encolar tareas"""
    # Preparar documento
    documento = {
        "dominio": dominio,
        "titular": titular or "",  # Asegurar que no sea None
        "identificacion": identificacion or "",  # Asegurar que no sea None
        "procesado_por": {
            "main": False,
            "lynx": False,
            "certgraph": False,
            "opendata": False  
        }
    }
    
    # Usar upsert para evitar duplicados
    result = dominios_pendientes.update_one(
        {"dominio": dominio}, 
        {"$setOnInsert": documento}, 
        upsert=True
    )
    
    if result.upserted_id:
        logger.info(f"Importado nuevo dominio: {dominio}")
        return True
    else:
        logger.info(f"Dominio ya existe: {dominio}")
        return False

def importar_dominios_desde_archivo(path):
    """Importar dominios desde RISP_OTROS.csv a MongoDB"""
    count_new = 0
    count_existing = 0
    count_empty = 0
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            for linea_num, linea in enumerate(f, 1):
                linea = linea.strip()
                if not linea:
                    continue
                if linea.startswith('"NOMBRE_DOMINIO"'):
                    continue
                    
                partes = linea.strip('"').split('"|"')
                
                # Verificar que al menos tenemos el dominio
                if not partes or not partes[0]:
                    logger.warning(f"Línea {linea_num} ignorada - formato inválido: {linea}")
                    continue
                
                dominio = partes[0]
                # Obtener titular e identificación si existen, de lo contrario usar cadenas vacías
                titular = partes[1] if len(partes) > 1 else ""
                identificacion = re.sub(r'[-\s]', '', partes[2]) if len(partes) > 2 else ""
                
                if not titular and not identificacion:
                    count_empty += 1
                
                if import_domain(dominio, titular, identificacion):
                    count_new += 1
                else:
                    count_existing += 1
                    
    except Exception as e:
        logger.error(f"Error procesando archivo CSV: {str(e)}")
        
    return f"Importación completada: {count_new} nuevos dominios, {count_existing} dominios existentes, {count_empty} dominios con datos incompletos"

if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/app/RISP_OTROS.csv"
    result = importar_dominios_desde_archivo(filepath)
    print(result)