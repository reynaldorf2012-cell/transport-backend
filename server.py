from fastapi import FastAPI
import os
from pymongo import MongoClient

app = FastAPI()

# ==============================
# CONFIGURACIÓN MONGO SEGURA
# ==============================
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "transportes_db")

client = None
db = None

try:
    if MONGO_URL:
        client = MongoClient(MONGO_URL)
        db = client[DB_NAME]
        print("✅ Conectado a MongoDB")
    else:
        print("⚠️ MONGO_URL no definida")
except Exception as e:
    print("❌ Error conectando a Mongo:", e)

# ==============================
# RUTA DE PRUEBA (CORREGIDA)
# ==============================
@app.get("/")
def root():
    return {
        "status": "ok",
        "mongo": "connected" if db is not None else "not connected"
    }

# ==============================
# TEST DB (CORREGIDO)
# ==============================
@app.get("/test-db")
def test_db():
    if db is None:
        return {"error": "No DB connection"}

    try:
        coleccion = db["servicios"]
        count = coleccion.count_documents({})
        return {"total_servicios": count}
    except Exception as e:
        return {"error": str(e)}