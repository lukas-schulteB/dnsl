import sys
import re
from tasks import procesar_dominio

def limpiar_identificacion(identificacion):
    return re.sub(r'[-\s]', '', identificacion)

def enviar_tareas_desde_archivo(path):
    with open(path, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            if linea.startswith('"NOMBRE_DOMINIO"'):
                continue
            partes = linea.strip('"').split('"|"')
            if len(partes) == 3:
                dominio, titular, identificacion = partes
                identificacion_limpia = limpiar_identificacion(identificacion)
                procesar_dominio.delay(dominio, titular, identificacion_limpia)

if __name__ == "__main__":
    archivo = sys.argv[1] if len(sys.argv) > 1 else "RISP_OTROS.csv"
    enviar_tareas_desde_archivo(archivo)
