"""
Firestore Service — persistência das empresas e jobs.

Coleções:
  empresas/       → documentos de empresas com TAC PCD
  jobs/           → status de execuções do agente
"""
import os
import uuid
from datetime import datetime
from typing import Optional

from google.cloud import firestore

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "seu-projeto-gcp")

_db: Optional[firestore.AsyncClient] = None


def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=PROJECT_ID)
    return _db


# ──────────────────────────────
# EMPRESAS
# ──────────────────────────────

async def salvar_empresa(empresa: dict) -> str:
    db = get_db()
    doc_id = empresa.get("id") or str(uuid.uuid4())
    empresa["id"] = doc_id
    empresa["atualizado_em"] = datetime.utcnow()
    if "criado_em" not in empresa:
        empresa["criado_em"] = datetime.utcnow()

    await db.collection("empresas").document(doc_id).set(empresa, merge=True)
    return doc_id


async def salvar_empresas_batch(empresas: list[dict]) -> list[str]:
    db = get_db()
    ids = []
    batch = db.batch()

    for emp in empresas:
        doc_id = emp.get("id") or str(uuid.uuid4())
        emp["id"] = doc_id
        emp["atualizado_em"] = datetime.utcnow()
        if "criado_em" not in emp:
            emp["criado_em"] = datetime.utcnow()

        ref = db.collection("empresas").document(doc_id)
        batch.set(ref, emp, merge=True)
        ids.append(doc_id)

        # Firestore batch suporta até 500 ops
        if len(ids) % 499 == 0:
            await batch.commit()
            batch = db.batch()

    await batch.commit()
    return ids


async def listar_empresas(
    orgao:   Optional[str] = None,
    situacao: Optional[str] = None,
    busca:   Optional[str] = None,
    limite:  int = 100,
) -> list[dict]:
    db = get_db()
    query = db.collection("empresas").order_by(
        "oportunidade.score_oportunidade", direction=firestore.Query.DESCENDING
    ).limit(limite)

    if orgao:
        query = query.where("orgao", "==", orgao)
    if situacao:
        query = query.where("situacao", "==", situacao)

    docs = []
    async for doc in query.stream():
        data = doc.to_dict()
        # Filtro de busca por nome (Firestore não suporta LIKE, fazemos client-side)
        if busca:
            if busca.lower() not in (data.get("razao_social") or "").lower():
                continue
        docs.append(data)

    return docs


async def buscar_empresa(empresa_id: str) -> Optional[dict]:
    db = get_db()
    doc = await db.collection("empresas").document(empresa_id).get()
    return doc.to_dict() if doc.exists else None


async def atualizar_pdf_url(empresa_id: str, pdf_url: str):
    db = get_db()
    await db.collection("empresas").document(empresa_id).update({
        "pdf_url": pdf_url,
        "atualizado_em": datetime.utcnow(),
    })


# ──────────────────────────────
# JOBS
# ──────────────────────────────

async def criar_job(config: dict) -> str:
    db = get_db()
    job_id = str(uuid.uuid4())
    await db.collection("jobs").document(job_id).set({
        "job_id":    job_id,
        "status":    "pending",
        "progresso": 0,
        "log":       [],
        "config":    config,
        "resultado": None,
        "criado_em": datetime.utcnow(),
    })
    return job_id


async def atualizar_job(job_id: str, updates: dict):
    db = get_db()
    await db.collection("jobs").document(job_id).update({
        **updates,
        "atualizado_em": datetime.utcnow(),
    })


async def get_job(job_id: str) -> Optional[dict]:
    db = get_db()
    doc = await db.collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


async def append_job_log(job_id: str, mensagem: str):
    db = get_db()
    await db.collection("jobs").document(job_id).update({
        "log": firestore.ArrayUnion([f"[{datetime.utcnow().strftime('%H:%M:%S')}] {mensagem}"])
    })
