# Sistema de Análisis de Dominios

Es una plataforma distribuida para el descubrimiento y el enriquecimiento masivo de nombres de dominio. Combina Celery para el procesamiento paralelo y MongoDB para el almacenamiento de resultados.

---

## Tabla de Contenido

1. [Arquitectura](#arquitectura)  
2. [Servicios](#servicios)  
3. [Añadir una nueva herramienta](#añadir-una-nueva-herramienta)  
   1. [📂 1. Estructura de carpetas](#-1-estructura-de-carpetas)  
   2. [🔧 2. Implementación del worker (`tasks.py`)](#-2-implementación-del-worker-taskspy)  
   3. [🗄️ 3. Configuración de MongoDB (`init.js`)](#️-3-configuración-de-mongodb-initjs)  
   4. [📥 4. Actualizar el importador (`import_domains.py`)](#-4-actualizar-el-importador-import_domainspy)  
   5. [🐋 5. Crear archivo Docker Compose](#-5-crear-archivo-docker-compose)  
4. [Despliegue](#despliegue)  
5. [Troubleshooting común](#troubleshooting-común)  
6. [CI/CD con GitHub Actions](#cicd-con-github-actions-self-hosted-runner)
7. [Reseteo de dominios procesados](#Reseteo-de-dominios-procesados)
---

## Arquitectura

```text
┌───────────────┐          ┌──────────────┐
│  RabbitMQ     │◀───────▶│    Celery    │
└───────────────┘          └──────────────┘
        ▲                          ▲
        │                          │
┌───────┴───────┐          ┌───────┴──────┐
│    MongoDB    │◀───────▶│   Servicios  │
└───────────────┘          └──────────────┘

```

## Servicios

|       Servicio        |                      Descripción                     |
|-----------------------|------------------------------------------------------|
| **Main Service**      | Resolución DNS, búsqueda de subdominios              |
| **Lynx Service**      | Rastreo y análisis de enlaces web usando lynx        |  
| **Certgraph Service** | Recolección y análisis de certificados SSL/TLS       |  
| **OpenData Service**  | Enriquecimiento con datos de registradores españoles |
| **Importer**          | Importación masiva de dominios desde CSV a MongoDB   | 

# Añadir una nueva herramienta

Esta sección explica paso a paso cómo integrar una nueva herramienta de análisis al sistema distribuido de dominios.

## 📂 1. Estructura de carpetas

Crea la carpeta de tu nueva herramienta dentro de `collectors/` con la siguiente estructura:

```
dns/
└── collectors/
    └── nueva_herramienta_service/
        ├── Dockerfile
        ├── requirements.txt
        ├── tasks.py
        └── … (otros archivos si son necesarios)
```

### Dockerfile ejemplo:
```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Instalar dependencias del sistema si es necesario
# RUN apt-get update && apt-get install -y \
#     tu-herramienta-externa \
#     && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# El comando ejecuta directamente el worker_loop, NO celery worker
CMD ["python", "tasks.py"]

### requirements.txt ejemplo:
```txt
celery==5.2.0
pymongo==4.0.0
requests==2.28.0
# ... otras dependencias específicas de tu herramienta
```

---

## 🔧 2. Implementación del worker (`tasks.py`)

### Configuración base

```python
import os
import time
import logging
from datetime import datetime, timezone
from pymongo import MongoClient
from celery import Celery

# ▸ Configuración de variables de entorno
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL")
MONGO_URI = os.environ.get("MONGO_URI")

# ▸ Configuración de logging 
logging.basicConfig(level=logging.ERROR, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('nueva_herramienta_worker')


# ▸ Configuración de Celery (siguiendo tu patrón)
app = Celery('nueva_herramienta_tasks', broker=CELERY_BROKER_URL)
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.broker_connection_retry_on_startup = True
app.conf.task_routes = {
    'tasks.procesar_dominio': {'queue': 'nueva_herramienta_tasks'}
}

# ▸ Helper para MongoDB
def get_mongo_db():
    """Obtener cliente y base de datos MongoDB"""
    try:
        client = MongoClient(MONGO_URI)
        db = client["dominios_db"]
        return client, db
    except Exception as e:
        logger.error(f"Error conectando a MongoDB: {e}")
        raise

def ejecutar_nueva_herramienta(dominio: str):
    """
    Aquí implementas la lógica específica de tu nueva herramienta
    
    Ejemplos de lo que podrías hacer:
    - Análisis de puertos abiertos
    - Escaneo de vulnerabilidades
    - Análisis de tecnologías web
    - Recolección de metadatos
    - etc.
    """
    try:
        # Ejemplo: análisis básico
        resultados = {
            "timestamp": datetime.now(timezone.utc),
            "herramienta_version": "1.0.0",
            "datos": {}
        }
        
        # AQUÍ VA TU LÓGICA ESPECÍFICA
        # Por ejemplo:
        # import subprocess
        # resultado_comando = subprocess.run(['tu_herramienta', dominio], 
        #                                   capture_output=True, text=True)
        # resultados["datos"] = {"output": resultado_comando.stdout}
        
        # Simulación de procesamiento
        logger.info(f"Procesando {dominio} con nueva_herramienta")
        time.sleep(2)  # Simula tiempo de procesamiento
        
        resultados["datos"] = {
            "ejemplo_campo": f"Análisis de {dominio} completado",
            "estado": "exitoso"
        }
        
        return resultados
        
    except Exception as e:
        logger.error(f"Error ejecutando nueva_herramienta en {dominio}: {e}")
        return {"error": str(e), "timestamp": datetime.now(timezone.utc)}

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def procesar_dominio(self, dominio: str):
    """
    Tarea de Celery para procesar un dominio individual
    """
    try:
        client, db = get_mongo_db()
        
        # Colecciones
        col_resultados = db["dominios_nueva_herramienta"]
        col_pendientes = db["dominios_pendientes"]
        
        logger.info(f"🔍 Procesando dominio: {dominio}")
        
        # 1. Ejecutar la herramienta
        resultado_herramienta = ejecutar_nueva_herramienta(dominio)
        
        # 2. Preparar documento de resultados
        documento_resultado = {
            "dominio": dominio,
            "fecha_procesamiento": datetime.now(timezone.utc),
            "resultado": resultado_herramienta,
            "worker_id": f"nueva_herramienta_{os.getpid()}",
            "version": "1.0.0"
        }
        
        # 3. Guardar en colección de resultados
        col_resultados.update_one(
            {"dominio": dominio},
            {"$set": documento_resultado},
            upsert=True
        )
        
        # 4. Marcar como completado en pendientes
        col_pendientes.update_one(
            {"dominio": dominio},
            {
                "$set": {
                    "procesado_por.nueva_herramienta": True,
                    "procesado_por.nueva_herramienta_fecha": datetime.now(timezone.utc)
                },
                "$unset": {
                    "procesado_por.nueva_herramienta_iniciado": ""
                }
            }
        )
        
        logger.info(f"✅ Dominio completado: {dominio}")
        client.close()
        return {"dominio": dominio, "estado": "completado"}
        
    except Exception as exc:
        logger.exception(f"❌ Error procesando {dominio}: {exc}")
        
        # Reintentar si no se han agotado los intentos
        if self.request.retries < self.max_retries:
            logger.info(f"🔄 Reintentando {dominio} (intento {self.request.retries + 1})")
            raise self.retry(exc=exc)
        else:
            # Marcar como error en la base de datos
            try:
                client, db = get_mongo_db()
                db["dominios_pendientes"].update_one(
                    {"dominio": dominio},
                    {
                        "$set": {
                            "procesado_por.nueva_herramienta": True,  # Marcar como "procesado" para evitar loops
                            "procesado_por.nueva_herramienta_error": str(exc),
                            "procesado_por.nueva_herramienta_fecha": datetime.now(timezone.utc)
                        },
                        "$unset": {
                            "procesado_por.nueva_herramienta_iniciado": ""
                        }
                    }
                )
                client.close()
            except Exception as db_error:
                logger.error(f"Error guardando error en BD: {db_error}")
            
            return {"dominio": dominio, "estado": "error", "error": str(exc)}

def worker_loop():
    """
    Bucle principal del worker - ESTE ES EL PATRÓN QUE USAS
    Se ejecuta directamente en el contenedor, NO como tarea de Celery
    """
    logger.info("🚀 Iniciando worker loop de nueva_herramienta")
    
    while True:
        try:
            client, db = get_mongo_db()
            col_pendientes = db["dominios_pendientes"]
            
            # Buscar un dominio pendiente y marcarlo como "iniciado"
            doc = col_pendientes.find_one_and_update(
                {
                    "procesado_por.nueva_herramienta": False,
                    "procesado_por.nueva_herramienta_iniciado": {"$exists": False}
                },
                {
                    "$set": {
                        "procesado_por.nueva_herramienta_iniciado": datetime.now(timezone.utc)
                    }
                },
                sort=[("_id", 1)]  # FIFO: primer llegado, primer servido
            )
            
            if doc:
                dominio = doc["dominio"]
                logger.info(f"📤 Enviando a cola: {dominio}")
                
                # Enviar a la cola de Celery para procesamiento
                procesar_dominio.delay(dominio)
                
                # Pequeña pausa para evitar saturar la cola
                time.sleep(1)
                
            else:
                # No hay dominios pendientes
                logger.info("😴 No hay dominios pendientes para nueva_herramienta, esperando...")
                time.sleep(30)  # Esperar 30 segundos antes de volver a buscar
            
            client.close()
            
        except Exception as e:
            logger.exception(f"💥 Error en worker_loop: {e}")
            time.sleep(10)  # Esperar antes de reintentar

# Script principal (se ejecuta cuando el contenedor inicia)
if __name__ == "__main__":
    # Este es el punto de entrada principal
    logger.info("🎯 Iniciando nueva_herramienta service")
    worker_loop()
```

---

## 🗄️ 3. Configuración de MongoDB (`init.js`)

Añade las configuraciones necesarias para tu nueva herramienta en el archivo de inicialización:

### Crear colección de resultados

```javascript
// Colección para almacenar resultados de la nueva herramienta
if (!db.getCollectionNames().includes("dominios_nueva_herramienta")) {
  print("Creando colección: dominios_nueva_herramienta");
  db.createCollection("dominios_nueva_herramienta");
  
  // Índice único por dominio
  db.dominios_nueva_herramienta.createIndex(
    { dominio: 1 },
    { unique: true }
  );
  
  // Índice por fecha para consultas temporales
  db.dominios_nueva_herramienta.createIndex(
    { fecha_analisis: -1 }
  );
}
```

### Crear índices en cola de pendientes

```javascript
// Índice para optimizar búsquedas de dominios pendientes
if (!db.dominios_pendientes.getIndexes().some(i => i.name === "procesado_nueva_herramienta_1")) {
  print("Creando índice para nueva_herramienta en dominios_pendientes");
  db.dominios_pendientes.createIndex(
    { 
      "procesado_por.nueva_herramienta": 1,
      "procesado_por.nueva_herramienta_iniciado": 1
    },
    { name: "procesado_nueva_herramienta_1" }
  );
}
```

---

## 📥 4. Actualizar el importador (`import_domains.py`)

Modifica la función que importa dominios para incluir tu nueva herramienta:

### Actualizar estructura de procesamiento

```python
# Dentro de la función import_domain()
"procesado_por": {
    "main": False,
    "lynx": False,
    "certgraph": False,
    "crosslinked": False,
    "opendata": False,
    "nueva_herramienta": False,  # ← Nueva herramienta añadida
}
```

### Verificar la implementación

```python
def import_domain(domain_name):
    """Importa un dominio al sistema"""
    domain_doc = {
        "dominio": domain_name,
        "fecha_importacion": datetime.now(timezone.utc),
        "procesado_por": {
            "main": False,
            "lynx": False,
            "certgraph": False,
            "crosslinked": False,
            "opendata": False,
            "nueva_herramienta": False,  # ← Añadido aquí
        }
    }
    
    # Insertar en MongoDB
    result = collection.update_one(
        {"dominio": domain_name},
        {"$set": domain_doc},
        upsert=True
    )
    
    return result
```

---

## 🐋 5. Crear archivo Docker Compose

Crea `docker-compose.nueva-herramienta.yml`:

```yaml
services:
  nueva_herramienta_worker:
    build:
      context: ./collectors/nueva_herramienta_service
      dockerfile: Dockerfile
    container_name: nueva_herramienta_worker
    environment:
      - CELERY_BROKER_URL=amqp://${RABBITMQ_USER}:${RABBITMQ_PASSWORD}@${RABBITMQ_HOST}:${RABBITMQ_PORT}/${RABBITMQ_VHOST}
      - MONGO_URI=mongodb://${MONGO_USER}:${MONGO_PASSWORD}@${MONGO_HOST}:${MONGO_PORT}/
      - PYTHONPATH=/app
    command: celery -A tasks worker --loglevel=info -Q nueva_herramienta_tasks --concurrency=2
    restart: unless-stopped
    networks:
      - dominios_net
    depends_on:
      - mongodb
      - rabbitmq

networks:
  dominios_net:
    external: true
```

## Despliegue

### Infraestructura base:
```bash
docker-compose -f docker-compose.infra.yml up -d
```

### Servicios individuales:
```bash
# Importer (ejecutar una vez y como primera herramienta)
docker-compose -f docker-compose.importer.yml up importer

# Main service (DNS + subdominios)
docker-compose -f docker-compose.celery-worker.yml up -d

# Lynx service
docker-compose -f docker-compose.lynx.yml up -d

# Certgraph service  
docker-compose -f docker-compose.certgraph.yml up -d

# OpenData service
docker-compose -f docker-compose.opendata.yml up -d

```

### Stack completo:
```bash
docker-compose \
  -f docker-compose.infra.yml \
  -f docker-compose.celery-worker.yml \
  -f docker-compose.lynx.yml \
  -f docker-compose.certgraph.yml \
  -f docker-compose.opendata.yml \
  up -d
```
## Troubleshooting común

### Archivos de datos externos (no incluidos en el repositorio)

Debido al tamaño, algunos archivos de datos no están incluidos en GitHub y deben descargarse por separado:

```bash
# Archivos requeridos para el servicio OpenData:
# - risp_otros: Datos adicionales de registradores españoles
# - ip_rangos: Rangos de IPs para análisis de geolocalización

# Descargar y colocar en la carpeta correspondiente:
# collectors/opendata_service/data/risp_otros
# collectors/opendata_service/data/ip_rangos
```

> **Nota**: Estos archivos contienen datos de referencia externos y deben obtenerse de las fuentes oficiales correspondientes.

### MongoDB: Error de autenticación
```bash
# Verificar conexión
docker run --rm --network dominios_net mongo:7.0 mongosh \
  "mongodb://usuario:contraseña@mongodb:27017/admin" \
  --eval "db.runCommand('ping')"
```

### Verificar documentos atascados:
```bash
docker run --rm --network dominios_net mongo:7.0 mongosh \
  "mongodb://usuario:contraseña@mongodb:27017/admin" \
  --eval 'use dominios_db; 
  db.dominios_pendientes.find({
    "procesado_por.<herramienta>": false, 
    "procesado_por.<herramienta>_iniciado": {$exists: true}
  }).count()'
```

## CI/CD con GitHub Actions (Self-hosted Runner)

El proyecto utiliza un **self-hosted runner** para mayor seguridad y rendimiento. Cada servicio tiene su propio workflow de deployment automático.

### Configuración actual:

- **Runner**: `self-hosted` 
- **Autenticación MongoDB**: Conecta a `/admin` luego `use dominios_db`
- **Red Docker**: `dominios_net` para comunicación entre contenedores
- **Gestión de código**: `actions/checkout@v4` 

###  Limpiar cache Python si da error el workflow
sudo rm -rf /opt/actions-runner/_work/keadns/keadns/dns/collectors/*/__pycache__/


Crea `.github/workflows/deploy-nueva-herramienta.yml`:

```yaml
name: Deploy Nueva Herramienta Service

on:
  push:
    branches: [main]
    paths:
      - 'dns/collectors/nueva_herramienta_service/**'
      - 'dns/docker-compose.nueva-herramienta.yml'
  workflow_dispatch:

jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      
      - name: Deploy Nueva Herramienta Service
        run: |
          cd dns
          
          # 1. Detener servicio actual (si existe)
          docker-compose -f docker-compose.nueva-herramienta.yml down nueva_herramienta_worker || true
          
          # 2. ⚠️ IMPORTANTE: Re-ejecutar importer para actualizar estructura
          echo "🔄 Actualizando estructura de dominios..."
          docker-compose -f docker-compose.importer.yml up --build importer
          
          # 3. Reset de documentos atascados
          docker run --rm --network dominios_net mongo:7.0 mongosh \
            "mongodb://${MONGO_USER}:${MONGO_PASSWORD}@mongodb:27017/admin" \
            --eval 'use dominios_db; db.dominios_pendientes.updateMany(
              {"procesado_por.nueva_herramienta": false, "procesado_por.nueva_herramienta_iniciado": {$exists: true}}, 
              {$unset: {"procesado_por.nueva_herramienta_iniciado": ""}}
            )'
          
          # 4. Reconstruir y reiniciar
          docker-compose -f docker-compose.nueva-herramienta.yml build nueva_herramienta_worker
          docker-compose -f docker-compose.nueva-herramienta.yml up -d nueva_herramienta_worker
          
          # 5. Verificar despliegue
          echo "✅ Verificando despliegue..."
          sleep 10
          docker-compose -f docker-compose.nueva-herramienta.yml ps nueva_herramienta_worker
        env:
          MONGO_USER: ${{ secrets.MONGO_USER }}
          MONGO_PASSWORD: ${{ secrets.MONGO_PASSWORD }}
          MONGO_HOST: ${{ secrets.MONGO_HOST }}
          MONGO_PORT: ${{ secrets.MONGO_PORT }}
          RABBITMQ_USER: ${{ secrets.RABBITMQ_USER }}
          RABBITMQ_PASSWORD: ${{ secrets.RABBITMQ_PASSWORD }}
          RABBITMQ_HOST: ${{ secrets.RABBITMQ_HOST }}
          RABBITMQ_PORT: ${{ secrets.RABBITMQ_PORT }}
          RABBITMQ_VHOST: ${{ secrets.RABBITMQ_VHOST }}
```

## Reseteo de dominios procesados

### Resetear todos los servicios a estado inicial
```bash
# Resetear TODOS los servicios a false y limpiar campos "_iniciado"
docker run --rm --network dominios_net mongo:7.0 mongosh \
  "mongodb://usuario:contraseña@mongodb:27017/admin" \
  --eval 'use dominios_db; 
  db.dominios_pendientes.updateMany(
    {},
    {
      $set: {
        "procesado_por.main": false,
        "procesado_por.lynx": false,
        "procesado_por.certgraph": false,
        "procesado_por.crosslinked": false,
        "procesado_por.opendata": false
      },
      $unset: {
        "procesado_por.main_iniciado": "",
        "procesado_por.lynx_iniciado": "",
        "procesado_por.certgraph_iniciado": "",
        "procesado_por.crosslinked_iniciado": "",
        "procesado_por.opendata_iniciado": "",
        "procesado_por.main_fecha": "",
        "procesado_por.lynx_fecha": "",
        "procesado_por.certgraph_fecha": "",
        "procesado_por.crosslinked_fecha": "",
        "procesado_por.opendata_fecha": "",
        "procesado_por.main_error": "",
        "procesado_por.lynx_error": "",
        "procesado_por.certgraph_error": "",
        "procesado_por.crosslinked_error": "",
        "procesado_por.opendata_error": ""
      }
    }
  )'
```

### Resetear un servicio específico
```bash
# Ejemplo: resetear solo el servicio lynx
docker run --rm --network dominios_net mongo:7.0 mongosh \
  "mongodb://usuario:contraseña@mongodb:27017/admin" \
  --eval 'use dominios_db; 
  db.dominios_pendientes.updateMany(
    {},
    {
      $set: {
        "procesado_por.lynx": false
      },
      $unset: {
        "procesado_por.lynx_iniciado": "",
        "procesado_por.lynx_fecha": "",
        "procesado_por.lynx_error": ""
      }
    }
  )'
```