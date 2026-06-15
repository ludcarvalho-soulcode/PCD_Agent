"""
Firestore Service — persistência das empresas e jobs.
Localização: backend/services/firestore_service.py
"""
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore
from google.oauth2 import service_account

# Configuração de ambiente
CREDENCIAIS_PATH = "/home/geova/projeto_pcd/credenciais.json"
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tutores-lms")

_db: Optional[firestore.AsyncClient] = None

# Regex para extração de dados
_ENDERECO_UF_RE = re.compile(r"(?P<cidade>[^,/—-]+?)\s*/\s*(?P<uf>[A-Z]{2})\b")

def _normalizar_texto(valor: Optional[str]) -> str:
    return (valor or "").strip().lower()

def _doc_estado(data: dict) -> str:
    estado = data.get("estado") or data.get("uf")
    if estado:
        return str(estado).strip().upper()
    endereco = str(data.get("endereco") or "")
    match = _ENDERECO_UF_RE.search(endereco)
    return match.group("uf").upper() if match else ""

def _doc_cidade(data: dict) -> str:
    cidade = data.get("cidade")
    if cidade:
        return str(cidade).strip()
    endereco = str(data.get("endereco") or "")
    match = _ENDERECO_UF_RE.search(endereco)
    return match.group("cidade").strip(" -—,") if match else ""

def _texto_busca(data: dict) -> str:
    partes = [
        data.get("razao_social"), data.get("cidade"), data.get("estado"),
        data.get("uf"), data.get("orgao"), data.get("setor"), data.get("endereco"),
    ]
    return " ".join(str(parte or "") for parte in partes).lower()

def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        # Inicialização protegida
        credenciais = service_account.Credentials.from_service_account_file(CREDENCIAIS_PATH)
        _db = firestore.AsyncClient(credentials=credenciais, project=PROJECT_ID, database="agents-internos-pcd")
    return _db

# ──────────────────────────────
# EMPRESAS
# ──────────────────────────────

async def salvar_empresa(empresa: dict) -> str:
    db = get_db()
    doc_id = empresa.get("id") or str(uuid.uuid4())
    empresa["id"] = doc_id
    now = datetime.now(timezone.utc)
    empresa["atualizado_em"] = now
    empresa.setdefault("criado_em", now)

    await db.collection("empresas").document(doc_id).set(empresa, merge=True)
    return doc_id

async def salvar_empresas_batch(empresas: list[dict]) -> list[str]:
    db = get_db()
    ids = []
    batch = db.batch()
    now = datetime.now(timezone.utc)

    for i, emp in enumerate(empresas):
        doc_id = emp.get("id") or str(uuid.uuid4())
        emp["id"] = doc_id
        emp["atualizado_em"] = now
        emp.setdefault("criado_em", now)

        ref = db.collection("empresas").document(doc_id)
        batch.set(ref, emp, merge=True)
        ids.append(doc_id)

        if (i + 1) % 499 == 0:
            await batch.commit()
            batch = db.batch()

    await batch.commit()
    return ids

async def listar_empresas(
    orgao: Optional[str] = None, situacao: Optional[str] = None,
    busca: Optional[str] = None, estado: Optional[str] = None,
    cidade: Optional[str] = None, regiao: Optional[str] = None,
    setor: Optional[str] = None, limite: int = 100,
) -> list[dict]:
    db = get_db()
    query = db.collection("empresas").order_by(
        "oportunidade.score_oportunidade", direction=firestore.Query.DESCENDING
    )

    if orgao: query = query.where("orgao", "==", orgao)
    if situacao: query = query.where("situacao", "==", situacao)

    docs = []
    async for doc in query.stream():
        data = doc.to_dict()
        if busca and _normalizar_texto(busca) not in _texto_busca(data): continue
        if estado and _doc_estado(data) != estado.strip().upper(): continue
        if cidade and _normalizar_texto(cidade) not in _normalizar_texto(_doc_cidade(data)): continue
        if regiao and _normalizar_texto(regiao) != _normalizar_texto(data.get("regiao")): continue
        if setor and _normalizar_texto(setor) != _normalizar_texto(data.get("setor")): continue
        
        docs.append(data)
        if len(docs) >= limite: break
    return docs

async def buscar_empresa(empresa_id: str) -> Optional[dict]:
    doc = await get_db().collection("empresas").document(empresa_id).get()
    return doc.to_dict() if doc.exists else None

async def atualizar_pdf_url(empresa_id: str, pdf_url: str):
    await get_db().collection("empresas").document(empresa_id).update({
        "pdf_url": pdf_url,
        "atualizado_em": datetime.now(timezone.utc),
    })

# ──────────────────────────────
# JOBS
# ──────────────────────────────

async def criar_job(config: dict) -> str:
    db = get_db()
    job_id = str(uuid.uuid4())
    await db.collection("jobs").document(job_id).set({
        "job_id": job_id, "status": "pending", "progresso": 0,
        "log": [], "config": config, "resultado": None,
        "criado_em": datetime.now(timezone.utc),
    })
    return job_id

async def atualizar_job(job_id: str, updates: dict):
    await get_db().collection("jobs").document(job_id).update({
        **updates, "atualizado_em": datetime.now(timezone.utc),
    })

async def get_job(job_id: str) -> Optional[dict]:
    doc = await get_db().collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None

async def append_job_log(job_id: str, mensagem: str):
    await get_db().collection("jobs").document(job_id).update({
        "log": firestore.ArrayUnion([f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {mensagem}"])
    })