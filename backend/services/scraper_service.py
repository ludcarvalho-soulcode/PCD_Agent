"""
Scraper Service — acessa o site MPT-SP e extrai procedimentos TAC/PCD.

Fluxo:
1. GET/POST na página de movimentação de procedimentos
2. Navega pelas páginas de resultados
3. Envia HTML para o Vertex AI (Gemini) analisar e extrair TACs PCD
4. Retorna lista estruturada
"""
import asyncio
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from services.vertex_service import extrair_tacs_do_html, enriquecer_empresa, classificar_oportunidade

logger = logging.getLogger(__name__)

BASE_URL    = "https://www.prt2.mpt.mp.br"
BUSCA_URL   = f"{BASE_URL}/servicos/movimentacao-de-procedimentos"
HEADERS     = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Palavras-chave que identificam TAC relacionado a PCD
TAC_KEYWORDS = [
    "tac", "termo de ajuste", "pcd", "pessoa com deficiência",
    "lei de cotas", "lei 8.213", "art. 93", "portador de deficiência",
    "inclusão", "cota", "reabilitado"
]


def _contem_tac_pcd(texto: str) -> bool:
    t = texto.lower()
    tem_tac = any(k in t for k in ["tac", "termo de ajuste"])
    tem_pcd = any(k in t for k in ["pcd", "pessoa com defici", "lei 8.213", "cota", "art. 93"])
    return tem_tac and tem_pcd


async def _fetch_page(client: httpx.AsyncClient, url: str, params: dict = None) -> str:
    """Busca o HTML de uma página com retry."""
    for attempt in range(3):
        try:
            resp = await client.get(url, params=params, headers=HEADERS, timeout=30.0)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


async def _buscar_com_filtro(
    client: httpx.AsyncClient,
    tipo_procedimento: str = "TAC",
    assunto: str = "PCD",
    pagina: int = 1,
) -> str:
    """
    Tenta buscar procedimentos usando os parâmetros de query do site MPT.
    O site pode usar GET com query string ou POST com form data.
    """
    params = {
        "tipo":    tipo_procedimento,
        "assunto": assunto,
        "page":    pagina,
        "q":       "PCD deficiência cotas",
    }
    try:
        return await _fetch_page(client, BUSCA_URL, params=params)
    except Exception:
        # Fallback: página sem filtros
        return await _fetch_page(client, BUSCA_URL)


def _limpar_html(html: str) -> str:
    """Remove scripts, estilos e menus para reduzir tokens enviados ao Gemini."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    # Foca no conteúdo principal
    main = soup.find("main") or soup.find(id="conteudo") or soup.find(class_="conteudo")
    if main:
        return str(main)
    return soup.get_text(separator="\n", strip=True)[:20000]


async def raspar_tacs_pcd(
    orgao: Optional[str] = None,
    paginas: int = 5,
    log_callback=None,
) -> list[dict]:
    """
    Função principal de scraping.
    Percorre as páginas do MPT e usa Gemini para extrair TACs PCD.
    """
    def _log(msg: str):
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    todas_empresas: list[dict] = []
    vistos: set[str] = set()  # CNPJ já processados

    _log(f"Iniciando scraping — {paginas} páginas | órgão: {orgao or 'todos'}")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for pagina in range(1, paginas + 1):
            _log(f"Raspando página {pagina}/{paginas}...")

            try:
                html = await _buscar_com_filtro(client, pagina=pagina)
                html_limpo = _limpar_html(html)

                _log(f"  → {len(html_limpo)} chars de conteúdo extraído. Enviando ao Gemini...")

                empresas = await extrair_tacs_do_html(html_limpo, orgao=orgao or "")
                _log(f"  → Gemini identificou {len(empresas)} TAC(s) PCD nesta página")

                for emp in empresas:
                    cnpj = emp.get("cnpj", "")
                    if cnpj and cnpj in vistos:
                        continue
                    if cnpj:
                        vistos.add(cnpj)

                    # Filtra por órgão se informado
                    if orgao and orgao.lower() not in (emp.get("orgao") or "").lower():
                        continue

                    # Enriquece dados faltantes
                    if not emp.get("email") or not emp.get("telefone"):
                        _log(f"  → Enriquecendo dados de: {emp.get('razao_social', '?')}")
                        emp = await enriquecer_empresa(emp)

                    # Classifica oportunidade
                    oportunidade = await classificar_oportunidade(emp)
                    emp["oportunidade"] = oportunidade

                    todas_empresas.append(emp)

                await asyncio.sleep(1.5)  # Respeita rate-limit do site

            except Exception as e:
                _log(f"  ⚠ Erro na página {pagina}: {e}")
                continue

    _log(f"Scraping concluído. Total: {len(todas_empresas)} empresas com TAC PCD.")
    return todas_empresas
