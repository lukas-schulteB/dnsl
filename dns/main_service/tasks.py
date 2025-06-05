import os
import csv
import ipaddress
import subprocess
import socket
import ssl
from functools import lru_cache
from datetime import datetime, timezone, timedelta
import time

import dns.resolver
import pymongo
import requests
from celery import Celery
from ipwhois import IPWhois

import logging

logging.basicConfig(level=logging.WARNING)  # Cambiado de INFO a WARNING
logger = logging.getLogger(__name__)


MONGO_URI = os.environ.get("MONGO_URI")

# Implementación de conexión lazy para evitar problemas con fork en Celery
_mongo_client = None
_mongo_db = None

def get_mongo_client():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = pymongo.MongoClient(MONGO_URI)
    return _mongo_client

def get_db():
    global _mongo_db
    if _mongo_db is None:
        _mongo_db = get_mongo_client()["dominios_db"]
    return _mongo_db

def get_col_actual():
    return get_db()["dominios_actuales"]

def get_col_historico():
    return get_db()["dominios_historico"]

def get_col_pendientes():
    """Obtener colección de dominios pendientes de procesar"""
    return get_db()["dominios_pendientes"]

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL")
app = Celery("tasks", broker=CELERY_BROKER_URL)

# Configure task routing to use explicit queue names
app.conf.task_routes = {
    'tasks.procesar_dominio': {'queue': 'main_queue'},
    'tasks.distribuir_dominios': {'queue': 'main_queue'},
    'tasks.procesar_dominio_individual': {'queue': 'main_queue'},
    'tasks.worker_loop': {'queue': 'main_queue'}
}

# Ensure both services start with same queue configuration
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.broker_connection_retry_on_startup = True

# Configure the beat schedule
app.conf.beat_schedule = {
    'distribuir-cada-10-segundos': {
        'task': 'tasks.distribuir_dominios',
        'schedule': 10.0,
    }
    # monitor task removed
}

# -------------------------------------------------------------------
# DNS resolver global
# -------------------------------------------------------------------
_resolver = dns.resolver.Resolver()
_resolver.timeout = 2
_resolver.lifetime = 5

# -------------------------------------------------------------------
# Carga rangos IP (solo códigos de país y continente)
# -------------------------------------------------------------------
rangos_ip = []
if os.path.exists("/app/ip_rangos.csv"):
    with open("/app/ip_rangos.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rangos_ip = [
            (
                int(ipaddress.ip_address(r["start_ip"])),
                int(ipaddress.ip_address(r["end_ip"])),
                r["country"],
                r["continent"]
            )
            for r in reader
        ]
    rangos_ip.sort(key=lambda x: x[0])

# -------------------------------------------------------------------
# LRU caches: GeoIP y ASN
# -------------------------------------------------------------------
@lru_cache(maxsize=10000)
def buscar_localizacion(ip_str: str) -> dict:
    ip_int = int(ipaddress.ip_address(ip_str))
    for start, end, country, continent in rangos_ip:
        if start <= ip_int <= end:
            return {"country": country, "continent": continent}
    return {}

@lru_cache(maxsize=4096)
def obtener_asn_info(ip_str: str) -> dict:
    try:
        res = IPWhois(ip_str).lookup_whois()
        return {
            "asn": res.get("asn"),
            "asn_cidr": res.get("asn_cidr"),
            "asn_desc": res.get("asn_desc"),
            "asn_country_code": res.get("asn_country_code")
        }
    except Exception as e:
        return {"error_asn": str(e)}

# -------------------------------------------------------------------
# Resolver registros DNS (sin guardar vacíos)
# -------------------------------------------------------------------
def resolver_registros_dns(dominio: str) -> dict:
    tipos = ["A", "MX", "NS", "TXT"]
    resultados = {}
    for t in tipos:
        items = []
        try:
            answers = _resolver.resolve(dominio, t)
            for r in answers:
                if t == "A":
                    ip = str(r)
                    items.append({
                        "ip": ip,
                        **obtener_asn_info(ip),
                        **buscar_localizacion(ip)
                    })
                elif t == "MX":
                    ex = r.exchange.to_text().rstrip('.')
                    pref = r.preference
                    ips = []
                    try:
                        for mx in _resolver.resolve(ex, "A"):
                            mip = str(mx)
                            ips.append({
                                "ip": mip,
                                **obtener_asn_info(mip),
                                **buscar_localizacion(mip)
                            })
                    except Exception:
                        pass
                    items.append({"exchange": ex, "preference": pref, "ips": ips})
                elif t == "NS":
                    host = r.to_text().rstrip('.')
                    ns_ips = []
                    try:
                        for a in _resolver.resolve(host, "A"):
                            ipns = str(a)
                            ns_ips.append({
                                "ip": ipns,
                                **obtener_asn_info(ipns),
                                **buscar_localizacion(ipns)
                            })
                    except Exception:
                        pass
                    items.append({"ns_host": host, "ips": ns_ips})
                else:  # TXT
                    txt = b" ".join(r.strings).decode(errors="ignore")
                    items.append({"txt": txt})
        except Exception:
            pass

        if items:
            resultados[t] = items
    return resultados


# -------------------------------------------------------------------
# Subfinder local 
# -------------------------------------------------------------------
def obtener_subdominios_local(domain: str, timeout: int = 300):
    subs, errors = set(), []
    try:
        proc = subprocess.run(
            ["subfinder", "-d", domain, "-silent"],
            capture_output=True, text=True,
            timeout=timeout, check=True
        )
        for ln in proc.stdout.splitlines():
            s = ln.strip().lower().rstrip(".")
            if s:
                subs.add(s)
    except subprocess.TimeoutExpired as e:
        errors.append(f"subfinder timeout tras {e.timeout}s")
    except subprocess.CalledProcessError as e:
        out = e.stderr or e.stdout or str(e)
        errors.append(f"subfinder error: {out}")
    except Exception as e:
        errors.append(f"subfinder excepción: {e}")

    filtered = [
        s for s in sorted(subs)
        if s.endswith(f".{domain}") or s == domain
    ]
    return filtered, errors

# -------------------------------------------------------------------
# Guardar en Mongo
# -------------------------------------------------------------------
def guardar_informacion(info: dict):
    get_col_historico().insert_one(info)
    clean = {k: v for k, v in info.items() if k != "_id"}
    get_col_actual().update_one(
        {"dominio": clean["dominio"]},
        {"$set": clean},
        upsert=True
    )

# -------------------------------------------------------------------
# Tarea Celery
# -------------------------------------------------------------------
@app.task(bind=True, max_retries=2, default_retry_delay=60)
def procesar_dominio(self, dominio: str, titular: str = "", identificacion: str = ""):
    info = {
        "dominio": dominio,
        "titular": titular,
        "identificacion": identificacion,
        "fecha_consulta": datetime.now(timezone.utc)
    }

    info["dns"] = resolver_registros_dns(dominio)
    subs, errs = obtener_subdominios_local(dominio)
    if errs:
        info["subdominios"] = [{"error": e} for e in errs]
    else:
        detalles = []
        for sub in subs:
            if sub == dominio:
                continue
            dns_sub = resolver_registros_dns(sub)
            if dns_sub:
                detalles.append({"subdominio": sub, "dns": dns_sub})
        info["subdominios"] = detalles

    guardar_informacion(info)
    return f"Procesado: {dominio}"

# -------------------------------------------------------------------
# Worker loop para dominios pendientes
# -------------------------------------------------------------------
@app.task
def worker_loop():
    """Tarea que supervisa el sistema de distribución de tareas"""
    logger.warning("🚀 Iniciando supervisor de distribución de tareas")
    
    # Start the distributor immediately
    distribuir_dominios.apply_async(countdown=1)
    
    # Exit - this task only needs to run once at startup
    return "Distribuidor iniciado correctamente"

@app.task
def distribuir_dominios():
    """Versión simplificada que permite a múltiples workers procesar dominios sin conflictos"""
    client = None
    try:
        client = pymongo.MongoClient(MONGO_URI)
        db = client["dominios_db"]
        col_pendientes = db["dominios_pendientes"]
        col_stats = db["worker_stats"]
        
        # Check if we're already processing a domain
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        in_progress = col_pendientes.count_documents({
            "procesado_por.main": {"$ne": True},
            "procesado_por.main_iniciado": {"$exists": True},
            "procesado_por.worker_id": worker_id
        })
        
        # Only claim new domain if we're not busy
        if in_progress == 0:
            # Intentar reclamar exactamente un dominio de forma atómica
            current_time = datetime.now(timezone.utc)
            domain_doc = col_pendientes.find_one_and_update(
                {
                    "procesado_por.main": {"$ne": True}, 
                    "procesado_por.main_iniciado": {"$exists": False}
                },
                {"$set": {
                    "procesado_por.main_iniciado": current_time,
                    "procesado_por.worker_id": worker_id
                }},
                sort=[("_id", 1)],
                return_document=pymongo.ReturnDocument.AFTER
            )
            
            if domain_doc:
                dominio = domain_doc["dominio"]
                titular = domain_doc.get("titular", "")
                identificacion = domain_doc.get("identificacion", "")
                
                # Change to debug level - only seen when needed
                logger.debug(f"🔹 Worker {worker_id} reclamó dominio: {dominio}")
                
                # Update statistics for this worker
                col_stats.update_one(
                    {"_id": worker_id},
                    {"$inc": {"dominios_reclamados": 1}}
                )
                
                start_time = datetime.now(timezone.utc)
                try:
                    # Change to debug - reduce log noise
                    logger.debug(f"▶️ Worker {worker_id} inicia procesamiento: {dominio}")
                    procesar_dominio(dominio, titular, identificacion)
                    
                    # Mark as processed
                    col_pendientes.update_one(
                        {"dominio": dominio},
                        {"$set": {
                            "procesado_por.main": True,
                            "procesado_por.completed_at": datetime.now(timezone.utc),
                            "procesado_por.processing_time": (datetime.now(timezone.utc) - start_time).total_seconds()
                        }}
                    )
                    
                    col_stats.update_one(
                        {"_id": worker_id},
                        {"$inc": {
                            "dominios_procesados": 1,
                            "tiempo_total_segundos": (datetime.now(timezone.utc) - start_time).total_seconds()
                        }}
                    )
                    
                    # Keep completion logs as they're useful for monitoring performance
                    # But simplify the format
                    logger.warning(f"✅ {dominio} completado en {(datetime.now(timezone.utc) - start_time).total_seconds():.2f}s")
                except Exception as e:
                    # Keep error logs
                    logger.error(f"❌ Error en {dominio}: {str(e)}")
                    col_stats.update_one(
                        {"_id": worker_id},
                        {"$inc": {"dominios_error": 1}}
                    )
                
                next_check = 1  # Check for another domain immediately
            else:
                # Check how many total pending domains exist
                pending_count = col_pendientes.count_documents({
                    "procesado_por.main": {"$ne": True}, 
                    "procesado_por.main_iniciado": {"$exists": False}
                })
                
                next_check = 10 if pending_count == 0 else 3
        else:
            # We're still processing a domain, check back later
            next_check = 5
            
        # Update heartbeat for monitoring
        col_stats.update_one(
            {"_id": worker_id},
            {"$set": {"last_heartbeat": datetime.now(timezone.utc)},
             "$inc": {"heartbeat_count": 1},
             "$setOnInsert": {"first_seen": datetime.now(timezone.utc)}},
            upsert=True
        )
            
        # Siempre programar la siguiente ejecución
        distribuir_dominios.apply_async(countdown=next_check)
        
    except Exception as e:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.error(f"❌ Error en worker {worker_id}: {str(e)}")
        distribuir_dominios.apply_async(countdown=5)
    finally:
        if client:
            client.close()

@app.task
def procesar_dominio_individual(dominio, titular="", identificacion=""):
    """Procesa un único dominio y actualiza su estado"""
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    start_time = datetime.now(timezone.utc)
    
    try:
        logger.warning(f"▶️ Worker {worker_id} inicia procesamiento: {dominio}")
        
        client = pymongo.MongoClient(MONGO_URI)
        db = client["dominios_db"]
        col_pendientes = db["dominios_pendientes"]
        col_stats = db["worker_stats"]
        
        # Check if domain is still valid to process
        domain_status = col_pendientes.find_one(
            {"dominio": dominio},
            {"procesado_por": 1}
        )
        
        # Skip if already processed
        if not domain_status:
            logger.warning(f"⚠️ Worker {worker_id}: Dominio {dominio} no encontrado")
            return None
            
        if domain_status.get("procesado_por", {}).get("main", False):
            logger.warning(f"⚠️ Worker {worker_id}: Dominio {dominio} ya procesado")
            return None
            
        # Check if this worker is the one that claimed the domain
        claimed_by = domain_status.get("procesado_por", {}).get("worker_id")
        if claimed_by and claimed_by != worker_id:
            logger.warning(f"⚠️ Worker {worker_id}: Dominio {dominio} fue reclamado por {claimed_by}")
            return None  # Skip processing - let the claiming worker handle it
        
        # Process the domain
        procesar_dominio(dominio, titular, identificacion)
        
        # Mark as processed
        col_pendientes.update_one(
            {"dominio": dominio},
            {"$set": {
                "procesado_por.main": True,
                "procesado_por.completed_at": datetime.now(timezone.utc),
                "procesado_por.processing_time": (datetime.now(timezone.utc) - start_time).total_seconds()
            }}
        )
        
        # Update worker statistics
        col_stats.update_one(
            {"_id": worker_id},
            {"$inc": {
                "dominios_procesados": 1,
                "tiempo_total_segundos": (datetime.now(timezone.utc) - start_time).total_seconds()
            }}
        )
        
        logger.warning(f"✅ Worker {worker_id} completó dominio: {dominio} en {(datetime.now(timezone.utc) - start_time).total_seconds():.2f}s")
        
    except Exception as e:
        logger.error(f"❌ Worker {worker_id} error en {dominio}: {str(e)}")
        
        # Update error statistics
        if 'col_stats' in locals():
            col_stats.update_one(
                {"_id": worker_id},
                {"$inc": {"dominios_error": 1}}
            )
    finally:
        if 'client' in locals():
            client.close()


def start_distributor():
    """Función para iniciar el distribuidor como un proceso independiente"""
    logger.warning("Iniciando distribuidor de tareas como proceso independiente")
    distribuir_dominios()
    
    # Esta función nunca debería terminar
    while True:
        time.sleep(300)

