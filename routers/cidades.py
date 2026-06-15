from fastapi import APIRouter, HTTPException
import json
import os
import httpx

router = APIRouter()

CIDADES_PATH = os.path.join(os.path.dirname(__file__), '..', 'cidades_por_uf.json')
IBGE_API = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

# Cache em memória para cidades do IBGE
CIDADES_CACHE = {}

@router.get("/cidades/{uf}")
def get_cidades_por_uf(uf: str):
    uf = uf.upper()
    # Tenta buscar do cache
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
        pass  # Se falhar, cai para o fallback
    # Fallback: arquivo local
    try:
        with open(CIDADES_PATH, encoding='utf-8') as f:
            cidades_por_uf = json.load(f)
        cidades = cidades_por_uf.get(uf)
        if not cidades:
            raise HTTPException(status_code=404, detail="UF não encontrada")
        return {"uf": uf, "cidades": cidades}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
