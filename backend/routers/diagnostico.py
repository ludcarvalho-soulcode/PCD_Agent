"""
Router de diagnóstico — testa o Playwright em um portal MPT
e retorna o HTML que ele consegue ver + screenshot base64
"""
from fastapi import APIRouter
from playwright.async_api import async_playwright
import base64, asyncio

router = APIRouter()

@router.get("/testar-portal")
async def testar_portal(url: str = "https://www.prt2.mpt.mp.br/servicos/termos-de-ajuste-de-conduta"):
    resultado = {
        "url": url,
        "status": None,
        "titulo": None,
        "html_resumo": None,
        "botao_filtrar_encontrado": False,
        "tabela_encontrada": False,
        "linhas_tabela": 0,
        "texto_pagina": None,
        "erro": None,
    }

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = await context.new_page()

            # Abre a página
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            resultado["status"] = resp.status if resp else None
            await asyncio.sleep(3)

            resultado["titulo"] = await page.title()

            # Verifica botão filtrar
            for sel in ["input[value*='iltrar']", "button:text('Filtrar')", "a:text('Filtrar')", "[value='Filtrar']"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        resultado["botao_filtrar_encontrado"] = True
                        resultado["seletor_filtrar"] = sel

                        # Clica no filtrar
                        await el.click()
                        await asyncio.sleep(3)
                        break
                except Exception:
                    continue

            # Verifica tabela
            tabela = await page.query_selector("table")
            if tabela:
                resultado["tabela_encontrada"] = True
                linhas = await page.query_selector_all("table tbody tr")
                resultado["linhas_tabela"] = len(linhas)

                # Pega texto das primeiras 3 linhas
                amostras = []
                for linha in linhas[:3]:
                    amostras.append(await linha.inner_text())
                resultado["amostra_linhas"] = amostras

            # Pega texto resumido da página
            from bs4 import BeautifulSoup
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            texto = soup.get_text(separator=" ", strip=True)
            resultado["texto_pagina"] = texto[:2000]
            resultado["html_resumo"] = html[:3000]

            await browser.close()

    except Exception as e:
        resultado["erro"] = str(e)

    return resultado
