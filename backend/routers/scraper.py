from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import ScraperJobRequest, ScraperJobResponse
from services import firestore_service as fs
from services import scraper_service, storage_service

router = APIRouter()


async def _executar_job(job_id: str, req: ScraperJobRequest):
    try:
        await fs.atualizar_job(job_id, {"status": "running", "progresso": 5})

        # Mudamos para async def para sincronizar perfeitamente com o scraper_service
        async def _log(msg: str):
            await fs.append_job_log(job_id, msg)

        # --- TRATAMENTO DO ÓRGÃO PARA EVITAR ZERO TACS ---
        orgao_tratado = req.orgao.strip().lower() if req.orgao else "todos"

        # Mapeamento de conveniência: se digitar variações de SP, joga pro termo que o scraper aceita
       # --- TRATAMENTO DO ÓRGÃO PARA EVITAR ZERO TACS ---
        orgao_tratado = req.orgao.strip().lower() if req.orgao else "todos"

        # Mapeamento de conveniência: se digitar variações de SP, joga pro termo que o scraper aceita
        if orgao_tratado in ["mpt-sp", "mpt_sp", "sao paulo", "sp"]:
            # CORRIGIDO: Agora aponta para o portal de São Paulo (PRT2)
            orgao_tratado = "prt2"

        # Agora usamos await já que o _log virou assíncrono
        await _log(
            f"Orgao original recebido: '{req.orgao}' -> tratado para: '{orgao_tratado}'"
        )
        # -------------------------------------------------

        empresas = await scraper_service.raspar_tacs_pcd(
            orgao=orgao_tratado,  # <--- Passa o órgão limpo e convertido
            paginas=req.paginas,
            validar_gemini_sem_regex=req.forcar,
            buscar_contatos=req.buscar_contatos or req.forcar,
            persist_callback=fs.salvar_empresa,
            log_callback=_log,  # <--- Enviamos nossa função async tratada
        )
        await fs.atualizar_job(job_id, {"progresso": 60})

        if not empresas:
            await fs.atualizar_job(
                job_id,
                {
                    "status": "done",
                    "progresso": 100,
                    "resultado": {
                        "total": 0,
                        "mensagem": f"Nenhum TAC PCD encontrado para o órgão {orgao_tratado}.",
                    },
                },
            )
            return

        ids = [emp.get("id") for emp in empresas if emp.get("id")]
        if len(ids) != len(empresas):
            ids = await fs.salvar_empresas_batch(empresas)
        await fs.atualizar_job(job_id, {"progresso": 80})

        try:
            pdf_url = await storage_service.gerar_e_salvar_relatorio(
                empresas, job_id
            )
        except Exception as e:
            await _log(f"Erro ao gerar PDF: {str(e)}")
            pdf_url = None

        await fs.atualizar_job(
            job_id,
            {
                "status": "done",
                "progresso": 100,
                "resultado": {
                    "total": len(empresas),
                    "ids": ids,
                    "pdf_url": pdf_url,
                },
            },
        )

    except Exception as e:
        await fs.atualizar_job(
            job_id,
            {
                "status": "error",
                "progresso": 0,
                "resultado": {"erro": str(e)},
            },
        )


@router.post("/run", response_model=ScraperJobResponse)
async def iniciar_scraping(
    req: ScraperJobRequest, background_tasks: BackgroundTasks
):
    # CRIAÇÃO NO FIRESTORE
    job_id = await fs.criar_job(req.dict())
    # DISPARO EM BACKGROUND
    background_tasks.add_task(_executar_job, job_id, req)

    return ScraperJobResponse(
        job_id=job_id,
        status="pending",
        message="Job criado. Use GET /api/scraper/status/{job_id} para acompanhar.",
    )


@router.get("/status/{job_id}")
async def status_job(job_id: str):
    job = await fs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job
