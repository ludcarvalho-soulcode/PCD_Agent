"""
Scraper Service v6 — Sem filtro de data ou tamanho
Analisa TACs do MPT e retorna apenas documentos relacionados a PCD
com situação de interesse para prospecção.
"""

import asyncio
import io
import logging
import platform
import re
from typing import Optional, Callable

from playwright.async_api import async_playwright
import pdfplumber

from services.vertex_service import (
    classificar_oportunidade,
    extrair_tacs_do_html,
    enriquecer_empresa,
    buscar_dados_cnpj,
    _eh_documento_tac_pcd,
)

logger = logging.getLogger(__name__)

FONTES = [
    {"id": "prt1-rj", "orgao": "PRT1 — Rio de Janeiro", "regiao": "Sudeste", "url": "https://www.prt1.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt2-sp", "orgao": "PRT2 — São Paulo", "regiao": "Sudeste", "url": "https://www.prt2.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt3-mg", "orgao": "PRT3 — Minas Gerais", "regiao": "Sudeste", "url": "https://www.prt3.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt4-rs", "orgao": "PRT4 — Rio Grande do Sul", "regiao": "Sul", "url": "https://www.prt4.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt5-ba", "orgao": "PRT5 — Bahia", "regiao": "Nordeste", "url": "https://www.prt5.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt6-pe", "orgao": "PRT6 — Pernambuco", "regiao": "Nordeste", "url": "https://www.prt6.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt7-ce", "orgao": "PRT7 — Ceará", "regiao": "Nordeste", "url": "https://www.prt7.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt8-pa", "orgao": "PRT8 — Pará e Amapá", "regiao": "Norte", "url": "https://www.prt8.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt9-pr", "orgao": "PRT9 — Paraná", "regiao": "Sul", "url": "https://www.prt9.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt10-df-to", "orgao": "PRT10 — Distrito Federal e Tocantins", "regiao": "Centro-Oeste", "url": "https://www.prt10.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt11-am-rr", "orgao": "PRT11 — Amazonas e Roraima", "regiao": "Norte", "url": "https://www.prt11.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt12-sc", "orgao": "PRT12 — Santa Catarina", "regiao": "Sul", "url": "https://www.prt12.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt13-pb", "orgao": "PRT13 — Paraíba", "regiao": "Nordeste", "url": "https://www.prt13.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt14-ro-ac", "orgao": "PRT14 — Rondônia e Acre", "regiao": "Norte", "url": "https://www.prt14.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt15-sp", "orgao": "PRT15 — Campinas", "regiao": "Sudeste", "url": "https://www.prt15.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt16-ma", "orgao": "PRT16 — Maranhão", "regiao": "Nordeste", "url": "https://www.prt16.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt17-es", "orgao": "PRT17 — Espírito Santo", "regiao": "Sudeste", "url": "https://www.prt17.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt18-go", "orgao": "PRT18 — Goiás", "regiao": "Centro-Oeste", "url": "https://www.prt18.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt19-al", "orgao": "PRT19 — Alagoas", "regiao": "Nordeste", "url": "https://www.prt19.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt20-se", "orgao": "PRT20 — Sergipe", "regiao": "Nordeste", "url": "https://www.prt20.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt21-rn", "orgao": "PRT21 — Rio Grande do Norte", "regiao": "Nordeste", "url": "https://www.prt21.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt22-pi", "orgao": "PRT22 — Piauí", "regiao": "Nordeste", "url": "https://www.prt22.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt23-mt", "orgao": "PRT23 — Mato Grosso", "regiao": "Centro-Oeste", "url": "https://www.prt23.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
    {"id": "prt24-ms", "orgao": "PRT24 — Mato Grosso do Sul", "regiao": "Centro-Oeste", "url": "https://www.prt24.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"},
]

FONTES_DICT = {fonte["id"]: fonte for fonte in FONTES}

SITUACOES_ALVO = [
    "TAC descumprido",
    "TAC não assinado",
    "ACP ajuizada",
]


def _normalizar_orgao(valor: Optional[str]) -> Optional[str]:
    """
    Aceita:
    - prt1
    - PRT1
    - prt1-rj
    - PRT1 — Rio de Janeiro
    - PRT15 — Campinas
    """
    if not valor:
        return None

    v = valor.strip().lower()

    if not v:
        return None

    if v in FONTES_DICT:
        return v

    for fonte in FONTES:
        if v == fonte["orgao"].lower():
            return fonte["id"]

    match = re.search(r"prt\s*0?(\d+)", v)
    if match:
        numero = int(match.group(1))
        prefixo = f"prt{numero}-"
        encontrados = [
            fonte["id"]
            for fonte in FONTES
            if fonte["id"].startswith(prefixo)
        ]
        if len(encontrados) == 1:
            return encontrados[0]

    return None


def _extrair_texto_pdf(pdf_bytes: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:

            total_paginas = len(pdf.pages)

            texto = ""
            for page in pdf.pages[:15]:
                t = page.extract_text()
                if t:
                    texto += t + "\n"

            logger.info(
                f"PDF carregado | paginas={total_paginas} | chars={len(texto)}"
            )

            return texto[:30000]

    except Exception as e:
        logger.error(f"Erro ao extrair PDF: {e}")
        return ""

def _extrair_prazo_local(texto: str) -> str:
    t = texto.lower()

    m = re.search(r"prazo para o cumprimento desta obrigação[:\s]+([^\n\.]{5,60})", t)
    if m:
        return m.group(1).strip().title()

    m = re.search(
        r"no prazo (?:suplementar )?de (\d+)\s*\([\w\s]+\)\s*(anos?|meses?)[^,\n]*(?:a partir d[ae] (\d{2}/\d{2}/\d{4}))?",
        t,
    )
    if m:
        qtd, unidade, data = m.group(1), m.group(2), m.group(3)
        return f"{qtd} {unidade} a partir de {data}" if data else f"{qtd} {unidade}"

    m = re.search(r"at[eé]\s+(\d{2}/\d{2}/\d{4})", t)
    if m:
        return m.group(1).strip()

    m = re.search(r"(\d+)\s*\([\w\s]+\)\s*semestres?", t)
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


async def _esperar_tabela_carregar(page, log: Callable) -> None:
    """
    Não espera o texto 'Aguarde' sumir, porque em alguns portais ele fica no DOM.
    Espera existir pelo menos uma linha real na tabela.
    """
    await log("   ⏳ Aguardando linhas reais da tabela...")

    try:
        await page.wait_for_function(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('table tbody tr'));
              return rows.some(row => {
                const text = (row.innerText || '').toLowerCase();
                const cells = row.querySelectorAll('td');
                return cells.length >= 4 &&
                       !text.includes('aguarde') &&
                       !text.includes('nenhum registro');
              });
            }
            """,
            timeout=60_000,
        )
        await log("   ✅ Tabela carregada com registros reais.")
    except Exception as wait_err:
        await log(f"   ⚠️ Timeout esperando linhas reais da tabela: {wait_err}")
        await asyncio.sleep(8)

    await asyncio.sleep(2)


async def _raspar_todas_paginas(
    page,
    fonte: dict,
    max_paginas: int,
    log: Callable,
) -> list[dict]:
    registros = []
    procs_vistos = set()

    await log(f"   Abrindo {fonte['url']}")

    for tentativa in range(3):
        try:
            await page.goto(fonte["url"], wait_until="commit", timeout=30_000)
            await page.wait_for_selector("form, table, main, #main-container, body", timeout=15_000)
            await asyncio.sleep(3)

            await log("   ⚡ Clicando em Filtrar para carregar processos...")

            clicou = False
            for sel in ["button:text('Filtrar')", "input[value*='iltrar']", "a:text('Filtrar')"]:
                try:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        clicou = True
                        break
                except Exception:
                    continue

            if not clicou:
                await log("   ⚠️ Botão Filtrar não encontrado. Tentando ler tabela atual.")

            await _esperar_tabela_carregar(page, log)

            try:
                await page.screenshot(path=f"screenshot_{fonte['id']}.png", full_page=True)
                await log(f"   📸 Screenshot salvo: screenshot_{fonte['id']}.png")
            except Exception as ss_err:
                await log(f"   ⚠️ Não conseguiu tirar screenshot: {ss_err}")

            break

        except Exception as e:
            if tentativa == 2:
                await log(f"❌ Todas as 3 tentativas falharam para {fonte['url']}: {e}")
                raise

            await log(f"   Tentativa {tentativa + 1} falhou — aguardando...")
            await asyncio.sleep(5 * (tentativa + 1))

    for num_pag in range(1, max_paginas + 1):
        linhas = await page.query_selector_all("table tbody tr")

        if not linhas:
            await log(f"   Página {num_pag}: sem linhas válidas na tabela")
            break

        await log(f"   Página {num_pag}/{max_paginas}: {len(linhas)} linha(s) detectada(s). Processando...")

        qtd_registros_pagina = 0

        for linha in linhas:
            try:
                texto_linha = (await linha.inner_text()).strip().lower()
                celulas = await linha.query_selector_all("td")

                if (
                    "aguarde" in texto_linha
                    or "nenhum registro" in texto_linha
                    or len(celulas) < 4
                ):
                    continue

                regiao_mpt = (await celulas[0].inner_text()).strip()
                data_str = (await celulas[1].inner_text()).strip()
                numero = (await celulas[2].inner_text()).strip()
                procedimento = (await celulas[3].inner_text()).strip()

                if not procedimento or procedimento in procs_vistos:
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

                registros.append(
                    {
                        "regiao_mpt": regiao_mpt,
                        "data": data_str,
                        "numero": numero,
                        "procedimento": procedimento,
                        "doc_url": doc_url,
                    }
                )
                qtd_registros_pagina += 1

            except Exception as e:
                await log(f"   ⚠️ Erro ao processar linha: {e}")
                continue

        await log(f"   Página {num_pag}: {qtd_registros_pagina} registro(s) válido(s)")

        if num_pag < max_paginas:
            try:
                prox = await page.query_selector(
                    "a:text('Próximo'), .paginate_button.next:not(.disabled), li.next:not(.disabled) a"
                )

                if prox:
                    await prox.click()
                    await asyncio.sleep(4)
                    await _esperar_tabela_carregar(page, log)
                else:
                    break

            except Exception:
                break

    await log(f"   Total coletado: {len(registros)} registro(s)")
    return registros


async def raspar_tacs_pcd(
    orgao: Optional[str] = None,
    orgao_id: Optional[str] = None,
    paginas: int = 10,
    log_callback: Optional[Callable] = None,
) -> list[dict]:
    """
    Compatível com as duas chamadas:
    - raspar_tacs_pcd(orgao="prt1")
    - raspar_tacs_pcd(orgao_id="prt1-rj")
    """

    async def _log(msg: str):
        logger.info(msg)
        if log_callback:
            if asyncio.iscoroutinefunction(log_callback):
                await log_callback(msg)
            else:
                log_callback(msg)

    orgao_recebido = orgao_id or orgao
    orgao_normalizado = _normalizar_orgao(orgao_recebido)

    if orgao_normalizado:
        fontes = [FONTES_DICT[orgao_normalizado]]
    else:
        fontes = list(FONTES)

    await _log(f"orgao recebido = {orgao}")
    await _log(f"orgao_id recebido = {orgao_id}")
    await _log(f"orgao normalizado = {orgao_normalizado}")
    await _log(f"fontes selecionadas = {[f['id'] for f in fontes]}")

    await _log(
        f"Iniciando scraping v6 — {len(fontes)} portal(is) | "
        f"SEM filtro de data/tamanho | apenas PCD descumprido"
    )

    todas_empresas: list[dict] = []
    vistos: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        sistema_operacional = platform.system().lower()

        if "windows" in sistema_operacional:
            ua_dinamico = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        else:
            ua_dinamico = (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )

        context = await browser.new_context(
            user_agent=ua_dinamico,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 800},
            accept_downloads=True,
        )

        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        for fonte in fontes:
            await _log(f"\n-> {fonte['orgao']}")

            page = await context.new_page()

            try:
                registros = await _raspar_todas_paginas(page, fonte, paginas, _log)

                for reg in registros:
                    chave_procedimento = (
                        reg.get("procedimento")
                        or reg.get("numero")
                        or reg.get("doc_url")
                    )

                    if not chave_procedimento:
                        continue

                    if chave_procedimento in vistos:
                        await _log(f"   Ignorado duplicado: {chave_procedimento}")
                        continue

                    vistos.add(chave_procedimento)

                    texto_pdf = ""

                    if reg.get("doc_url"):
                        try:
                            resp = await page.request.get(reg["doc_url"])

                            if resp.ok:
                                pdf_bytes = await resp.body()
                                texto_pdf = _extrair_texto_pdf(pdf_bytes)

                        except Exception as e:
                            await _log(f"   PDF não baixado: {e}")

                    if not texto_pdf:
                        await _log(f"   PDF sem texto extraído: {reg['procedimento']}")
                        continue

                    await _log(
                        f"   Texto extraído ({reg['procedimento']}): "
                        f"{texto_pdf[:500].replace(chr(10), ' ')}"
                    )

                    eh_pcd_regex = _eh_documento_tac_pcd(texto_pdf)
                    if not eh_pcd_regex:
                        await _log(
                            f"⚠️ Sem evidência regex de PCD. "
                            f"Enviando para validação Gemini: {reg['procedimento']}"
                        )

                    try:
                        await _log(f"   Chamando Gemini para {reg['procedimento']}...")

                        empresas_gemini = await extrair_tacs_do_html(
                            texto_pdf,
                            orgao=fonte["orgao"],
                        )

                        await _log(
                            f"   Gemini retornou para {reg['procedimento']}: {empresas_gemini}"
                        )
                    except Exception as e:
                        await _log(f"   Gemini erro: {e}")
                        continue

                    if not empresas_gemini:
                        await _log(f"   Gemini retornou vazio: {reg['procedimento']}")
                        continue

                    await _log(
                        f"   Tipo Gemini: {type(empresas_gemini)} | Valor: {repr(empresas_gemini)[:500]}"
                    )

                    if isinstance(empresas_gemini, dict):
                        empresas_gemini = [empresas_gemini]

                    if not isinstance(empresas_gemini, list):
                        await _log(
                            f"   Aviso: formato retornado pelo Gemini inválido "
                            f"para {reg['procedimento']}"
                        )
                        continue

                    for empresa in empresas_gemini:
                        if isinstance(empresa, list) and empresa:
                            empresa = empresa[0]

                        if not isinstance(empresa, dict):
                            await _log(f"   Item inválido do Gemini: {empresa}")
                            continue

                        motivo_baixo = (empresa.get("motivo") or "").lower()
                        bloqueios_nao_pcd = [
                            "não menciona a cota pcd",
                            "nao menciona a cota pcd",
                            "não menciona especificamente a cota pcd",
                            "nao menciona especificamente a cota pcd",
                            "não menciona a lei 8.213",
                            "nao menciona a lei 8.213",
                            "não é pcd",
                            "nao é pcd",
                            "não se trata de pcd",
                            "nao se trata de pcd",
                            "não de cota pcd",
                            "nao de cota pcd",
                            "não trata de cota pcd",
                            "nao trata de cota pcd",
                            "não trata de pcd",
                            "nao trata de pcd",
                            "trata de liberdade sindical",
                            "trata de segurança",
                            "trata de jornada",
                            "trata de aprendizagem",
                        ]

                        if any(b in motivo_baixo for b in bloqueios_nao_pcd):
                            await _log(
                                f"   Ignorado pelo motivo não-PCD: "
                                f"{empresa.get('razao_social', '?')}"
                            )
                            continue

                        situacao = empresa.get("situacao", "")

                        if not situacao:
                            situacao = _classificar_situacao_local(texto_pdf)
                            empresa["situacao"] = situacao

                        if situacao not in SITUACOES_ALVO and situacao != "TAC descumprido":
                            await _log(
                                f"   Ignorado ({situacao}): "
                                f"{empresa.get('razao_social', '?')}"
                            )
                            continue

                        empresa["orgao"] = fonte["orgao"]
                        empresa["regiao"] = fonte["regiao"]
                        empresa["numero_procedimento"] = reg["procedimento"]
                        empresa["data_abertura"] = reg["data"]
                        empresa["doc_url"] = reg["doc_url"]

                        empresa.setdefault("num_funcionarios", 0)
                        empresa.setdefault("cota_exigida", 0)
                        empresa.setdefault("cota_cumprida", 0)
                        empresa.setdefault("setor", "A identificar")

                        if not empresa.get("prazo_cumprimento"):
                            empresa["prazo_cumprimento"] = _extrair_prazo_local(texto_pdf)

                        cnpj = empresa.get("cnpj") or ""
                        cnpj_limpo = re.sub(r"[^\d]", "", str(cnpj))

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

                                    empresa["porte"] = dados_cnpj.get("porte", "")
                                    empresa["municipio"] = dados_cnpj.get("municipio", "")
                                    empresa["uf"] = dados_cnpj.get("uf", "")

                                    await _log(
                                        f"   Receita: {dados_cnpj.get('porte', '')} — "
                                        f"{dados_cnpj.get('municipio', '')}/{dados_cnpj.get('uf', '')}"
                                    )

                            except Exception:
                                pass

                        if not empresa.get("email") or not empresa.get("telefone"):
                            try:
                                empresa = await enriquecer_empresa(empresa)
                            except Exception:
                                pass

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

                        await _log(
                            f"   OK {empresa.get('razao_social', '?')} — "
                            f"{situacao} — score "
                            f"{empresa['oportunidade']['score_oportunidade']}/10"
                        )

                    await asyncio.sleep(0.5)

            except Exception as e:
                await _log(f"   Erro no portal {fonte['orgao']}: {e}")

            finally:
                await page.close()

        await browser.close()

    await _log(
        f"\nConcluído — {len(todas_empresas)} TAC(s) PCD descumprido(s) encontrado(s)."
    )

    return todas_empresas
