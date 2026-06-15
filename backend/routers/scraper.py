from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import ScraperJobRequest, ScraperJobResponse
from services import firestore_service as fs
from services import scraper_service, storage_service

router = APIRouter()

async def _executar_job(job_id: str, req: ScraperJobRequest):
    try:
        await fs.atualizar_job(job_id, {"status": "running", "progresso": 5})

        def _log(msg: str):
            # Usamos o loop do evento para garantir a escrita assíncrona
            import asyncio
            asyncio.create_task(fs.append_job_log(job_id, msg))

        empresas = await scraper_service.raspar_tacs_pcd(
            orgao=req.orgao,
            paginas=req.paginas,
            log_callback=_log,
        )
        await fs.atualizar_job(job_id, {"progresso": 60})

        if not empresas:
            await fs.atualizar_job(job_id, {
                "status": "done", "progresso": 100,
                "resultado": {"total": 0, "mensagem": "Nenhum TAC PCD encontrado."},
            })
            return

        ids = await fs.salvar_empresas_batch(empresas)
        await fs.atualizar_job(job_id, {"progresso": 80})

        try:
            pdf_url = await storage_service.gerar_e_salvar_relatorio(empresas, job_id)
        except Exception as e:
            await _log(f"Erro ao gerar PDF: {str(e)}")
            pdf_url = None

        await fs.atualizar_job(job_id, {
            "status": "done", "progresso": 100,
            "resultado": {"total": len(empresas), "ids": ids, "pdf_url": pdf_url},
        })

    except Exception as e:
        await fs.atualizar_job(job_id, {
            "status": "error", "progresso": 0,
            "resultado": {"erro": str(e)},
        })

@router.post("/run", response_model=ScraperJobResponse)
async def iniciar_scraping(req: ScraperJobRequest, background_tasks: BackgroundTasks):
    # CRIAÇÃO NO FIRESTORE
    job_id = await fs.criar_job(req.dict())
    # DISPARO EM BACKGROUND
    background_tasks.add_task(_executar_job, job_id, req)
    
    return ScraperJobResponse(
        job_id=job_id, 
        status="pending",
        message="Job criado. Use GET /api/scraper/status/{job_id} para acompanhar."
    )

@router.get("/status/{job_id}")
async def status_job(job_id: str):
    job = await fs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job