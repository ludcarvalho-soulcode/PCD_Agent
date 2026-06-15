"""Router: /api/relatorios"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
# CORREÇÃO: Importação absoluta corrigida para o pacote 'backend'
from backend.services import firestore_service as fs
from backend.services import storage_service

router = APIRouter()


@router.post("/consolidado")
async def relatorio_consolidado(
    orgao:    str | None = None,
    situacao: str | None = None,
):
    empresas = await fs.listar_empresas(orgao=orgao, situacao=situacao, limite=500)
    buf = storage_service.gerar_relatorio_consolidado(empresas)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=relatorio_tac_pcd.pdf"},
    )