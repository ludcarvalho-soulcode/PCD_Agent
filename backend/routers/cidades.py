"""Router de cidades — integra com IBGE e fallback local"""
from fastapi import APIRouter, HTTPException
import json
import os
import httpx

router = APIRouter()

# CORREÇÃO: Ajustado para referenciar a partir da pasta 'backend/'
# ou da raiz, dependendo de como você organizou seu 'cidades_por_uf.json'.
# Se o arquivo estiver na raiz do projeto (projeto_pcd/):
CIDADES_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'cidades_por_uf.json')
# Se o arquivo estiver dentro da pasta 'backend/':
# CIDADES_PATH = os.path.join(os.path.dirname(__file__), '..', 'cidades_por_uf.json')

IBGE_API = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

# Cache em memória
CIDADES_CACHE = {}

@router.get("/cidades/{uf}")
def get_cidades_por_uf(uf: str):
    uf = uf.upper()
    if uf in CIDADES_CACHE:
        return {"uf": uf, "cidades": CIDADES_CACHE[uf]}
    
    # Tenta buscar do IBGE
    try:
        url = IBGE_API.format(uf=uf)
        resp = httpx.get(url, timeout=5.0)
        if resp.status_code == 200:
            cidades = [c['nome'] for c in resp.json()]
            if cidades:
                cidades_sorted = sorted(cidades)
                CIDADES_CACHE[uf] = cidades_sorted
                return {"uf": uf, "cidades": cidades_sorted}
    except Exception:
        pass 

    # Fallback: arquivo local
    try:
        with open(CIDADES_PATH, encoding='utf-8') as f:
            cidades_por_uf = json.load(f)
        cidades = cidades_por_uf.get(uf)
        if not cidades:
            raise HTTPException(status_code=404, detail="UF não encontrada")
        return {"uf": uf, "cidades": cidades}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Arquivo de cidades local não encontrado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))