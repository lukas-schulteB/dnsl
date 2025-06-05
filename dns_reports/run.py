from flask import Flask, render_template, request, jsonify, send_file, flash, redirect
from datetime import datetime
import os
import tempfile
import pdfkit
import json
from bson import ObjectId
from app.models import (get_col_actual, get_col_historico, 
                      get_specialized_data, get_historical_trend,
                      search_domains, get_company_domains, get_mongo_client)

# Clase para manejar ObjectId en JSON
class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super(MongoJSONEncoder, self).default(obj)

app = Flask(__name__, 
    template_folder='app/templates',
    static_folder='static')

app.secret_key = "your_secret_key_here"  # Add this after app definition

# Configura el encoder personalizado
app.json_encoder = MongoJSONEncoder
app.jinja_env.filters['tojson'] = lambda obj, **kwargs: json.dumps(obj, cls=MongoJSONEncoder, **kwargs)

@app.route('/')
def index():
    return render_template('search.html')

@app.route('/search')
def search():
    try:
        query = request.args.get('q', '')
        if not query:
            return render_template('search.html')
        
        # Search in domains and companies
        domains = search_domains(query, limit=20)  # Usamos la función de models.py
        
        return render_template('search_results.html', results=domains, query=query)
    except Exception as e:
        app.logger.error(f"Error en búsqueda: {str(e)}")
        return render_template('error.html', message=f"Error al realizar la búsqueda: {str(e)}")




@app.route('/report/<domain>')
def domain_report(domain):
    # Get domain data
    col_actual = get_col_actual()
    domain_data = col_actual.find_one({"dominio": domain})
    
    if not domain_data:
        return render_template('error.html', message="Domain not found")
    
    # ✅ VERIFICAR QUE LA ESTRUCTURA DE EMPRESA SEA CORRECTA
    if 'empresa' in domain_data:
        empresa_data = domain_data['empresa']
        # Asegurar que tiene estructura mínima
        if not isinstance(empresa_data, dict):
            domain_data['empresa'] = {}
        elif 'domicilio' not in empresa_data:
            domain_data['empresa']['domicilio'] = {}
    else:
        domain_data['empresa'] = {'domicilio': {}}
    
    # Enriquecer con datos especializados
    specialized = get_specialized_data(domain)
    
    return render_template('report.html',
                          report=domain_data,
                          specialized=specialized,
                          domain=domain)

@app.route('/company/<nif>')
def company_profile(nif):
    col_actual = get_col_actual()
    
    # Buscar por ambos campos: empresa.nif e identificacion
    domains_query = {
        "$or": [
            {"empresa.nif": nif},
            {"identificacion": nif}
        ]
    }
    
    domains = list(col_actual.find(domains_query))
    
    if not domains:
        return render_template('error.html', message=f"No se encontró información para la empresa con NIF: {nif}")
    
    # Buscar el primer documento que tenga información completa de empresa
    company_info = {}
    for domain_doc in domains:
        if "empresa" in domain_doc and domain_doc["empresa"]:
            company_info = domain_doc["empresa"]
            break
    
    # Si no hay información de empresa, crear estructura básica
    if not company_info:
        company_info = {
            "nif": nif,
            "denominacion": f"Empresa {nif}",
            "domicilio": {"provincia": "No disponible"},
            "actividad_economica": {"cnae_descripcion": "No disponible"}
        }
    
    # Procesar lista de dominios
    domain_list = []
    for doc in domains:
        domain_name = doc.get("dominio", "")
        if domain_name:
            domain_list.append({
                "dominio": domain_name,
                "fecha_consulta": doc.get("fecha_consulta", ""),
                # Añadir más campos si están disponibles
                "dns": doc.get("dns", {}),
                "subdominios": doc.get("subdominios", [])
            })
    
    return render_template('company.html', 
                          company=company_info, 
                          domains=domain_list,
                          nif=nif)

@app.route('/export/pdf/<domain>')
def export_pdf(domain):
    # Generate PDF report
    domain_data = get_domain_complete_data(domain)  # ← SOLO ESTA LÍNEA CAMBIA
    
    if not domain_data:
        return jsonify({"error": "Domain not found"})
    
    rendered_html = render_template('pdf_report.html', report=domain_data)
    
    # Try multiple common installation paths for wkhtmltopdf
    possible_paths = [
        'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe',
        'C:\\Program Files (x86)\\wkhtmltopdf\\bin\\wkhtmltopdf.exe',
        'C:\\wkhtmltopdf\\bin\\wkhtmltopdf.exe',
        'C:\\Program Files\\wkhtmltopdf\\wkhtmltopdf.exe',  # Try this path too
        'wkhtmltopdf'  # If it's in the system PATH
    ]
    
    # Debug: Print all checked paths
    print("Checking wkhtmltopdf paths:")
    for path in possible_paths:
        exists = os.path.exists(path)
        print(f"  - {path}: {'Found' if exists else 'Not found'}")
    
    config = None
    for path in possible_paths:
        try:
            if os.path.exists(path):
                print(f"Found wkhtmltopdf at: {path}")
                config = pdfkit.configuration(wkhtmltopdf=path)
                break
        except Exception as e:
            print(f"Error with path {path}: {str(e)}")
            continue
            
    if config is None:
        flash("Error: No se encontró wkhtmltopdf. Por favor instálalo desde https://wkhtmltopdf.org/downloads.html", "danger")
        return redirect(f"/report/{domain}")
    
    try:
        # Generate PDF from HTML with configuration
        temp_dir = tempfile.gettempdir()
        pdf_path = os.path.join(temp_dir, f"{domain}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf")
        print(f"Generating PDF to path: {pdf_path}")
        
        # Add options for better PDF generation
        options = {
            'encoding': 'UTF-8',
            'no-outline': None,
            'quiet': ''
        }
        
        pdfkit.from_string(rendered_html, pdf_path, configuration=config, options=options)
        print("PDF generated successfully")
        
        return send_file(pdf_path, as_attachment=True, download_name=f"{domain}_report.pdf")
    except Exception as e:
        print(f"PDF generation error: {str(e)}")
        flash(f"Error generando PDF: {str(e)}. Asegúrate de que wkhtmltopdf esté instalado correctamente.", "danger")
        return redirect(f"/report/{domain}")

@app.route('/pdf/<domain>')
def pdf_export_shortcut(domain):
    """Alternative shorter route for PDF exports"""
    return export_pdf(domain)

@app.route('/verify/ssl/<domain>')
def verify_ssl(domain):
    try:
        from app.models import get_certificate_info
        
        # Obtener certificado directamente
        cert_info = get_certificate_info(domain)
        
        if 'error' in cert_info:
            flash(f"Error al verificar certificado: cert_info['error']", "danger")
        else:
            # Guardar en MongoDB para futuros usos
            client = get_mongo_client()
            db = client.dominios_db
            
            # Actualizar o crear registro en certgraph
            certgraph = db.dominios_certgraph.find_one({"dominio": domain})
            if certgraph:
                db.dominios_certgraph.update_one(
                    {"dominio": domain},
                    {"$set": {"certificados": [cert_info], "fecha_analisis": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}
                )
            else:
                db.dominios_certgraph.insert_one({
                    "dominio": domain,
                    "certificados": [cert_info],
                    "dominios_relacionados": [],
                    "errores": [],
                    "fecha_analisis": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            
            flash("Certificado verificado y actualizado correctamente", "success")
        
        return redirect(f"/report/{domain}")
    except Exception as e:
        flash(f"Error inesperado: {str(e)}", "danger")
        return redirect(f"/report/{domain}")

@app.route('/company-details/<domain>')
def company_details(domain):
    """Mostrar detalles completos de la empresa asociada al dominio"""
    col_actual = get_col_actual()
    company_data = col_actual.find_one({"dominio": domain})
    
    if not company_data or not company_data.get('empresa'):
        flash("No se encontró información de empresa para este dominio", "warning")
        return redirect(f"/report/{domain}")
    
    # Buscar otros dominios de la misma empresa
    nif = company_data.get('empresa', {}).get('nif')
    related_domains = []
    
    if nif:
        related_domains = list(col_actual.find(
            {"empresa.nif": nif, "dominio": {"$ne": domain}},
            {"dominio": 1, "fecha_consulta": 1}
        ))
    
    # Ya no necesitamos pasar 'company' como variable separada
    return render_template('company_details.html', 
                          company_data=company_data,
                          related_domains=related_domains)

@app.route('/dashboard')
def dashboard():
    col_actual = get_col_actual()
    
    try:
        # Estadísticas generales - solo dominios únicos
        total_domains = col_actual.count_documents({})
        
        if total_domains == 0:
            return render_template('dashboard.html', 
                              stats={
                                  'total': 0,
                                  'with_company': 0,
                                  'with_subdomains': 0,
                                  'without_cnae': 0
                              },
                              provincias=None,
                              sectores=None,
                              top_asn=None)
        
        domains_with_company = col_actual.count_documents({"empresa": {"$exists": True}})
        domains_with_subdomains = col_actual.count_documents({
            "subdominios": {"$exists": True, "$ne": []}
        })
        
        # Contar empresas sin CNAE
        without_cnae = col_actual.count_documents({
            "empresa": {"$exists": True},
            "$or": [
                {"empresa.actividad_economica.cnae_descripcion": {"$exists": False}},
                {"empresa.actividad_economica.cnae_descripcion": ""},
                {"empresa.actividad_economica.cnae_descripcion": None}
            ]
        })
        
        # Dominios por provincia
        provincia_pipeline = [
            {"$match": {"empresa.domicilio.provincia": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": "$empresa.domicilio.provincia", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        provincias = list(col_actual.aggregate(provincia_pipeline))
        
        # Top sectores empresariales CON CNAE Y SIN CNAE
        sectores_pipeline = [
            {"$match": {"empresa": {"$exists": True}}},
            {"$addFields": {
                "sector_category": {
                    "$cond": {
                        "if": {
                            "$and": [
                                {"$ne": ["$empresa.actividad_economica.cnae_descripcion", None]},
                                {"$ne": ["$empresa.actividad_economica.cnae_descripcion", ""]},
                                {"$ne": ["$empresa.actividad_economica.cnae_descripcion", ""]}
                            ]
                        },
                        "then": "$empresa.actividad_economica.cnae_descripcion",
                        "else": "Sin CNAE registrado"
                    }
                }
            }},
            {"$group": {
                "_id": "$sector_category", 
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}},
            {"$limit": 8},
            {"$project": {
                "sector_name": {"$substr": ["$_id", 0, 40]},
                "count": 1,
                "is_no_cnae": {"$eq": ["$_id", "Sin CNAE registrado"]}
            }}
        ]
        sectores = list(col_actual.aggregate(sectores_pipeline))
        
        # Top ASN corregido - CONTAR DOMINIOS ÚNICOS, NO REGISTROS DNS
        asn_pipeline = [
            # Paso 1: Desenrollar registros A
            {"$match": {"dns.A": {"$exists": True, "$ne": []}}},
            {"$unwind": "$dns.A"},
            {"$match": {"dns.A.asn": {"$exists": True, "$ne": ""}}},
            
            # Paso 2: Agrupar por dominio y ASN para eliminar duplicados dentro del mismo dominio
            {"$group": {
                "_id": {
                    "dominio": "$dominio",
                    "asn": "$dns.A.asn"
                },
                "asn_desc": {"$first": "$dns.A.asn_desc"},
                "country": {"$first": "$dns.A.country"}
            }},
            
            # Paso 3: Ahora agrupar por ASN contando dominios únicos
            {"$group": {
                "_id": "$_id.asn",
                "count": {"$sum": 1},  # Cuenta dominios únicos
                "asn_desc": {"$first": "$asn_desc"},
                "country": {"$first": "$country"}
            }},
            
            {"$sort": {"count": -1}},
            {"$limit": 6},
            {"$project": {
                "name": {"$ifNull": ["$asn_desc", "$_id"]},
                "asn_number": "$_id", 
                "count": 1,
                "country": 1
            }}
        ]
        top_asn = list(col_actual.aggregate(asn_pipeline))
        
        return render_template('dashboard.html', 
                              stats={
                                  'total': total_domains,
                                  'with_company': domains_with_company,
                                  'with_subdomains': domains_with_subdomains,
                                  'without_cnae': without_cnae
                              },
                              provincias=provincias if provincias else None,
                              sectores=sectores if sectores else None,
                              top_asn=top_asn if top_asn else None)
    
    except Exception as e:
        print(f"Error en dashboard: {e}")
        return render_template('dashboard.html', 
                          stats={
                              'total': 0,
                              'with_company': 0,
                              'with_subdomains': 0,
                              'without_cnae': 0
                          },
                          provincias=None,
                          sectores=None,
                          top_asn=None)

@app.route('/network')
def company_network():
    """Genera datos para visualizar redes de empresas y sus dominios"""
    query = request.args.get('q', '')
    relation_type = request.args.get('relation', 'all')
    
    if not query:
        return render_template('network_search.html')
    
    col_actual = get_col_actual()
    
    processed_nifs = set()
    processed_domains = set()
    nodes = []
    links = []
    company_ids = {}
    domain_ids = {}
    
    # Añadir debug temporal al código
    def expand_network(search_term, current_depth=0):
        if current_depth > 1 or len(nodes) >= 50:
            return
            
        print(f"Buscando: '{search_term}' con relation_type: {relation_type}")
        
        # ✅ Debug: Ver qué estructura tienen los documentos
        sample_docs = list(col_actual.find({"identificacion": {"$exists": True}}).limit(2))
        print("Estructura de documentos de muestra:")
        for doc in sample_docs:
            print(f"- identificacion: {doc.get('identificacion')}")
            print(f"- empresa.nif: {doc.get('empresa', {}).get('nif', 'NO EXISTE')}")
            print(f"- dominio: {doc.get('dominio')}")
            print("---")
        
        # Buscar por identificacion también
        search_query_1 = {
            "identificacion": {"$regex": search_term, "$options": "i"}
        }
        
        search_query_2 = {
            "$or": [
                {"empresa.denominacion": {"$regex": search_term, "$options": "i"}},
                {"empresa.nif": {"$regex": search_term, "$options": "i"}}
            ],
            "empresa": {"$exists": True}
        }
        
        companies_1 = list(col_actual.find(search_query_1, {"dominio": 1, "empresa": 1, "identificacion": 1}))
        companies_2 = list(col_actual.find(search_query_2, {"dominio": 1, "empresa": 1, "identificacion": 1}))
        
        print(f"Encontradas por identificacion: {len(companies_1)}")
        print(f"Encontradas por empresa.nif: {len(companies_2)}")
        
        # Combinar resultados
        companies = companies_1 + companies_2
        
        # Procesar cada empresa y sus dominios
        for doc in companies:
            domain = doc.get("dominio", "")
            
            # ✅ NUEVA LÓGICA: Manejar ambos tipos de estructura
            company_name = "Desconocido"
            company_nif = None
            location = ""
            sector = ""
            
            # Caso 1: Estructura con empresa.nif
            if "empresa" in doc and "nif" in doc["empresa"]:
                company_data = doc["empresa"]
                company_name = company_data.get("denominacion", "Desconocido")
                company_nif = company_data["nif"]
                
                if "domicilio" in company_data and "provincia" in company_data["domicilio"]:
                    location = company_data["domicilio"]["provincia"]
                if "actividad_economica" in company_data and "cnae_descripcion" in company_data["actividad_economica"]:
                    sector = company_data["actividad_economica"]["cnae_descripcion"]
            
            # Caso 2: Estructura solo con identificacion
            elif "identificacion" in doc:
                company_nif = doc["identificacion"]
                # Buscar información adicional de la empresa en el mismo documento o en otra colección
                company_name = company_nif  # Por ahora usar el NIF como nombre
                
                # Intentar obtener más información de la empresa si existe
                if "empresa" in doc:
                    company_data = doc["empresa"]
                    if "denominacion" in company_data:
                        company_name = company_data["denominacion"]
                    if "domicilio" in company_data and "provincia" in company_data["domicilio"]:
                        location = company_data["domicilio"]["provincia"]
                    if "actividad_economica" in company_data and "cnae_descripcion" in company_data["actividad_economica"]:
                        sector = company_data["actividad_economica"]["cnae_descripcion"]
            else:
                # Saltar documentos sin identificación
                continue
            
            if not company_nif:
                continue
            
            print(f"Procesando empresa: {company_name} (NIF: {company_nif}) - Dominio: {domain}")
            
            # Evitar procesar la misma empresa dos veces
            if company_nif in processed_nifs:
                # Solo agregar el enlace del dominio si es nuevo
                if domain and domain not in processed_domains:
                    domain_id = len(nodes)
                    domain_ids[domain] = domain_id
                    nodes.append({
                        "id": domain_id,
                        "name": domain,
                        "type": "domain"
                    })
                    
                    links.append({
                        "source": company_ids[company_nif],
                        "target": domain_id,
                        "value": 1,
                        "type": "owns_domain"
                    })
                    
                    processed_domains.add(domain)
                continue
            
            # Añadir empresa
            company_id = len(nodes)
            company_ids[company_nif] = company_id
            nodes.append({
                "id": company_id,
                "name": company_name,
                "type": "company",
                "nif": company_nif,
                "location": location,
                "sector": sector
            })
            processed_nifs.add(company_nif)
            
            # Añadir dominio si existe
            if domain and domain not in processed_domains:
                domain_id = len(nodes)
                domain_ids[domain] = domain_id
                nodes.append({
                    "id": domain_id,
                    "name": domain,
                    "type": "domain"
                })
                
                processed_domains.add(domain)
                
                # Enlazar dominio con empresa
                links.append({
                    "source": company_id,
                    "target": domain_id,
                    "value": 1,
                    "type": "owns_domain"
                })
            
            # ✅ ACTUALIZAR BÚSQUEDAS RELACIONADAS: Buscar por ambos campos
            if relation_type in ['all', 'domains']:
                # Buscar más dominios de la misma empresa (buscar en ambos campos)
                related_domains_query = {
                    "$or": [
                        {"empresa.nif": company_nif},
                        {"identificacion": company_nif}
                    ],
                    "dominio": {"$ne": domain, "$exists": True}
                }
                
                related_domains = list(col_actual.find(related_domains_query, {"dominio": 1}))
                
                for rel_doc in related_domains:
                    rel_domain = rel_doc.get("dominio")
                    if rel_domain and rel_domain not in processed_domains:
                        rel_domain_id = len(nodes)
                        domain_ids[rel_domain] = rel_domain_id
                        nodes.append({
                            "id": rel_domain_id,
                            "name": rel_domain,
                            "type": "domain"
                        })
                        
                        processed_domains.add(rel_domain)
                        
                        links.append({
                            "source": company_id,
                            "target": rel_domain_id,
                            "value": 1,
                            "type": "owns_domain"
                        })
            
            # Buscar empresas relacionadas por sector (solo si tenemos información de sector)
            if current_depth < 1 and sector and relation_type in ['all', 'sector']:
                related_companies = list(col_actual.find(
                    {
                        "empresa.actividad_economica.cnae_descripcion": sector,
                        "$and": [
                            {"empresa.nif": {"$ne": company_nif}},
                            {"identificacion": {"$ne": company_nif}}
                        ]
                    },
                    {"dominio": 1, "empresa": 1, "identificacion": 1}
                ).limit(3))
                
                for rel_company in related_companies:
                    # Procesar empresa relacionada con la misma lógica
                    rel_company_name = "Desconocido"
                    rel_company_nif = None
                    rel_location = ""
                    rel_sector = ""
                    
                    if "empresa" in rel_company and "nif" in rel_company["empresa"]:
                        rel_company_data = rel_company["empresa"]
                        rel_company_name = rel_company_data.get("denominacion", "Desconocido")
                        rel_company_nif = rel_company_data["nif"]
                        
                        if "domicilio" in rel_company_data and "provincia" in rel_company_data["domicilio"]:
                            rel_location = rel_company_data["domicilio"]["provincia"]
                        if "actividad_economica" in rel_company_data and "cnae_descripcion" in rel_company_data["actividad_economica"]:
                            rel_sector = rel_company_data["actividad_economica"]["cnae_descripcion"]
                    elif "identificacion" in rel_company:
                        rel_company_nif = rel_company["identificacion"]
                        rel_company_name = rel_company_nif
                    
                    if rel_company_nif and rel_company_nif not in processed_nifs:
                        rel_company_id = len(nodes)
                        company_ids[rel_company_nif] = rel_company_id
                        
                        nodes.append({
                            "id": rel_company_id,
                            "name": rel_company_name,
                            "type": "company",
                            "nif": rel_company_nif,
                            "location": rel_location,
                            "sector": rel_sector
                        })
                        processed_nifs.add(rel_company_nif)
                        
                        # Crear enlace por sector
                        links.append({
                            "source": company_id,
                            "target": rel_company_id,
                            "value": 0.5,
                            "type": "same_sector"
                        })
                        
    # ✅ AÑADIR ESTA PARTE QUE FALTA:
    
    # Iniciar búsqueda
    expand_network(query)
    
    print(f"Resultado final: {len(nodes)} nodos, {len(links)} enlaces")
    print(f"Tipos de enlaces: {set(link.get('type') for link in links)}")
    
    # Estadísticas
    stats = {
        "companies": len([n for n in nodes if n["type"] == "company"]),
        "domains": len([n for n in nodes if n["type"] == "domain"]),
        "connections": len(links)
    }
    
    # Si no se encontraron datos, mostrar mensaje
    if len(nodes) == 0:
        return render_template('network_search.html', 
                              error=f"No se encontraron datos para '{query}'")
    
    network_data = {"nodes": nodes, "links": links}
    
    return render_template('network_view.html', 
                          network_data=json.dumps(network_data), 
                          query=query,
                          stats=stats,
                          relation_type=relation_type)

@app.route('/ssl-network')
def ssl_network():
    """Visualización de dominios que comparten certificados SSL"""
    fingerprint = request.args.get('fingerprint', '').strip()
    
    if not fingerprint:
        client = get_mongo_client()
        db = client.dominios_db
        
        # ✅ PIPELINE CORREGIDO - Contar dominios únicos, no certificados
        pipeline = [
            {"$match": {"certificados": {"$exists": True, "$ne": []}}},
            {"$unwind": "$certificados"},
            {"$match": {"certificados.fingerprint": {"$exists": True, "$ne": ""}}},
            # ✅ Agrupar por fingerprint Y dominio para evitar duplicados
            {"$group": {
                "_id": {
                    "fingerprint": "$certificados.fingerprint",
                    "dominio": "$dominio"
                }
            }},
            # ✅ Ahora contar dominios únicos por fingerprint
            {"$group": {
                "_id": "$_id.fingerprint", 
                "count": {"$sum": 1}  # Cuenta dominios únicos
            }},
            {"$match": {"count": {"$gt": 1}}},  # Solo certificados compartidos
            {"$sort": {"count": -1}},
            {"$limit": 15}
        ]
        
        try:
            top_fingerprints = list(db.dominios_certgraph.aggregate(pipeline))
            print(f"✅ Encontrados {len(top_fingerprints)} fingerprints comunes")
            for fp in top_fingerprints:
                print(f"  - {fp['_id'][:16]}...{fp['_id'][-8:]}: {fp['count']} dominios")
        except Exception as e:
            print(f"❌ Error en agregación: {e}")
            top_fingerprints = []
        
        return render_template('ssl_network_search.html', fingerprints=top_fingerprints)
    
    # Buscar dominios con el fingerprint especificado
    client = get_mongo_client()
    db = client.dominios_db
    
    print(f"🔍 Buscando dominios con fingerprint: {fingerprint}")
    
    # ✅ QUERY CORREGIDA - Búsqueda simple sin $elemMatch
    query = {
        "certificados.fingerprint": fingerprint
    }
    
    # También buscar variaciones del fingerprint (con/sin dos puntos)
    fingerprint_variants = [
        fingerprint,
        fingerprint.replace(':', ''),
        fingerprint.upper(),
        fingerprint.lower()
    ]
    
    # Usar $in para buscar cualquier variante
    query = {
        "certificados.fingerprint": {"$in": fingerprint_variants}
    }
    
    print(f"Query utilizada: {query}")
    
    try:
        domains = list(db.dominios_certgraph.find(
            query,
            {"dominio": 1, "certificados": 1}
        ))
        
        print(f"✅ Encontrados {len(domains)} dominios")
        
    except Exception as e:
        print(f"❌ Error en consulta: {e}")
        return render_template('ssl_network_search.html', 
                              error=f"Error en la búsqueda: {str(e)}")
    
    if not domains:
        return render_template('ssl_network_search.html', 
                              error=f"No se encontraron dominios con el fingerprint: {fingerprint}")
    
    # Construir la red
    nodes = []
    links = []
    
    # Nodo central del certificado
    cert_node = {
        "id": "cert_0",
        "name": f"SSL Cert",
        "type": "certificate",
        "fingerprint": fingerprint,
        "short_fp": fingerprint[:8] + "..." + fingerprint[-8:] if len(fingerprint) > 16 else fingerprint
    }
    nodes.append(cert_node)
    
    # Obtener información del certificado del primer dominio
    cert_info = None
    for domain_info in domains:
        if 'certificados' in domain_info:
            for cert in domain_info['certificados']:
                cert_fp = cert.get('fingerprint', '')
                # Buscar coincidencia (insensible a mayúsculas/minúsculas)
                if any(variant.lower() in cert_fp.lower() for variant in fingerprint_variants):
                    cert_info = {
                        "fingerprint": cert.get("fingerprint", fingerprint),
                        "key_algorithm": cert.get("key_algorithm", "N/A"),
                        "version": cert.get("version", "N/A"),
                        "serial_number": cert.get("serial_number", "N/A"),
                        "alternative_names": cert.get("alternative_names", [])
                    }
                    break
        if cert_info:
            break
    
    # Añadir dominios
    for i, domain_info in enumerate(domains):
        domain = domain_info["dominio"]
        domain_node = {
            "id": f"domain_{i}",
            "name": domain,
            "type": "domain"
        }
        nodes.append(domain_node)
        
        # Enlace entre certificado y dominio
        links.append({
            "source": "cert_0",
            "target": f"domain_{i}",
            "value": 1
        })
    
    network_data = {"nodes": nodes, "links": links}
    
    return render_template('ssl_network_view.html', 
                          network_data=json.dumps(network_data),
                          fingerprint=fingerprint,
                          domain_count=len(domains),
                          domains=domains,
                          cert_info=cert_info)

@app.route('/tag/<domain>', methods=['POST'])
def add_tag(domain):
    """Añadir etiqueta a un dominio"""
    if not request.is_json:
        return jsonify({"error": "Invalid request"}), 400
        
    tag = request.json.get('tag')
    if not tag:
        return jsonify({"error": "No tag provided"}), 400
        
    # Actualizar en MongoDB
    col_actual = get_col_actual()
    result = col_actual.update_one(
        {"dominio": domain},
        {"$addToSet": {"tags": tag}}  # addToSet evita duplicados
    )
    
    if result.modified_count > 0:
        return jsonify({"success": True, "message": f"Tag '{tag}' added"})
    else:
        return jsonify({"success": False, "message": "No changes made"})

def debug_company_domains(nif):
    """Herramienta de depuración para ver todos los dominios de una empresa"""
    col_actual = get_col_actual()
    domains = list(col_actual.find(
        {"empresa.nif": nif},
        {"dominio": 1, "_id": 0}
    ))
    print(f"Dominios encontrados para NIF {nif}:")
    for d in domains:
        print(f"  - {d.get('dominio', 'N/A')}")
    return domains

def get_domain_complete_data(domain):
    """Get complete domain data from multiple collections"""
    client = get_mongo_client()
    db = client['dominios_db']
    
    # Datos principales
    col_actual = get_col_actual()
    main_data = col_actual.find_one({"dominio": domain})
    
    if not main_data:
        return None
    
    # Datos de certificados desde dominios_certgraph
    col_certgraph = db['dominios_certgraph']
    cert_data = col_certgraph.find_one({"dominio": domain})
    
    if cert_data and 'certificados' in cert_data:
        main_data['certificados'] = cert_data['certificados']
        if 'dominios_relacionados' in cert_data:
            main_data['dominios_relacionados'] = cert_data['dominios_relacionados']
        if 'fecha_analisis' in cert_data:
            main_data['fecha_analisis'] = cert_data['fecha_analisis']
    
    return main_data

if __name__ == "__main__":
    app.run(debug=True)


