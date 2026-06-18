"""
Firestore Service — persistência das empresas e jobs.

Coleções:
  empresas/       → documentos de empresas com TAC PCD
  jobs/           → status de execuções do agente

Estratégia de upsert:
  Chave de deduplicação: CNPJ (14 dígitos) ou numero_procedimento
  Se já existe documento com a mesma chave → atualiza (merge)
  Se não existe → cria novo documento
  Isso evita duplicatas quando o agente executa múltiplas vezes
"""
import os

# Define o caminho correto para o arquivo de credenciais que está na raiz
CREDENCIAIS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "credenciais.json")
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional
from google.oauth2 import service_account

from google.cloud import firestore

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tutores-lms")

_db: Optional[firestore.AsyncClient] = None

def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        # Inicialização protegida
        credenciais = service_account.Credentials.from_service_account_file(CREDENCIAIS_PATH)
        _db = firestore.AsyncClient(credentials=credenciais, project=PROJECT_ID, database="agents-internos-pcd")
    return _db
    
def _chave_empresa(empresa: dict) -> str:
    """
    Gera chave única para deduplicação.
    Prioridade: CNPJ limpo → numero_procedimento → uuid novo
    """
    cnpj = re.sub(r"[^\d]", "", empresa.get("cnpj") or "")
    if len(cnpj) == 14 and cnpj != "00000000000000":
        return f"cnpj_{cnpj}"

    proc = (empresa.get("numero_procedimento") or "").strip()
    if proc and proc != "—":
        proc_limpo = re.sub(r"[^\w]", "_", proc)
        return f"proc_{proc_limpo}"

    return str(uuid.uuid4())


# ── EMPRESAS ───────────────────────────────────────────────────────

async def salvar_empresa(empresa: dict) -> str:
    """Salva ou atualiza uma empresa (upsert por CNPJ ou procedimento)."""
    db  = get_db()
    now = datetime.utcnow()

    chave  = _chave_empresa(empresa)
    doc_id = empresa.get("id") or chave

    empresa["id"]           = doc_id
    empresa["atualizado_em"]= now
    empresa.setdefault("criado_em", now)

    await db.collection("empresas").document(doc_id).set(empresa, merge=True)
    return doc_id


async def salvar_empresas_batch(empresas: list[dict]) -> list[str]:
    """
    Salva/atualiza lista de empresas em batch.
    Usa upsert — se empresa já existe (mesmo CNPJ ou procedimento) atualiza.
    Retorna lista de IDs salvos.
    """
    db  = get_db()
    now = datetime.utcnow()
    ids = []
    novos = 0
    atualizados = 0

    batch = db.batch()
    ops   = 0

    for emp in empresas:
        chave  = _chave_empresa(emp)
        doc_id = emp.get("id") or chave

        # Verifica se já existe
        ref = db.collection("empresas").document(doc_id)
        doc = await ref.get()

        if doc.exists:
            # Atualiza — preserva criado_em original
            dados_existentes = doc.to_dict()
            emp["id"]            = doc_id
            emp["criado_em"]     = dados_existentes.get("criado_em", now)
            emp["atualizado_em"] = now
            atualizados += 1
        else:
            # Novo documento
            emp["id"]            = doc_id
            emp["criado_em"]     = now
            emp["atualizado_em"] = now
            novos += 1

        batch.set(ref, emp, merge=True)
        ids.append(doc_id)
        ops += 1

        # Firestore batch suporta até 500 ops por vez
        if ops % 499 == 0:
            await batch.commit()
            batch = db.batch()
            ops   = 0

    if ops > 0:
        await batch.commit()

    logger.info(f"Firestore: {novos} novo(s) + {atualizados} atualizado(s) = {len(ids)} total")
    return ids


async def listar_empresas(
    orgao:    Optional[str] = None,
    situacao: Optional[str] = None,
    busca:    Optional[str] = None,
    limite:   int           = 200,
) -> list[dict]:
    db    = get_db()
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
        if busca:
            if busca.lower() not in (data.get("razao_social") or "").lower():
                continue
        docs.append(data)

    return docs


async def buscar_empresa(empresa_id: str) -> Optional[dict]:
    db  = get_db()
    doc = await db.collection("empresas").document(empresa_id).get()
    return doc.to_dict() if doc.exists else None


async def atualizar_pdf_url(empresa_id: str, pdf_url: str):
    db = get_db()
    await db.collection("empresas").document(empresa_id).update({
        "pdf_url":       pdf_url,
        "atualizado_em": datetime.utcnow(),
    })


# ── JOBS ───────────────────────────────────────────────────────────

async def criar_job(config: dict) -> str:
    db     = get_db()
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
    db  = get_db()
    doc = await db.collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


async def append_job_log(job_id: str, mensagem: str):
    db = get_db()
    await db.collection("jobs").document(job_id).update({
        "log": firestore.ArrayUnion(
            [f"[{datetime.utcnow().strftime('%H:%M:%S')}] {mensagem}"]
        )
    })

