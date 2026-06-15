"""Router: /api/jobs"""
from fastapi import APIRouter, HTTPException
# CORREÇÃO: Importação corrigida para o caminho absoluto a partir da raiz do projeto
from backend.services import firestore_service as fs

router = APIRouter()

@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await fs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job