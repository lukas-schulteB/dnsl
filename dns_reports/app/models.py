from pymongo import MongoClient
from datetime import datetime, timedelta
from bson import ObjectId
import ssl
import socket
import OpenSSL
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_mongo_client():
    """Obtener conexión a MongoDB (centralizada)"""
    connection_string = os.getenv("MONGO_CONNECTION_STRING")
    return MongoClient(connection_string)

def get_col_actual():
    """Conexión a la colección de dominios actuales (solo lectura)"""
    client = get_mongo_client()
    db = client.dominios_db
    return db.dominios_actuales

def get_col_historico():
    """Conexión a la colección histórica (solo lectura)"""
    client = get_mongo_client()
    db = client.dominios_db
    return db.dominios_historico

def sanitize_mongo_doc(doc):
    """Limpia documentos MongoDB para serialización JSON"""
    if doc is None:
        return None
        
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            # Convertir ObjectId a string
            if isinstance(value, ObjectId):
                result[key] = str(value)
            # Manejar diccionarios anidados
            elif isinstance(value, dict):
                result[key] = sanitize_mongo_doc(value)
            # Manejar listas anidadas
            elif isinstance(value, list):
                result[key] = [sanitize_mongo_doc(item) for item in value]
            else:
                result[key] = value
        return result
    # Para listas
    elif isinstance(doc, list):
        return [sanitize_mongo_doc(item) for item in doc]
    # Para valores individuales
    elif isinstance(doc, ObjectId):
        return str(doc)
    return doc

def get_certificate_info(domain):
    """Obtiene información del certificado SSL directamente"""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_ASN1, cert)
                
                fingerprint = x509.digest('sha256').decode('ascii')
                subject = dict(x509.get_subject().get_components())
                issuer = dict(x509.get_issuer().get_components())
                
                return {
                    "fingerprint": fingerprint,
                    "subject": {k.decode('utf-8'): v.decode('utf-8') for k, v in subject.items()},
                    "issuer": {k.decode('utf-8'): v.decode('utf-8') for k, v in issuer.items()},
                    "valid_from": datetime.strptime(x509.get_notBefore().decode('ascii'), '%Y%m%d%H%M%SZ'),
                    "valid_to": datetime.strptime(x509.get_notAfter().decode('ascii'), '%Y%m%d%H%M%SZ'),
                    "serial_number": x509.get_serial_number(),
                    "version": x509.get_version(),
                    "has_expired": x509.has_expired()
                }
    except Exception as e:
        return {"error": str(e)}

def get_specialized_data(dominio):
    """Obtener datos de herramientas especializadas para un dominio"""
    client = get_mongo_client()
    db = client.dominios_db
    
    certgraph_data = sanitize_mongo_doc(db.dominios_certgraph.find_one({"dominio": dominio}))
    
    # Si no hay certificados en certgraph, intentar obtener directamente
    if certgraph_data and (not certgraph_data.get('certificados') or len(certgraph_data.get('certificados', [])) == 0):
        try:
            # Verificar SSL directamente
            direct_cert = get_certificate_info(dominio)
            if direct_cert and 'error' not in direct_cert:
                if 'certificados' not in certgraph_data:
                    certgraph_data['certificados'] = []
                certgraph_data['certificados'].append(direct_cert)
                certgraph_data['direct_verification'] = True  # Marcar que fue verificación directa
        except Exception as e:
            pass  # Continuar si falla la verificación directa
    
    specialized_data = {
        "certgraph": certgraph_data,
        "lynx": sanitize_mongo_doc(db.dominios_lynx.find_one({"dominio": dominio})),
        "crosslinked": sanitize_mongo_doc(db.dominios_crosslinked.find_one({"dominio": dominio}))
    }
    
    return specialized_data

def get_historical_trend(dominio, days=30):
    """Obtener tendencia histórica de un dominio"""
    col = get_col_historico()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    pipeline = [
        {"$match": {
            "dominio": dominio,
            "fecha_consulta": {"$gte": start_date, "$lte": end_date}
        }},
        {"$project": {
            "fecha": "$fecha_consulta",
            "registros_dns": {"$size": {"$objectToArray": "$dns"}},
            "tiene_ssl": {"$cond": [{"$ifNull": ["$ssl", False]}, True, False]},
            "subdominios": {"$size": {"$ifNull": ["$subdominios", []]}},
        }},
        {"$sort": {"fecha": 1}}
    ]
    
    return list(col.aggregate(pipeline))

def search_domains(query, limit=20):
    """Búsqueda avanzada de dominios"""
    col = get_col_actual()
    results = col.find({
        "$or": [
            {"dominio": {"$regex": query, "$options": "i"}},
            {"titular": {"$regex": query, "$options": "i"}},
            {"empresa.denominacion": {"$regex": query, "$options": "i"}},
            {"empresa.nif": {"$regex": query, "$options": "i"}}
        ]
    }).limit(limit)
    return [sanitize_mongo_doc(doc) for doc in results]

def get_company_domains(nif=None, denominacion=None):
    """Obtener todos los dominios asociados a una empresa"""
    col = get_col_actual()
    query = {}
    
    if nif:
        query["empresa.nif"] = nif
    elif denominacion:
        query["empresa.denominacion"] = {"$regex": denominacion, "$options": "i"}
    else:
        return []
    
    results = col.find(query).sort("dominio", 1)
    return [sanitize_mongo_doc(doc) for doc in results]

def get_processing_status(dominio):
    """Obtener estado de procesamiento de un dominio"""
    client = get_mongo_client()
    db = client.dominios_db
    return db.procesamiento_estado.find_one({"dominio": dominio})