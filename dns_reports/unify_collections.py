# Unificar colecciones de MongoDB para agregar datos de empresa a dominios actuales
from pymongo import MongoClient
import json
from datetime import datetime
from bson import ObjectId
from app.models import get_mongo_client  # Importa tu función existente

# Encoder personalizado para imprimir en la consola
class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super(MongoJSONEncoder, self).default(obj)

def unify_collections():
    # Usar tu propia función de conexión
    client = get_mongo_client()
    db = client.dominios_db
    
    # Obtener colecciones
    dominios_actuales = db.dominios_actuales
    dominios_empresa = db.dominios_empresa
    
    # Crear copia de seguridad antes de modificar
    print("Creando copia de seguridad de la colección original...")
    backup_collection = db.dominios_actuales_backup
    if backup_collection.count_documents({}) == 0:
        # Solo hacemos backup si no existe
        for doc in dominios_actuales.find():
            backup_collection.insert_one(doc)
        print(f"Backup creado con {backup_collection.count_documents({})} documentos")
    else:
        print(f"Ya existe un backup con {backup_collection.count_documents({})} documentos")
    
    # Contador para estadísticas
    total_docs = dominios_empresa.count_documents({})
    updated_docs = 0
    missing_docs = 0
    already_has_data = 0
    
    print(f"Procesando {total_docs} documentos de empresas...")
    
    # Iterar sobre todos los registros de dominios_empresa
    for empresa_doc in dominios_empresa.find():
        domain = empresa_doc.get('dominio')
        if not domain:
            print(f"Error: Documento sin dominio: {empresa_doc.get('_id')}")
            continue
        
        # Buscar el documento correspondiente en dominios_actuales
        actual_doc = dominios_actuales.find_one({"dominio": domain})
        if not actual_doc:
            print(f"No se encontró el dominio {domain} en dominios_actuales")
            missing_docs += 1
            continue
        
        # Verificar si ya tiene datos de empresa
        if 'empresa' in actual_doc and isinstance(actual_doc['empresa'], dict) and actual_doc['empresa']:
            print(f"El dominio {domain} ya tiene datos de empresa")
            already_has_data += 1
            continue
        
        # Extraer datos de empresa
        if 'empresa' in empresa_doc and isinstance(empresa_doc['empresa'], dict):
            empresa_data = empresa_doc['empresa']
        else:
            # Si el documento no tiene una estructura normal, usar todo el documento
            # excepto _id y dominio que ya están en el documento principal
            empresa_data = {k: v for k, v in empresa_doc.items() if k not in ['_id', 'dominio']}
        
        # Actualizar el documento en dominios_actuales
        result = dominios_actuales.update_one(
            {"_id": actual_doc["_id"]},
            {"$set": {"empresa": empresa_data}}
        )
        
        if result.modified_count > 0:
            print(f"Actualizado dominio: {domain}")
            updated_docs += 1
        else:
            print(f"No se pudo actualizar el dominio: {domain}")
    
    # Resumen de la operación
    print("\n--- RESUMEN DE LA OPERACIÓN ---")
    print(f"Total documentos procesados: {total_docs}")
    print(f"Documentos actualizados: {updated_docs}")
    print(f"Documentos no encontrados: {missing_docs}")
    print(f"Documentos que ya tenían datos: {already_has_data}")
    
    # Verificar resultados
    with_empresa = dominios_actuales.count_documents({"empresa": {"$exists": True}})
    print(f"Documentos con campo empresa ahora: {with_empresa}")

if __name__ == "__main__":
    unify_collections()
    print("Proceso de unificación completado.")