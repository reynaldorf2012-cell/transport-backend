from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
import bcrypt
import jwt
from bson import ObjectId
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import base64
from geopy.geocoders import Nominatim
from PIL import Image as PILImage
import requests
import pytz
import certifi

# ============ CONFIG ============

ROOT_DIR = Path(*file*).parent
load_dotenv(ROOT_DIR / ‘.env’)

MEXICO_TZ = pytz.timezone(‘America/Mexico_City’)

def to_mexico_time(dt: datetime) -> datetime:
if dt.tzinfo is None:
dt = pytz.utc.localize(dt)
return dt.astimezone(MEXICO_TZ)

VALID_CATEGORIES = [‘folio’, ‘transporte’, ‘placas’, ‘temperatura’, ‘sello’, ‘licencia’, ‘carga’, ‘descarga’]
FOTO_CATEGORIAS = [“documentacion”, “evidencia”, “transporte”, “placas”, “temperatura”, “sello”, “licencia”]
MAX_FOTOS_POR_CATEGORIA = 10

# ============ MONGODB ============

mongo_url = os.environ[‘MONGO_URL’]
db_name = os.environ[‘DB_NAME’]

client = AsyncIOMotorClient(
mongo_url,
tlsCAFile=certifi.where(),
serverSelectionTimeoutMS=30000,
connectTimeoutMS=30000,
socketTimeoutMS=30000
)
db = client[db_name]

safe_url = mongo_url.split(’@’)[-1] if ‘@’ in mongo_url else mongo_url
print(f”[DB] Conectando a MongoDB Atlas: {safe_url} / {db_name}”)

# ============ JWT ============

JWT_SECRET = os.environ.get(‘JWT_SECRET’, ‘virgo-transport-secret-2024’)
JWT_ALGORITHM = “HS256”
JWT_EXPIRATION_HOURS = 24

# ============ LOGOS PDF ============

# Pon aquí tus propias URLs de logos (Cloudinary, etc.)

LOGO_HEADER_URL = os.environ.get(‘LOGO_HEADER_URL’, ‘’)
LOGO_WATERMARK_URL = os.environ.get(‘LOGO_WATERMARK_URL’, ‘’)

# ============ APP ============

app = FastAPI(title=“Virgo Transport API”)
api_router = APIRouter(prefix=”/api”)
security = HTTPBearer()

logging.basicConfig(level=logging.INFO, format=’%(asctime)s - %(levelname)s - %(message)s’)
logger = logging.getLogger(*name*)

geolocator = Nominatim(user_agent=“virgo_transport_app”)

# ============ MODELS ============

class UserBase(BaseModel):
username: str
nombre: str
role: str

class UserCreate(UserBase):
password: str

class User(UserBase):
id: str
created_at: datetime = Field(default_factory=datetime.utcnow)

class UserLogin(BaseModel):
username: str
password: str

class TokenResponse(BaseModel):
token: str
user: User

class FotoCreate(BaseModel):
tipo: str
categoria: str = ‘carga’
imagen_base64: str
latitud: Optional[float] = None
longitud: Optional[float] = None
direccion: Optional[str] = None

class Foto(BaseModel):
id: str = Field(default_factory=lambda: str(uuid.uuid4()))
tipo: str
categoria: str = ‘carga’
imagen_base64: str
latitud: Optional[float] = None
longitud: Optional[float] = None
direccion: Optional[str] = None
fecha: datetime = Field(default_factory=datetime.utcnow)
usuario_id: str = “”
aprobada: bool = False
comentario: Optional[str] = None
active: bool = True
added_by: str = “operador”

class FotoUpdate(BaseModel):
aprobada: Optional[bool] = None
comentario: Optional[str] = None
active: Optional[bool] = None

class ServicioCreate(BaseModel):
tipo_servicio: str
cliente: Optional[str] = None
camion: Optional[str] = None
placa_camion: Optional[str] = None
tipo_caja: Optional[str] = None
entidad_caja: Optional[str] = None
placa_caja: Optional[str] = None
operador_nombre: str
operador_foto_url: Optional[str] = None
operador_licencia: Optional[str] = None
origenes: List[str]
destinos: List[str]
cita_carga: Optional[str] = None
cita_descarga: Optional[str] = None
fecha_cita: Optional[str] = None
portada_url: Optional[str] = None
unidad: Optional[str] = None

class ServicioUpdate(BaseModel):
tipo_servicio: Optional[str] = None
cliente: Optional[str] = None
camion: Optional[str] = None
placa_camion: Optional[str] = None
tipo_caja: Optional[str] = None
entidad_caja: Optional[str] = None
placa_caja: Optional[str] = None
operador_nombre: Optional[str] = None
operador_foto_url: Optional[str] = None
operador_licencia: Optional[str] = None
origenes: Optional[List[str]] = None
destinos: Optional[List[str]] = None
estado: Optional[str] = None
unidad: Optional[str] = None
cita_carga: Optional[str] = None
cita_descarga: Optional[str] = None

class Servicio(BaseModel):
id: str
tipo_servicio: str
cliente: Optional[str] = None
camion: Optional[str] = None
placa_camion: Optional[str] = None
unidad: Optional[str] = None
tipo_caja: Optional[str] = None
entidad_caja: Optional[str] = None
placa_caja: Optional[str] = None
operador_nombre: str
operador_foto_url: Optional[str] = None
operador_licencia: Optional[str] = None
origenes: List[str] = []
origen: Optional[str] = None
destinos: List[str] = []
cita_carga: Optional[str] = None
cita_descarga: Optional[str] = None
fecha_cita: Optional[str] = None
estado: str = “pendiente”
estado_proceso: str = “ESPERA”
sub_estado: Optional[str] = None
hora_llegada_origen: Optional[datetime] = None
hora_inicio_carga: Optional[datetime] = None
hora_fin_carga: Optional[datetime] = None
hora_llegada_destino: Optional[datetime] = None
hora_inicio_descarga: Optional[datetime] = None
hora_fin_descarga: Optional[datetime] = None
hora_llegada: Optional[datetime] = None
hora_carga: Optional[datetime] = None
hora_entrega: Optional[datetime] = None
fotos: List[Foto] = []
fotos_etapas: Optional[dict] = None
firma_base64: Optional[str] = None
firmante_nombre: Optional[str] = None
fecha_creacion: datetime = Field(default_factory=datetime.utcnow)
fecha_actualizacion: datetime = Field(default_factory=datetime.utcnow)

class OperadorCreate(BaseModel):
nombre: str
telefono: str
licencia: str
vigencia_licencia: Optional[str] = None
rfc: Optional[str] = None
id_operador: str
foto_url: Optional[str] = None

class OperadorUpdate(BaseModel):
nombre: Optional[str] = None
telefono: Optional[str] = None
licencia: Optional[str] = None
vigencia_licencia: Optional[str] = None
rfc: Optional[str] = None
id_operador: Optional[str] = None
foto_url: Optional[str] = None
status: Optional[str] = None

class Operador(BaseModel):
id: str
nombre: str
telefono: str
licencia: str
vigencia_licencia: Optional[str] = None
rfc: Optional[str] = None
id_operador: str
foto_url: Optional[str] = None
status: str = “activo”
fecha_creacion: Optional[datetime] = None

class CamionCreate(BaseModel):
nombre: str
numero: int
placa: str
tipo_caja: str

class Camion(BaseModel):
id: str
nombre: str
numero: int
placa: str
tipo_caja: str

class CajaCreate(BaseModel):
tipo_caja: str
numero_entidad: str
placa: str

class CajaUpdate(BaseModel):
tipo_caja: Optional[str] = None
numero_entidad: Optional[str] = None
placa: Optional[str] = None
status: Optional[str] = None

class Caja(BaseModel):
id: str
tipo_caja: str
numero_entidad: str
placa: str
status: str = “activo”
fecha_creacion: Optional[datetime] = None

class AvanzarEtapaRequest(BaseModel):
forzar: bool = False

class FotoEtapaRequest(BaseModel):
imagen_base64: str
categoria: str = “evidencia”
tipo_foto: Optional[str] = None
latitud: Optional[float] = None
longitud: Optional[float] = None
etapa_override: Optional[str] = None

class RegistrarEventoRequest(BaseModel):
evento: str

class OperadorLogin(BaseModel):
id_operador: str

class ReabrirRequest(BaseModel):
etapa: str = “entrega”

class SignatureUpdate(BaseModel):
firma_base64: str
firmante_nombre: Optional[str] = None

# ============ HELPERS ============

def hash_password(password: str) -> str:
return bcrypt.hashpw(password.encode(‘utf-8’), bcrypt.gensalt()).decode(‘utf-8’)

def verify_password(password: str, hashed: str) -> bool:
return bcrypt.checkpw(password.encode(‘utf-8’), hashed.encode(‘utf-8’))

def create_token(user_id: str) -> str:
payload = {“user_id”: user_id, “exp”: datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)}
return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
try:
payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
user_id = payload.get(“user_id”)
if not user_id:
raise HTTPException(status_code=401, detail=“Token inválido”)
user = await db.users.find_one({”_id”: ObjectId(user_id)})
if not user:
raise HTTPException(status_code=401, detail=“Usuario no encontrado”)
return {“id”: str(user[”_id”]), “username”: user[“username”], “nombre”: user[“nombre”], “role”: user[“role”]}
except jwt.ExpiredSignatureError:
raise HTTPException(status_code=401, detail=“Token expirado”)
except jwt.InvalidTokenError:
raise HTTPException(status_code=401, detail=“Token inválido”)

def get_address_from_coords(lat: float, lon: float) -> str:
try:
location = geolocator.reverse(f”{lat}, {lon}”, language=“es”)
return location.address if location else f”{lat}, {lon}”
except:
return f”{lat}, {lon}”

def crear_estructura_fotos_etapas():
return {“espera”: {cat: [] for cat in FOTO_CATEGORIAS},
“carga”: {cat: [] for cat in FOTO_CATEGORIAS},
“entrega”: {cat: [] for cat in FOTO_CATEGORIAS}}

def contar_fotos_etapa(fotos_etapas: dict, etapa_key: str) -> int:
etapa_data = fotos_etapas.get(etapa_key, {})
if isinstance(etapa_data, list):
return len(etapa_data)
elif isinstance(etapa_data, dict):
return sum(len(v) for v in etapa_data.values() if isinstance(v, list))
return 0

def compress_image_base64(image_base64: str, max_width: int = 1280, quality: int = 75) -> str:
try:
if “,” in image_base64:
image_base64 = image_base64.split(”,”)[1]
img = PILImage.open(BytesIO(base64.b64decode(image_base64)))
if img.mode in (‘RGBA’, ‘P’):
img = img.convert(‘RGB’)
if img.width > max_width:
img = img.resize((max_width, int(img.height * max_width / img.width)), PILImage.LANCZOS)
buf = BytesIO()
img.save(buf, format=‘JPEG’, quality=quality, optimize=True)
return f”data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}”
except:
return f”data:image/jpeg;base64,{image_base64}” if not image_base64.startswith(“data:”) else image_base64

def generar_nombre_pdf(tipo_servicio: str, cliente: str) -> str:
import unicodedata, re
def limpiar(t):
if not t: return “NA”
t = unicodedata.normalize(‘NFD’, t)
t = ‘’.join(c for c in t if unicodedata.category(c) != ‘Mn’)
t = re.sub(r’\s+’, ‘*’, t)
t = re.sub(r’[^\w]’, ‘’, t)
return t.upper() or “NA”
return f”VIRGO*{limpiar(tipo_servicio)}_{limpiar(cliente)}.pdf”

def servicio_to_response(s: dict) -> Servicio:
fotos = [Foto(
id=f.get(“id”, str(uuid.uuid4())),
tipo=f.get(“tipo”, “”),
imagen_base64=f.get(“imagen_base64”, “”),
latitud=f.get(“latitud”),
longitud=f.get(“longitud”),
direccion=f.get(“direccion”),
fecha=f.get(“fecha”, datetime.utcnow()),
usuario_id=f.get(“usuario_id”, “”),
aprobada=f.get(“aprobada”, False),
comentario=f.get(“comentario”)
) for f in s.get(“fotos”, [])]


tipo_servicio = s.get("tipo_servicio") or s.get("cliente") or s.get("tipo") or ""
destinos = s.get("destinos", []) or ([s["destino"]] if s.get("destino") else [])
origenes = s.get("origenes", []) or ([s["origen"]] if s.get("origen") else [])
origen_legacy = s.get("origen") or (origenes[0] if origenes else "")

fotos_etapas_raw = s.get("fotos_etapas")
if fotos_etapas_raw:
    first = fotos_etapas_raw.get("espera")
    if isinstance(first, list):
        fotos_etapas = crear_estructura_fotos_etapas()
        for et in ["espera", "carga", "entrega"]:
            fotos_etapas[et]["evidencia"] = fotos_etapas_raw.get(et, [])
    else:
        fotos_etapas = fotos_etapas_raw
else:
    fotos_etapas = crear_estructura_fotos_etapas()

return Servicio(
    id=str(s["_id"]),
    tipo_servicio=tipo_servicio,
    cliente=s.get("cliente"),
    camion=s.get("camion"),
    placa_camion=s.get("placa_camion"),
    unidad=s.get("unidad") or s.get("camion"),
    tipo_caja=s.get("tipo_caja"),
    entidad_caja=s.get("entidad_caja"),
    placa_caja=s.get("placa_caja"),
    operador_nombre=s.get("operador_nombre", ""),
    operador_foto_url=s.get("operador_foto_url"),
    operador_licencia=s.get("operador_licencia"),
    origenes=origenes,
    origen=origen_legacy,
    destinos=destinos,
    cita_carga=s.get("cita_carga"),
    cita_descarga=s.get("cita_descarga"),
    fecha_cita=s.get("fecha_cita") or s.get("cita_carga"),
    estado=s["estado"],
    estado_proceso=s.get("estado_proceso", "ESPERA"),
    sub_estado=s.get("sub_estado"),
    hora_llegada_origen=s.get("hora_llegada_origen"),
    hora_inicio_carga=s.get("hora_inicio_carga"),
    hora_fin_carga=s.get("hora_fin_carga"),
    hora_llegada_destino=s.get("hora_llegada_destino"),
    hora_inicio_descarga=s.get("hora_inicio_descarga"),
    hora_fin_descarga=s.get("hora_fin_descarga"),
    hora_llegada=s.get("hora_llegada") or s.get("hora_llegada_origen"),
    hora_carga=s.get("hora_carga") or s.get("hora_fin_carga"),
    hora_entrega=s.get("hora_entrega") or s.get("hora_fin_descarga"),
    fotos=fotos,
    fotos_etapas=fotos_etapas,
    firma_base64=s.get("firma_base64"),
    firmante_nombre=s.get("firmante_nombre"),
    fecha_creacion=s["fecha_creacion"],
    fecha_actualizacion=s["fecha_actualizacion"]
)


EVENTOS_CONFIG = {
“llegada_origen”:   {“campo”: “hora_llegada_origen”,   “etapa_requerida”: “ESPERA”,  “siguiente_estado”: None,      “sub_estado”: “en_origen”,   “finaliza”: False},
“inicio_carga”:     {“campo”: “hora_inicio_carga”,     “etapa_requerida”: “ESPERA”,  “siguiente_estado”: “CARGA”,   “sub_estado”: “cargando”,    “finaliza”: False},
“fin_carga”:        {“campo”: “hora_fin_carga”,        “etapa_requerida”: “CARGA”,   “siguiente_estado”: “ENTREGA”, “sub_estado”: None,          “finaliza”: False},
“llegada_destino”:  {“campo”: “hora_llegada_destino”,  “etapa_requerida”: “ENTREGA”, “siguiente_estado”: None,      “sub_estado”: “en_destino”,  “finaliza”: False},
“inicio_descarga”:  {“campo”: “hora_inicio_descarga”,  “etapa_requerida”: “ENTREGA”, “siguiente_estado”: None,      “sub_estado”: “descargando”, “finaliza”: False},
“fin_descarga”:     {“campo”: “hora_fin_descarga”,     “etapa_requerida”: “ENTREGA”, “siguiente_estado”: None,      “sub_estado”: “completado”,  “finaliza”: True},
}

# ============ AUTH ROUTES ============

@api_router.post(”/auth/login”, response_model=TokenResponse)
async def login(credentials: UserLogin):
user = await db.users.find_one({“username”: credentials.username})
if not user:
raise HTTPException(status_code=401, detail=“Usuario o contraseña incorrectos”)
pwd = user.get(“password_hash”) or user.get(“password”)
if not pwd or not verify_password(credentials.password, pwd):
raise HTTPException(status_code=401, detail=“Usuario o contraseña incorrectos”)
if user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Solo administradores pueden iniciar sesión”)
token = create_token(str(user[”_id”]))
return TokenResponse(token=token, user=User(id=str(user[”_id”]), username=user[“username”], nombre=user[“nombre”], role=user[“role”]))

@api_router.get(”/auth/me”, response_model=User)
async def get_me(current_user: dict = Depends(get_current_user)):
return User(id=current_user[“id”], username=current_user[“username”], nombre=current_user[“nombre”], role=current_user[“role”])

# ============ PUBLIC ROUTES (OPERATORS) ============

@api_router.get(”/servicios/public”, response_model=List[Servicio])
async def get_servicios_public():
servicios = await db.servicios.find({“estado”: {”$in”: [“pendiente”, “en_progreso”]}}).sort(“fecha_creacion”, -1).to_list(100)
return [servicio_to_response(s) for s in servicios]

@api_router.get(”/servicios/public/{servicio_id}”, response_model=Servicio)
async def get_servicio_public(servicio_id: str):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
return servicio_to_response(servicio)

@api_router.post(”/servicios/public/{servicio_id}/fotos”)
async def add_foto_public(servicio_id: str, foto_data: FotoCreate):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


if len(servicio.get("fotos", [])) >= 50:
    raise HTTPException(status_code=400, detail="Límite de fotos alcanzado (máx. 50)")

categoria = foto_data.categoria.lower() if foto_data.categoria else foto_data.tipo
if categoria not in VALID_CATEGORIES:
    categoria = foto_data.tipo

compressed = compress_image_base64(foto_data.imagen_base64)
direccion = foto_data.direccion
if foto_data.latitud and foto_data.longitud and not direccion:
    direccion = get_address_from_coords(foto_data.latitud, foto_data.longitud)

foto = {
    "id": str(uuid.uuid4()), "tipo": foto_data.tipo, "categoria": categoria,
    "imagen_base64": compressed, "latitud": foto_data.latitud, "longitud": foto_data.longitud,
    "direccion": direccion, "fecha": datetime.now(MEXICO_TZ), "usuario_id": "operador", "aprobada": True
}

try:
    await db.servicios.update_one({"_id": ObjectId(servicio_id)},
        {"$push": {"fotos": foto}, "$set": {"estado": "en_progreso", "fecha_actualizacion": datetime.utcnow()}})
except Exception as e:
    raise HTTPException(status_code=500, detail="Error al guardar la foto")
return {"message": "Foto agregada exitosamente", "foto": foto}


@api_router.put(”/servicios/public/{servicio_id}/completar”)
async def completar_servicio_public(servicio_id: str):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
await db.servicios.update_one({”_id”: ObjectId(servicio_id)},
{”$set”: {“estado”: “completado”, “fecha_actualizacion”: datetime.utcnow()}})
return {“message”: “Servicio completado exitosamente”}

@api_router.delete(”/servicios/public/{servicio_id}/fotos/{foto_id}”)
async def delete_foto_public(servicio_id: str, foto_id: str):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if servicio.get(“estado”) == “completado”:
raise HTTPException(status_code=400, detail=“No se pueden eliminar fotos de un servicio completado”)


fotos = servicio.get("fotos", [])
new_fotos = [f for f in fotos if f.get("id") != foto_id]
if len(new_fotos) == len(fotos):
    raise HTTPException(status_code=404, detail="Foto no encontrada")

await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$set": {"fotos": new_fotos, "fecha_actualizacion": datetime.utcnow()}})
return {"message": "Foto eliminada exitosamente"}


@api_router.post(”/servicios/public/{servicio_id}/etapa/foto”)
async def agregar_foto_etapa(servicio_id: str, foto: FotoEtapaRequest):
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


etapa_key = (foto.etapa_override or servicio.get("estado_proceso", "ESPERA")).lower()
if etapa_key not in ["espera", "carga", "entrega"]:
    raise HTTPException(status_code=400, detail="Etapa inválida")

categoria = foto.categoria.lower()
if categoria not in FOTO_CATEGORIAS:
    raise HTTPException(status_code=400, detail=f"Categoría inválida")

fotos_etapas_raw = servicio.get("fotos_etapas")
if fotos_etapas_raw:
    first = fotos_etapas_raw.get("espera")
    if isinstance(first, list):
        fotos_etapas = crear_estructura_fotos_etapas()
        for et in ["espera", "carga", "entrega"]:
            fotos_etapas[et]["evidencia"] = fotos_etapas_raw.get(et, [])
    else:
        fotos_etapas = fotos_etapas_raw
else:
    fotos_etapas = crear_estructura_fotos_etapas()

if etapa_key not in fotos_etapas:
    fotos_etapas[etapa_key] = {cat: [] for cat in FOTO_CATEGORIAS}
if categoria not in fotos_etapas[etapa_key]:
    fotos_etapas[etapa_key][categoria] = []

if len(fotos_etapas[etapa_key][categoria]) >= MAX_FOTOS_POR_CATEGORIA:
    raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS_POR_CATEGORIA} fotos por categoría")

direccion = get_address_from_coords(foto.latitud, foto.longitud) if foto.latitud and foto.longitud else None

nueva_foto = {
    "id": str(uuid.uuid4()), "tipo": categoria, "categoria": categoria,
    "tipo_foto": foto.tipo_foto, "imagen_base64": foto.imagen_base64,
    "latitud": foto.latitud, "longitud": foto.longitud,
    "direccion": direccion, "fecha": datetime.utcnow(), "etapa": etapa_key.upper()
}
fotos_etapas[etapa_key][categoria].append(nueva_foto)

await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$set": {"fotos_etapas": fotos_etapas, "fecha_actualizacion": datetime.utcnow()}})

servicio = await db.servicios.find_one({"_id": ObjectId(servicio_id)})
return servicio_to_response(servicio)


@api_router.put(”/servicios/public/{servicio_id}/etapa/avanzar”)
async def avanzar_etapa(servicio_id: str, request: AvanzarEtapaRequest = AvanzarEtapaRequest()):
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


estado_actual = servicio.get("estado_proceso", "ESPERA").upper()
fotos_etapas = servicio.get("fotos_etapas", crear_estructura_fotos_etapas())
fotos_en_etapa = contar_fotos_etapa(fotos_etapas, estado_actual.lower())

if not request.forzar and fotos_en_etapa == 0:
    raise HTTPException(status_code=400, detail=f"Debes tomar al menos una foto en la etapa {estado_actual}")

ahora = datetime.utcnow()
if estado_actual == "ESPERA":
    update_fields = {"estado_proceso": "CARGA", "estado": "en_progreso", "fecha_actualizacion": ahora}
    if not servicio.get("hora_llegada"):
        update_fields["hora_llegada"] = ahora
elif estado_actual == "CARGA":
    update_fields = {"estado_proceso": "ENTREGA", "estado": "en_progreso", "fecha_actualizacion": ahora}
    if not servicio.get("hora_carga"):
        update_fields["hora_carga"] = ahora
elif estado_actual == "ENTREGA":
    update_fields = {"estado": "completado", "fecha_actualizacion": ahora}
    if not servicio.get("hora_entrega"):
        update_fields["hora_entrega"] = ahora
    await db.servicios.update_one({"_id": ObjectId(servicio_id)}, {"$set": update_fields})
    servicio = await db.servicios.find_one({"_id": ObjectId(servicio_id)})
    return {"message": "Viaje finalizado exitosamente", "servicio": servicio_to_response(servicio)}
else:
    raise HTTPException(status_code=400, detail="Estado inválido")

await db.servicios.update_one({"_id": ObjectId(servicio_id)}, {"$set": update_fields})
servicio = await db.servicios.find_one({"_id": ObjectId(servicio_id)})
return {"message": f"Avanzado a {update_fields.get('estado_proceso', 'ENTREGA')}", "servicio": servicio_to_response(servicio)}


@api_router.delete(”/servicios/public/{servicio_id}/etapa/foto/{foto_id}”)
async def eliminar_foto_etapa(servicio_id: str, foto_id: str):
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


fotos_etapas = servicio.get("fotos_etapas", crear_estructura_fotos_etapas())
foto_eliminada = False
for etapa in ["espera", "carga", "entrega"]:
    etapa_data = fotos_etapas.get(etapa, {})
    if isinstance(etapa_data, list):
        new_list = [f for f in etapa_data if f.get("id") != foto_id]
        if len(new_list) < len(etapa_data):
            fotos_etapas[etapa] = new_list
            foto_eliminada = True
            break
    else:
        for cat, fotos in etapa_data.items():
            if isinstance(fotos, list):
                new_list = [f for f in fotos if f.get("id") != foto_id]
                if len(new_list) < len(fotos):
                    fotos_etapas[etapa][cat] = new_list
                    foto_eliminada = True
                    break
    if foto_eliminada:
        break

if not foto_eliminada:
    raise HTTPException(status_code=404, detail="Foto no encontrada")

await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$set": {"fotos_etapas": fotos_etapas, "fecha_actualizacion": datetime.utcnow()}})
return {"message": "Foto eliminada exitosamente"}


@api_router.put(”/servicios/public/{servicio_id}/evento”)
async def registrar_evento(servicio_id: str, request: RegistrarEventoRequest):
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


evento = request.evento.lower()
if evento not in EVENTOS_CONFIG:
    raise HTTPException(status_code=400, detail=f"Evento no válido: {list(EVENTOS_CONFIG.keys())}")

config = EVENTOS_CONFIG[evento]
campo = config["campo"]

# Evitar duplicados en llegadas
if campo in ["hora_llegada_origen", "hora_llegada_destino"] and servicio.get(campo):
    return {"message": f"Evento ya registrado", "ya_registrado": True,
            "estado_proceso": servicio.get("estado_proceso"), "estado": servicio.get("estado")}

estado_actual = servicio.get("estado_proceso", "ESPERA").upper()
if config["etapa_requerida"] and estado_actual != config["etapa_requerida"]:
    raise HTTPException(status_code=400, detail=f"Evento '{evento}' solo en etapa {config['etapa_requerida']}")

ahora = datetime.utcnow()
update_fields = {campo: ahora, "fecha_actualizacion": ahora}
if config["sub_estado"]:
    update_fields["sub_estado"] = config["sub_estado"]
if config["siguiente_estado"]:
    update_fields["estado_proceso"] = config["siguiente_estado"]
    update_fields["estado"] = "en_progreso"
    update_fields["sub_estado"] = None
if config["finaliza"]:
    update_fields["estado"] = "completado"

legacy_map = {"hora_llegada_origen": "hora_llegada", "hora_fin_carga": "hora_carga", "hora_fin_descarga": "hora_entrega"}
if campo in legacy_map:
    update_fields[legacy_map[campo]] = ahora

await db.servicios.update_one({"_id": ObjectId(servicio_id)}, {"$set": update_fields})
s = await db.servicios.find_one({"_id": ObjectId(servicio_id)})
return {"message": f"Evento '{evento}' registrado", "timestamp": ahora.isoformat(),
        "estado_proceso": s.get("estado_proceso"), "estado": s.get("estado")}


@api_router.put(”/servicios/public/{servicio_id}/reabrir”)
async def reabrir_servicio(servicio_id: str, request: ReabrirRequest = ReabrirRequest()):
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if servicio.get(“estado”) != “completado”:
raise HTTPException(status_code=400, detail=“Solo se pueden reabrir servicios completados”)
etapa = request.etapa.upper()
if etapa not in [“ESPERA”, “CARGA”, “ENTREGA”]:
raise HTTPException(status_code=400, detail=“Etapa inválida”)
await db.servicios.update_one({”_id”: ObjectId(servicio_id)},
{”$set”: {“estado”: “en_progreso”, “estado_proceso”: etapa, “fecha_actualizacion”: datetime.utcnow()}})
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
return {“message”: f”Servicio reabierto en etapa {etapa}”, “servicio”: servicio_to_response(servicio)}

# ============ OPERATOR LOGIN ============

@api_router.post(”/operador/login”)
async def operador_login(data: OperadorLogin):
operador = await db.operadores.find_one({“id_operador”: data.id_operador.upper()})
if not operador:
raise HTTPException(status_code=401, detail=“ID de operador incorrecto”)
return {“success”: True, “operador”: {“id”: str(operador[”_id”]), “nombre”: operador[“nombre”],
“id_operador”: operador[“id_operador”], “telefono”: operador.get(“telefono”, “”)}}

@api_router.get(”/operador/{id_operador}/servicios”)
async def get_operador_servicios(id_operador: str, solo_hoy: bool = True, historial: bool = False, page: int = 1, limit: int = 10):
limit = min(limit, 20)
skip = (page - 1) * limit
operador = await db.operadores.find_one({“id_operador”: id_operador.upper()})
if not operador:
raise HTTPException(status_code=404, detail=“Operador no encontrado”)


mexico_tz = pytz.timezone('America/Mexico_City')
ahora_mexico = datetime.now(mexico_tz)

if historial:
    fecha_desde = datetime.utcnow() - timedelta(days=30)
elif solo_hoy:
    inicio_dia = ahora_mexico.replace(hour=0, minute=0, second=0, microsecond=0)
    fecha_desde = inicio_dia.astimezone(pytz.UTC).replace(tzinfo=None)
else:
    fecha_desde = datetime.utcnow() - timedelta(days=2)

query = {"operador_nombre": operador["nombre"], "fecha_creacion": {"$gte": fecha_desde}}
total_count = await db.servicios.count_documents(query)
servicios = await db.servicios.find(query).sort("fecha_creacion", -1).skip(skip).limit(limit).to_list(limit)

result = []
for s in servicios:
    fotos_etapas = s.get("fotos_etapas", {})
    total_fotos = sum(contar_fotos_etapa(fotos_etapas, et) for et in ["espera", "carga", "entrega"])
    if total_fotos == 0:
        total_fotos = len(s.get("fotos", []))
    origenes = s.get("origenes", []) or ([s["origen"]] if s.get("origen") else [])
    result.append({
        "id": str(s["_id"]),
        "tipo_servicio": s.get("tipo_servicio") or s.get("cliente") or "N/A",
        "unidad": s.get("unidad", "N/A"),
        "operador_nombre": s.get("operador_nombre", "N/A"),
        "origenes": origenes,
        "destinos": s.get("destinos", []),
        "estado": s.get("estado", "pendiente"),
        "estado_proceso": s.get("estado_proceso", "espera"),
        "fecha_creacion": s.get("fecha_creacion"),
        "cita_carga": s.get("cita_carga"),
        "cita_descarga": s.get("cita_descarga"),
        "fotos_count": total_fotos,
        "hora_llegada_origen": s.get("hora_llegada_origen"),
        "hora_inicio_carga": s.get("hora_inicio_carga"),
        "hora_fin_carga": s.get("hora_fin_carga"),
        "hora_llegada_destino": s.get("hora_llegada_destino"),
        "hora_inicio_descarga": s.get("hora_inicio_descarga"),
        "hora_fin_descarga": s.get("hora_fin_descarga"),
    })
return {"items": result, "total": total_count, "page": page, "limit": limit, "has_more": (skip + len(result)) < total_count}


# ============ ADMIN ROUTES ============

@api_router.get(”/servicios”, response_model=List[Servicio])
async def get_servicios(current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
servicios = await db.servicios.find().sort(“fecha_creacion”, -1).to_list(200)
return [servicio_to_response(s) for s in servicios]

@api_router.post(”/servicios”, response_model=Servicio)
async def create_servicio(servicio_data: ServicioCreate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Solo admin puede crear servicios”)


operador_foto_url = servicio_data.operador_foto_url
operador_licencia = servicio_data.operador_licencia

if not operador_foto_url or not operador_licencia:
    operador = await db.operadores.find_one({"nombre": servicio_data.operador_nombre})
    if operador:
        operador_foto_url = operador_foto_url or operador.get("foto_url")
        operador_licencia = operador_licencia or operador.get("licencia")

servicio = {
    "tipo_servicio": servicio_data.tipo_servicio,
    "cliente": servicio_data.cliente,
    "camion": servicio_data.camion,
    "placa_camion": servicio_data.placa_camion,
    "unidad": servicio_data.camion,
    "tipo_caja": servicio_data.tipo_caja,
    "entidad_caja": servicio_data.entidad_caja,
    "placa_caja": servicio_data.placa_caja,
    "operador_nombre": servicio_data.operador_nombre,
    "operador_foto_url": operador_foto_url,
    "operador_licencia": operador_licencia,
    "origenes": servicio_data.origenes,
    "origen": servicio_data.origenes[0] if servicio_data.origenes else "",
    "destinos": servicio_data.destinos,
    "destino": servicio_data.destinos[0] if servicio_data.destinos else "",
    "cita_carga": servicio_data.cita_carga,
    "cita_descarga": servicio_data.cita_descarga,
    "fecha_cita": servicio_data.cita_carga,
    "estado": "pendiente",
    "estado_proceso": "ESPERA",
    "fotos": [],
    "fotos_etapas": crear_estructura_fotos_etapas(),
    "fecha_creacion": datetime.utcnow(),
    "fecha_actualizacion": datetime.utcnow(),
}

result = await db.servicios.insert_one(servicio)
servicio["_id"] = result.inserted_id
return servicio_to_response(servicio)


@api_router.get(”/servicios/{servicio_id}”, response_model=Servicio)
async def get_servicio(servicio_id: str, current_user: dict = Depends(get_current_user)):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
return servicio_to_response(servicio)

@api_router.put(”/servicios/{servicio_id}”, response_model=Servicio)
async def update_servicio(servicio_id: str, update_data: ServicioUpdate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Solo admin puede actualizar servicios”)
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
update_dict["fecha_actualizacion"] = datetime.utcnow()
await db.servicios.update_one({"_id": ObjectId(servicio_id)}, {"$set": update_dict})
servicio = await db.servicios.find_one({"_id": ObjectId(servicio_id)})
return servicio_to_response(servicio)


@api_router.delete(”/servicios/{servicio_id}”)
async def delete_servicio(servicio_id: str, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Solo admin puede eliminar servicios”)
try:
result = await db.servicios.delete_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if result.deleted_count == 0:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
return {“message”: “Servicio eliminado exitosamente”}

@api_router.post(”/servicios/{servicio_id}/fotos”)
async def add_foto_admin(servicio_id: str, foto_data: FotoCreate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


compressed = compress_image_base64(foto_data.imagen_base64)
foto = {
    "id": str(uuid.uuid4()), "tipo": foto_data.tipo, "categoria": foto_data.categoria,
    "imagen_base64": compressed, "latitud": foto_data.latitud, "longitud": foto_data.longitud,
    "fecha": datetime.utcnow(), "usuario_id": current_user["id"], "aprobada": True, "added_by": "admin"
}
await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$push": {"fotos": foto}, "$set": {"fecha_actualizacion": datetime.utcnow()}})
return {"message": "Foto agregada", "foto": foto}


@api_router.put(”/servicios/{servicio_id}/fotos/{foto_id}”)
async def update_foto_admin(servicio_id: str, foto_id: str, update: FotoUpdate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


fotos = servicio.get("fotos", [])
for f in fotos:
    if f.get("id") == foto_id:
        if update.aprobada is not None: f["aprobada"] = update.aprobada
        if update.comentario is not None: f["comentario"] = update.comentario
        if update.active is not None: f["active"] = update.active
        break

await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$set": {"fotos": fotos, "fecha_actualizacion": datetime.utcnow()}})
return {"message": "Foto actualizada"}


@api_router.delete(”/servicios/{servicio_id}/fotos/{foto_id}”)
async def delete_foto_admin(servicio_id: str, foto_id: str, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


fotos = servicio.get("fotos", [])
new_fotos = [f for f in fotos if f.get("id") != foto_id]
await db.servicios.update_one({"_id": ObjectId(servicio_id)},
    {"$set": {"fotos": new_fotos, "fecha_actualizacion": datetime.utcnow()}})
return {"message": "Foto eliminada"}


@api_router.put(”/servicios/{servicio_id}/firma”)
async def update_firma(servicio_id: str, sig: SignatureUpdate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
try:
await db.servicios.update_one({”_id”: ObjectId(servicio_id)},
{”$set”: {“firma_base64”: sig.firma_base64, “firmante_nombre”: sig.firmante_nombre, “fecha_actualizacion”: datetime.utcnow()}})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
return {“message”: “Firma guardada”}

# ============ CATALOGS: OPERADORES ============

@api_router.get(”/catalogo/operadores”)
async def get_operadores_catalogo(current_user: dict = Depends(get_current_user)):
ops = await db.operadores.find().sort(“nombre”, 1).to_list(100)
return [{“id”: str(o[”_id”]), “nombre”: o[“nombre”], “telefono”: o.get(“telefono”, “”),
“licencia”: o.get(“licencia”, “”), “vigencia_licencia”: o.get(“vigencia_licencia”),
“rfc”: o.get(“rfc”), “id_operador”: o.get(“id_operador”, “”),
“foto_url”: o.get(“foto_url”), “status”: o.get(“status”, “activo”)} for o in ops]

@api_router.post(”/catalogo/operadores”)
async def create_operador(data: OperadorCreate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
existing = await db.operadores.find_one({“id_operador”: data.id_operador.upper()})
if existing:
raise HTTPException(status_code=400, detail=“ID de operador ya existe”)
op = {**data.dict(), “id_operador”: data.id_operador.upper(), “status”: “activo”, “fecha_creacion”: datetime.utcnow()}
result = await db.operadores.insert_one(op)
return {“id”: str(result.inserted_id), **data.dict()}

@api_router.put(”/catalogo/operadores/{operador_id}”)
async def update_operador(operador_id: str, data: OperadorUpdate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
update_dict = {k: v for k, v in data.dict().items() if v is not None}
update_dict[“fecha_actualizacion”] = datetime.utcnow()
await db.operadores.update_one({”_id”: ObjectId(operador_id)}, {”$set”: update_dict})
return {“message”: “Operador actualizado”}

@api_router.delete(”/catalogo/operadores/{operador_id}”)
async def delete_operador(operador_id: str, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
await db.operadores.delete_one({”_id”: ObjectId(operador_id)})
return {“message”: “Operador eliminado”}

# ============ CATALOGS: CAMIONES ============

@api_router.get(”/catalogo/camiones”)
async def get_camiones(current_user: dict = Depends(get_current_user)):
camiones = await db.camiones.find().sort(“nombre”, 1).to_list(100)
return [{“id”: str(c[”_id”]), “nombre”: c[“nombre”], “numero”: c.get(“numero”, 0),
“placa”: c[“placa”], “tipo_caja”: c.get(“tipo_caja”, “”)} for c in camiones]

@api_router.post(”/catalogo/camiones”)
async def create_camion(data: CamionCreate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
result = await db.camiones.insert_one({**data.dict(), “fecha_creacion”: datetime.utcnow()})
return {“id”: str(result.inserted_id), **data.dict()}

@api_router.delete(”/catalogo/camiones/{camion_id}”)
async def delete_camion(camion_id: str, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
await db.camiones.delete_one({”_id”: ObjectId(camion_id)})
return {“message”: “Camión eliminado”}

# ============ CATALOGS: CAJAS ============

@api_router.get(”/catalogo/cajas”)
async def get_cajas(current_user: dict = Depends(get_current_user)):
cajas = await db.cajas.find().sort(“numero_entidad”, 1).to_list(100)
return [{“id”: str(c[”_id”]), “tipo_caja”: c[“tipo_caja”], “numero_entidad”: c[“numero_entidad”],
“placa”: c[“placa”], “status”: c.get(“status”, “activo”)} for c in cajas]

@api_router.post(”/catalogo/cajas”)
async def create_caja(data: CajaCreate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
result = await db.cajas.insert_one({**data.dict(), “status”: “activo”, “fecha_creacion”: datetime.utcnow()})
return {“id”: str(result.inserted_id), **data.dict()}

@api_router.put(”/catalogo/cajas/{caja_id}”)
async def update_caja(caja_id: str, data: CajaUpdate, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
update_dict = {k: v for k, v in data.dict().items() if v is not None}
await db.cajas.update_one({”_id”: ObjectId(caja_id)}, {”$set”: update_dict})
return {“message”: “Caja actualizada”}

@api_router.delete(”/catalogo/cajas/{caja_id}”)
async def delete_caja(caja_id: str, current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
await db.cajas.delete_one({”_id”: ObjectId(caja_id)})
return {“message”: “Caja eliminada”}

# ============ PDF GENERATION ============

def get_logo_image(url: str, width: float, height: float):
“”“Download and return logo as ReportLab Image”””
if not url:
return None
try:
resp = requests.get(url, timeout=10)
resp.raise_for_status()
return RLImage(BytesIO(resp.content), width=width, height=height)
except:
return None

@api_router.get(”/servicios/{servicio_id}/pdf”)
async def generate_pdf(servicio_id: str, current_user: dict = Depends(get_current_user)):
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


s = servicio_to_response(servicio)
buffer = BytesIO()
doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.5*inch, leftMargin=0.5*inch,
                        topMargin=0.5*inch, bottomMargin=0.5*inch)
styles = getSampleStyleSheet()
elements = []

# Title
title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#1e3a5f'), spaceAfter=12)
elements.append(Paragraph(f"REPORTE DE SERVICIO DE TRANSPORTE", title_style))
elements.append(Paragraph(f"SERVICIO: {s.tipo_servicio.upper()}", title_style))
if s.cliente:
    elements.append(Paragraph(f"CLIENTE: {s.cliente.upper()}", title_style))
elements.append(Spacer(1, 12))

# Operator info
normal = styles['Normal']
data = [
    ["OPERADOR:", s.operador_nombre],
    ["UNIDAD:", s.camion or s.unidad or "N/A"],
    ["PLACAS:", s.placa_camion or "N/A"],
    ["LICENCIA:", s.operador_licencia or "N/A"],
    ["TIPO CAJA:", s.tipo_caja or "N/A"],
    ["ENTIDAD:", s.entidad_caja or "N/A"],
    ["PLACA CAJA:", s.placa_caja or "N/A"],
    ["ORIGEN:", ", ".join(s.origenes) if s.origenes else "N/A"],
    ["DESTINO:", ", ".join(s.destinos) if s.destinos else "N/A"],
]
if s.cita_carga:
    data.append(["CITA CARGA:", s.cita_carga])
if s.cita_descarga:
    data.append(["CITA DESCARGA:", s.cita_descarga])

table = Table(data, colWidths=[2*inch, 4.5*inch])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#1e3a5f')),
    ('TEXTCOLOR', (0, 0), (0, -1), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
    ('FONTSIZE', (0, 0), (-1, -1), 10),
    ('ROWBACKGROUNDS', (1, 0), (-1, -1), [colors.white, colors.HexColor('#f5f8ff')]),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
    ('PADDING', (0, 0), (-1, -1), 6),
]))
elements.append(table)
elements.append(Spacer(1, 16))

# Trazabilidad
traz_data = []
if s.hora_llegada_origen:
    t = to_mexico_time(s.hora_llegada_origen)
    traz_data.append(["Llegada origen:", t.strftime("%d/%m/%Y %I:%M %p")])
if s.hora_inicio_carga:
    t = to_mexico_time(s.hora_inicio_carga)
    traz_data.append(["Inicio carga:", t.strftime("%d/%m/%Y %I:%M %p")])
if s.hora_fin_carga:
    t = to_mexico_time(s.hora_fin_carga)
    traz_data.append(["Fin carga:", t.strftime("%d/%m/%Y %I:%M %p")])
if s.hora_llegada_destino:
    t = to_mexico_time(s.hora_llegada_destino)
    traz_data.append(["Llegada destino:", t.strftime("%d/%m/%Y %I:%M %p")])
if s.hora_inicio_descarga:
    t = to_mexico_time(s.hora_inicio_descarga)
    traz_data.append(["Inicio descarga:", t.strftime("%d/%m/%Y %I:%M %p")])
if s.hora_fin_descarga:
    t = to_mexico_time(s.hora_fin_descarga)
    traz_data.append(["Fin descarga:", t.strftime("%d/%m/%Y %I:%M %p")])

if traz_data:
    elements.append(Paragraph("TRAZABILIDAD", ParagraphStyle('H2', parent=styles['Heading2'], textColor=colors.HexColor('#1e3a5f'))))
    traz_table = Table(traz_data, colWidths=[2.5*inch, 4*inch])
    traz_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(traz_table)
    elements.append(Spacer(1, 12))

# Photos
all_fotos = []
if s.fotos_etapas:
    for etapa in ["espera", "carga", "entrega"]:
        etapa_data = s.fotos_etapas.get(etapa, {})
        if isinstance(etapa_data, dict):
            for cat, fotos in etapa_data.items():
                all_fotos.extend(fotos)
if not all_fotos:
    all_fotos = s.fotos

if all_fotos:
    elements.append(Paragraph("EVIDENCIA FOTOGRÁFICA", ParagraphStyle('H2', parent=styles['Heading2'], textColor=colors.HexColor('#1e3a5f'))))
    elements.append(Spacer(1, 8))
    row = []
    for foto in all_fotos:
        try:
            img_data = foto.imagen_base64 if isinstance(foto, Foto) else foto.get("imagen_base64", "")
            if "," in img_data:
                img_data = img_data.split(",")[1]
            img = RLImage(BytesIO(base64.b64decode(img_data)), width=2.8*inch, height=2.1*inch)
            row.append(img)
            if len(row) == 2:
                elements.append(Table([row], colWidths=[3.3*inch, 3.3*inch]))
                elements.append(Spacer(1, 6))
                row = []
        except:
            pass
    if row:
        while len(row) < 2:
            row.append("")
        elements.append(Table([row], colWidths=[3.3*inch, 3.3*inch]))

# Footer
elements.append(Spacer(1, 20))
footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=7, textColor=colors.grey, alignment=1)
elements.append(Paragraph("Este documento contiene información proporcionada por el cliente.", footer_style))
elements.append(Paragraph("Transportes Virgo la gestiona de forma confidencial y exclusivamente para fines operativos.", footer_style))

doc.build(elements)
buffer.seek(0)

filename = generar_nombre_pdf(s.tipo_servicio, s.cliente or "")
return StreamingResponse(buffer, media_type="application/pdf",
    headers={"Content-Disposition": f"attachment; filename={filename}"})


@api_router.get(”/servicios/{servicio_id}/portada-pdf”)
async def generate_portada_pdf(servicio_id: str, current_user: dict = Depends(get_current_user)):
“”“Generate cover page PDF for a service”””
try:
servicio = await db.servicios.find_one({”_id”: ObjectId(servicio_id)})
except:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)
if not servicio:
raise HTTPException(status_code=404, detail=“Servicio no encontrado”)


s = servicio_to_response(servicio)
buffer = BytesIO()
doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.75*inch, leftMargin=0.75*inch,
                        topMargin=0.75*inch, bottomMargin=0.75*inch)
styles = getSampleStyleSheet()
elements = []

center = ParagraphStyle('Center', parent=styles['Normal'], alignment=1)
h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor('#1e3a5f'), alignment=1, spaceAfter=6)
h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=16, textColor=colors.HexColor('#2d6a9f'), alignment=1, spaceAfter=4)

elements.append(Paragraph("VIRGO TRANSPORTES REFRIGERADOS", h1))
elements.append(Spacer(1, 12))
elements.append(Paragraph(f"SERVICIO: {s.tipo_servicio.upper()}", h2))
if s.cliente:
    elements.append(Paragraph(f"CLIENTE: {s.cliente.upper()}", h2))
elements.append(Spacer(1, 20))

# Operator photo
if s.operador_foto_url:
    try:
        resp = requests.get(s.operador_foto_url, timeout=8)
        photo = RLImage(BytesIO(resp.content), width=2*inch, height=2*inch)
        elements.append(photo)
    except:
        pass

elements.append(Spacer(1, 12))
elements.append(Paragraph(s.operador_nombre, ParagraphStyle('Name', parent=h2, fontSize=18)))
elements.append(Paragraph("OPERADOR ASIGNADO", center))
elements.append(Spacer(1, 20))

# Info table
data = [
    ["UNIDAD", s.camion or "N/A", "PLACAS", s.placa_camion or "N/A"],
    ["LICENCIA", s.operador_licencia or "N/A", "TIPO CAJA", s.tipo_caja or "N/A"],
    ["ENTIDAD", s.entidad_caja or "N/A", "PLACA CAJA", s.placa_caja or "N/A"],
]
table = Table(data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#1e3a5f')),
    ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#1e3a5f')),
    ('TEXTCOLOR', (0, 0), (0, -1), colors.white),
    ('TEXTCOLOR', (2, 0), (2, -1), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
    ('FONTSIZE', (0, 0), (-1, -1), 9),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ('PADDING', (0, 0), (-1, -1), 6),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
]))
elements.append(table)
elements.append(Spacer(1, 16))

# Route
route_data = [["ORIGEN", " → ".join(s.origenes) if s.origenes else "N/A"],
              ["DESTINO", " → ".join(s.destinos) if s.destinos else "N/A"]]
if s.cita_carga:
    route_data.append(["CITA CARGA", s.cita_carga])
if s.cita_descarga:
    route_data.append(["CITA DESCARGA", s.cita_descarga])

route_table = Table(route_data, colWidths=[2*inch, 5*inch])
route_table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#2d6a9f')),
    ('TEXTCOLOR', (0, 0), (0, -1), colors.white),
    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
    ('FONTSIZE', (0, 0), (-1, -1), 10),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ('PADDING', (0, 0), (-1, -1), 8),
    ('ALIGN', (0, 0), (0, -1), 'CENTER'),
]))
elements.append(route_table)

doc.build(elements)
buffer.seek(0)

filename = f"VIRGO_PORTADA_{s.tipo_servicio.replace(' ', '_').upper()}.pdf"
return StreamingResponse(buffer, media_type="application/pdf",
    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ============ RESEED ============

@api_router.post(”/admin/reseed-catalogs”)
async def reseed_catalogs(current_user: dict = Depends(get_current_user)):
if current_user[“role”] != “admin”:
raise HTTPException(status_code=403, detail=“Acceso denegado”)
await seed_catalogs()
return {“message”: “Catálogos actualizados exitosamente”}

async def seed_catalogs():
await db.operadores.delete_many({})
await db.camiones.delete_many({})
await db.cajas.delete_many({})


FOTO_LUIS = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776955926/luis_domingo_yds9eu.jpg"
FOTO_EDDY = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776955926/eddy_garcia_rr3v1p.jpg"
FOTO_JOSE_LUIS = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776955926/jose_luis_alanis_ibhcqe.jpg"
FOTO_ISRAEL = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776955926/israel_espinoza_s1frju.jpg"
FOTO_JAIME = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776955926/jaime_serrano_p3zjwt.jpg"
FOTO_FERNANDO = "https://res.cloudinary.com/dgp94fmou/image/upload/w_300,h_300,c_fit/v1776916916/1a3eb9ba-8bba-4839-a02e-1a0fe717a7b8_wheqhl.jpg"

operadores = [
    {"nombre": "LUIS DOMINGO GARCIA", "telefono": "4622170584", "licencia": "GTO0014693", "vigencia_licencia": "08/12/2029", "rfc": "GACL800425", "id_operador": "A101", "foto_url": FOTO_LUIS},
    {"nombre": "EDDY GARCIA DURAN", "telefono": "4622645747", "licencia": "LFD00005502", "vigencia_licencia": "12/06/2029", "rfc": "GADE930410H90", "id_operador": "A102", "foto_url": FOTO_EDDY},
    {"nombre": "JOSE LUIS OLVERA ROSALES", "telefono": "4623758941", "licencia": "QRO10561", "vigencia_licencia": "16/02/2028", "rfc": "OERL570407", "id_operador": "A103", "foto_url": None},
    {"nombre": "EDUARDO OLVERA PONCE", "telefono": "4621093169", "licencia": "LFD00065675", "vigencia_licencia": "20/05/2026", "rfc": "OEPJ9106012U0", "id_operador": "A104", "foto_url": None},
    {"nombre": "JOSE LUIS OLVERA ALANIS", "telefono": "4623241330", "licencia": "LFD00050237", "vigencia_licencia": "12/03/2030", "rfc": "OEAL020921FN2", "id_operador": "A105", "foto_url": FOTO_JOSE_LUIS},
    {"nombre": "ISRAEL ESPINOZA", "telefono": "4623692726", "licencia": "GTO0015373", "vigencia_licencia": "22/02/2027", "rfc": "IEMI760905H35", "id_operador": "B201", "foto_url": FOTO_ISRAEL},
    {"nombre": "JAIME SERRANO SANCHEZ", "telefono": "7203034300", "licencia": "LFD00014666", "vigencia_licencia": "06/08/2029", "rfc": "SES8001029U3", "id_operador": "B202", "foto_url": FOTO_JAIME},
    {"nombre": "FERNANDO RODRIGUEZ VEGA", "telefono": "5561901596", "licencia": "DF00225867", "vigencia_licencia": "25/10/2027", "rfc": "ROVF780304H40", "id_operador": "B203", "foto_url": FOTO_FERNANDO},
]
await db.operadores.insert_many(operadores)

camiones = [
    {"nombre": "ECO 01", "numero": 1, "placa": "12BJ3V", "tipo_caja": "THERMO"},
    {"nombre": "ECO 29", "numero": 29, "placa": "73BL9R", "tipo_caja": "THERMO"},
    {"nombre": "ECO 04", "numero": 4, "placa": "06BF8D", "tipo_caja": "THERMO"},
    {"nombre": "ECO 11", "numero": 11, "placa": "46BF3E", "tipo_caja": "THERMO"},
    {"nombre": "ECO 14", "numero": 14, "placa": "79BA9P", "tipo_caja": "THERMO"},
    {"nombre": "ECO 05", "numero": 5, "placa": "52BK3Y", "tipo_caja": "THERMO"},
    {"nombre": "ECO 12", "numero": 12, "placa": "38BL4P", "tipo_caja": "THERMO"},
    {"nombre": "ECO 22", "numero": 22, "placa": "96UZ6D", "tipo_caja": "THERMO"},
]
await db.camiones.insert_many(camiones)

cajas = [
    {"tipo_caja": "THERMO", "numero_entidad": "1141", "placa": "25UY7G", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "THERMO", "numero_entidad": "933", "placa": "58UW2J", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "THERMO", "numero_entidad": "14534", "placa": "50UW1K", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "THERMO", "numero_entidad": "929", "placa": "97UW2J", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "THERMO", "numero_entidad": "1151", "placa": "95UY6G", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "CAJA SECA", "numero_entidad": "153434", "placa": "15UT4H", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "CAJA SECA", "numero_entidad": "7", "placa": "85UZ4D", "status": "activo", "fecha_creacion": datetime.utcnow()},
    {"tipo_caja": "CAJA SECA", "numero_entidad": "1601", "placa": "96UZ6D", "status": "activo", "fecha_creacion": datetime.utcnow()},
]
await db.cajas.insert_many(cajas)


@api_router.get(”/”)
async def root():
return {“message”: “Virgo Transport API v1.0”, “status”: “ok”}

# ============ STARTUP ============

app.include_router(api_router)

app.add_middleware(
CORSMiddleware,
allow_origins=[”*”],
allow_credentials=True,
allow_methods=[”*”],
allow_headers=[”*”],
)

@app.on_event(“startup”)
async def startup_event():
try:
cajas_count = await db.cajas.count_documents({})
if cajas_count == 0:
admin = await db.users.find_one({“username”: “admin”})
if not admin:
await db.users.insert_one({
“username”: “admin”, “nombre”: “Administrador”,
“password”: hash_password(“admin123”), “role”: “admin”,
“created_at”: datetime.utcnow()
})
await seed_catalogs()
print(”[STARTUP] Seed inicial completado”)
else:
print(f”[STARTUP] BD existente con {cajas_count} cajas”)
except Exception as e:
print(f”[STARTUP] Error: {e}”)

@app.on_event(“shutdown”)
async def shutdown():
client.close()