"""Router: /api/empresas"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from services import firestore_service as fs
from services import storage_service

router = APIRouter()


@router.get("/")
async def listar(
    orgao:    Optional[str] = None,
    situacao: Optional[str] = None,
    busca:    Optional[str] = None,
    estado:   Optional[str] = None,
    cidade:   Optional[str] = None,
    regiao:   Optional[str] = None,
    setor:    Optional[str] = None,
    limite:   int = 100,
):
    return await fs.listar_empresas(orgao=orgao, situacao=situacao,
                                    busca=busca, estado=estado,
                                    cidade=cidade, regiao=regiao,
                                    setor=setor, limite=limite)


@router.get("/{empresa_id}")
async def buscar(empresa_id: str):
    emp = await fs.buscar_empresa(empresa_id)
    if not emp:
        raise HTTPException(404, "Empresa não encontrada")
    return emp


@router.post("/{empresa_id}/pdf")
async def gerar_pdf(empresa_id: str):
    emp = await fs.buscar_empresa(empresa_id)
    if not emp:
        raise HTTPException(404, "Empresa não encontrada")
    url = await storage_service.gerar_e_salvar_pdf_empresa(emp)
    await fs.atualizar_pdf_url(empresa_id, url)
    return {"pdf_url": url}
