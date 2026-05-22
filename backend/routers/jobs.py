"""Router: /api/jobs"""
from fastapi import APIRouter, HTTPException
from services import firestore_service as fs

router = APIRouter()


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await fs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job
