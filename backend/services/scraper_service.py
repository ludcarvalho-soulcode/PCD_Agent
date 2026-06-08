"""
Scraper Service v6 — Sem filtro de data ou tamanho
Analisa TODOS os TACs, traz apenas PCD descumprido ou não assinado
"""
import asyncio
import io
import logging
import re
from typing import Optional, Callable

from playwright.async_api import async_playwright
import pdfplumber

from services.vertex_service import classificar_oportunidade, extrair_tacs_do_html, enriquecer_empresa, buscar_dados_cnpj

logger = logging.getLogger(__name__)

FONTES = [
    {"id":"prt2-sp",  "orgao":"PRT2 — São Paulo",         "regiao":"Sudeste",  "url":"https://www.prt2.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt1-rj",  "orgao":"PRT1 — Rio de Janeiro",    "regiao":"Sudeste",  "url":"https://www.prt1.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt3-mg",  "orgao":"PRT3 — Minas Gerais",      "regiao":"Sudeste",  "url":"https://www.prt3.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt4-rs",  "orgao":"PRT4 — Rio Grande do Sul", "regiao":"Sul",      "url":"https://www.prt4.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt6-pe",  "orgao":"PRT6 — Pernambuco",        "regiao":"Nordeste", "url":"https://www.prt6.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt9-pr",  "orgao":"PRT9 — Paraná",            "regiao":"Sul",      "url":"https://www.prt9.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id":"prt15-sp", "orgao":"PRT15 — Campinas",         "regiao":"Sudeste",  "url":"https://www.prt15.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
]

# Situações de interesse para prospecção
SITUACOES_ALVO = ["TAC descumprido", "TAC não assinado", "ACP ajuizada"]


def _extrair_texto_pdf(pdf_bytes: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texto = ""
            for page in pdf.pages[:6]:
                t = page.extract_text()
                if t:
                    texto += t + "\n"
            return texto[:12000]
    except Exception:
        return ""


def _extrair_prazo_local(texto: str) -> str:
    t = texto.lower()
    m = re.search(r'prazo para o cumprimento desta obriga[çc][aã]o[:\s]+([^\n\.]{5,60})', t)
    if m:
        return m.group(1).strip().title()
    m = re.search(r'no prazo (?:suplementar )?de (\d+)\s*\([\w\s]+\)\s*(anos?|meses?)[^,\n]*(?:a partir d[ae] (\d{2}/\d{2}/\d{4}))?', t)
    if m:
        qtd, unidade, data = m.group(1), m.group(2), m.group(3)
        return f"{qtd} {unidade} a partir de {data}" if data else f"{qtd} {unidade}"
    m = re.search(r'at[eé]\s+(\d{2}/\d{2}/\d{4})', t)
    if m:
        return m.group(1).strip()
    m = re.search(r'(\d+)\s*\([\w\s]+\)\s*semestres?', t)
    if m:
        return f"{m.group(1)} semestres"
    return ""


def _classificar_situacao_local(texto: str) -> str:
    t = texto.lower()
    if any(k in t for k in ["descumprido", "descumpriu", "inadimplente", "nao cumpriu", "não cumpriu"]):
        return "TAC descumprido"
    if any(k in t for k in ["cumprimento total", "integralmente cumprido", "arquivado", "encerrado"]):
        return "TAC cumprido"
    return "TAC em cumprimento"


async def _raspar_todas_paginas(page, fonte: dict, max_paginas: int, log: Callable) -> list[dict]:
    """Raspa TODAS as páginas sem filtro de data ou tamanho."""
    registros = []
    procs_vistos = set()

    log(f"  Abrindo {fonte['url']}")
    for tentativa in range(3):
        try:
            await page.goto(fonte["url"], wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            break
        except Exception as e:
            if tentativa == 2:
                raise
            log(f"  Tentativa {tentativa+1} falhou — aguardando...")
            await asyncio.sleep(4)

    # Tenta selecionar 100 por página
    for sel_100 in ["select[name='DataTables_Table_0_length']", "select[name*='length']"]:
        try:
            await page.select_option(sel_100, "100")
            await asyncio.sleep(1)
            break
        except Exception:
            continue

    # Clica em Filtrar sem preencher nada
    for sel in ["button:text('Filtrar')", "input[value*='iltrar']", "a:text('Filtrar')"]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await asyncio.sleep(3)
                break
        except Exception:
            continue

    for num_pag in range(1, max_paginas + 1):
        linhas = await page.query_selector_all("table tbody tr")
        if not linhas:
            log(f"  Página {num_pag}: sem linhas")
            break

        log(f"  Página {num_pag}/{max_paginas}: {len(linhas)} registro(s)")

        for linha in linhas:
            try:
                celulas = await linha.query_selector_all("td")
                if len(celulas) < 4:
                    continue

                regiao_mpt   = (await celulas[0].inner_text()).strip()
                data_str     = (await celulas[1].inner_text()).strip()
                numero       = (await celulas[2].inner_text()).strip()
                procedimento = (await celulas[3].inner_text()).strip()

                if procedimento in procs_vistos:
                    continue
                procs_vistos.add(procedimento)

                doc_url = ""
                if len(celulas) >= 5:
                    link = await celulas[4].query_selector("a")
                    if link:
                        href = await link.get_attribute("href") or ""
                        if href.startswith("/"):
                            base = fonte["url"].split("/servicos")[0]
                            doc_url = base + href
                        elif href.startswith("http"):
                            doc_url = href

                registros.append({
                    "regiao_mpt":   regiao_mpt,
                    "data":         data_str,
                    "numero":       numero,
                    "procedimento": procedimento,
                    "doc_url":      doc_url,
                })
            except Exception:
                continue

        # Próxima página
        if num_pag < max_paginas:
            try:
                prox = await page.query_selector(
                    "a:text('Próximo'), .paginate_button.next:not(.disabled), li.next:not(.disabled) a"
                )
                if prox:
                    await prox.click()
                    await asyncio.sleep(2)
                else:
                    log(f"  Sem próxima página")
                    break
            except Exception:
                break

    log(f"  Total coletado: {len(registros)} registro(s)")
    return registros


async def raspar_tacs_pcd(
    orgao: Optional[str] = None,
    paginas: int = 10,
    log_callback: Optional[Callable] = None,
) -> list[dict]:

    def _log(msg: str):
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    fontes = FONTES
    if orgao:
        fontes = [f for f in FONTES if orgao.lower() in f["orgao"].lower()]

    _log(f"Iniciando scraping v6 — {len(fontes)} portal(is) | SEM filtro de data/tamanho | apenas PCD descumprido")

    todas_empresas: list[dict] = []
    vistos: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        for fonte in fontes:
            _log(f"\n-> {fonte['orgao']}")
            page = await context.new_page()

            try:
                registros = await _raspar_todas_paginas(page, fonte, paginas, _log)

                for reg in registros:
                    if reg["procedimento"] in vistos:
                        continue

                    # Baixa o PDF
                    texto_pdf = ""
                    if reg["doc_url"]:
                        try:
                            resp = await page.request.get(reg["doc_url"])
                            if resp.ok:
                                pdf_bytes = await resp.body()
                                texto_pdf = _extrair_texto_pdf(pdf_bytes)
                        except Exception as e:
                            _log(f"  PDF nao baixado: {e}")

                    if not texto_pdf:
                        continue

                    # Gemini analisa — retorna vazio se não for PCD
                    try:
                        empresas_gemini = await extrair_tacs_do_html(texto_pdf, orgao=fonte["orgao"])
                    except Exception as e:
                        _log(f"  Gemini erro: {e}")
                        continue

                    if not empresas_gemini:
                        # Não é TAC PCD — ignora
                        continue

                    empresa = empresas_gemini[0]

                    # FILTRO PRINCIPAL: só traz descumprido ou não assinado
                    situacao = empresa.get("situacao", "")
                    if not situacao:
                        situacao = _classificar_situacao_local(texto_pdf)
                        empresa["situacao"] = situacao

                    if situacao not in SITUACOES_ALVO and situacao != "TAC descumprido":
                        _log(f"  Ignorado ({situacao}): {empresa.get('razao_social','?')}")
                        continue

                    vistos.add(reg["procedimento"])

                    # Metadados
                    empresa["orgao"]               = fonte["orgao"]
                    empresa["regiao"]              = fonte["regiao"]
                    empresa["numero_procedimento"] = reg["procedimento"]
                    empresa["data_abertura"]       = reg["data"]
                    empresa["doc_url"]             = reg["doc_url"]
                    empresa.setdefault("num_funcionarios", 0)
                    empresa.setdefault("cota_exigida",     0)
                    empresa.setdefault("cota_cumprida",    0)
                    empresa.setdefault("setor",            "A identificar")

                    # Prazo local como fallback
                    if not empresa.get("prazo_cumprimento"):
                        empresa["prazo_cumprimento"] = _extrair_prazo_local(texto_pdf)

                    # Enriquece com BrasilAPI
                    cnpj = empresa.get("cnpj", "")
                    cnpj_limpo = re.sub(r"[^\d]", "", cnpj)
                    if cnpj_limpo and len(cnpj_limpo) == 14 and cnpj_limpo != "00000000000000":
                        try:
                            dados_cnpj = await buscar_dados_cnpj(cnpj)
                            if dados_cnpj:
                                if dados_cnpj.get("razao_social_oficial"):
                                    empresa["razao_social"] = dados_cnpj["razao_social_oficial"]
                                if dados_cnpj.get("endereco_receita"):
                                    empresa["endereco"] = dados_cnpj["endereco_receita"]
                                if not empresa.get("telefone") and dados_cnpj.get("telefone_receita"):
                                    empresa["telefone"] = dados_cnpj["telefone_receita"]
                                if not empresa.get("num_funcionarios") or empresa["num_funcionarios"] == 0:
                                    empresa["num_funcionarios"] = dados_cnpj.get("num_funcionarios_estimado", 0)
                                empresa["porte"]     = dados_cnpj.get("porte", "")
                                empresa["municipio"] = dados_cnpj.get("municipio", "")
                                empresa["uf"]        = dados_cnpj.get("uf", "")
                                _log(f"  Receita: {dados_cnpj.get('porte','')} — {dados_cnpj.get('municipio','')}/{dados_cnpj.get('uf','')}")
                        except Exception:
                            pass

                    # Enriquece contato faltante
                    if not empresa.get("email") or not empresa.get("telefone"):
                        try:
                            empresa = await enriquecer_empresa(empresa)
                        except Exception:
                            pass

                    # Score
                    try:
                        empresa["oportunidade"] = await classificar_oportunidade(empresa)
                    except Exception:
                        empresa["oportunidade"] = {
                            "score_oportunidade": 8,
                            "nivel": "Alta",
                            "deficit_pcd": 0,
                            "recomendacao": "TAC descumprido. Alta prioridade para prospecção.",
                            "perfis_sugeridos": [],
                        }

                    todas_empresas.append(empresa)
                    _log(f"  OK {empresa.get('razao_social','?')} — {situacao} — score {empresa['oportunidade']['score_oportunidade']}/10")
                    await asyncio.sleep(0.5)

            except Exception as e:
                _log(f"  Erro no portal {fonte['orgao']}: {e}")
            finally:
                await page.close()

        await browser.close()

    _log(f"\nConcluido — {len(todas_empresas)} TAC(s) PCD descumprido(s) encontrado(s).")
    return todas_empresas
