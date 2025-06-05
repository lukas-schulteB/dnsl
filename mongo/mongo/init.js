// MongoDB initialization script for domain processing system

// Connect to database (or create if it doesn't exist)
const conn = new Mongo();
const db = conn.getDB("dominios_db");

print("Connected to database: dominios_db");

// Create collections 
print("Creating collections...");

// Main collections for domain data
db.createCollection("dominios_actuales");
db.createCollection("dominios_historico");

// Collection for tool-specific data
db.createCollection("dominios_lynx");
db.createCollection("dominios_certgraph");
db.createCollection("dominios_crosslinked");
db.createCollection("dominios_empresa");  // Nueva colección para OpenData

// Collection for pending domains
db.createCollection("dominios_pendientes");

// Create indexes
print("Creating indexes...");

// Indexes for dominios_actuales
db.dominios_actuales.createIndex({ "dominio": 1 }, { unique: true });
db.dominios_actuales.createIndex({ "registrante.identificacion": 1 });
db.dominios_actuales.createIndex({ "ips": 1 });

// Indexes for dominios_historico
db.dominios_historico.createIndex({ "dominio": 1 });
db.dominios_historico.createIndex({ "fecha": 1 });

// Indexes for tool collections
db.dominios_lynx.createIndex({ "dominio": 1 }, { unique: true });
db.dominios_certgraph.createIndex({ "dominio": 1 }, { unique: true });
db.dominios_crosslinked.createIndex({ "dominio": 1 }, { unique: true });
db.dominios_empresa.createIndex({ "dominio": 1 }, { unique: true }); 
db.dominios_empresa.createIndex({ "empresa.nif": 1 });  
db.dominios_empresa.createIndex({ "empresa.denominacion": 1 });  
db.dominios_empresa.createIndex({ "termino_busqueda": 1 });  
// Indexes for dominios_pendientes
db.dominios_pendientes.createIndex({ "dominio": 1 }, { unique: true });
db.dominios_pendientes.createIndex({ "procesado_por.main": 1 });
db.dominios_pendientes.createIndex({ "procesado_por.lynx": 1 });
db.dominios_pendientes.createIndex({ "procesado_por.certgraph": 1 });
db.dominios_pendientes.createIndex({ "procesado_por.opendata": 1 });  

print("Database initialization complete - all collections and indexes created");