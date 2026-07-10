"""
Contact Scraper Service
Busca contatos reais de empresas via:
1. DuckDuckGo Instant Answer API (JSON) — mais estável que HTML scraping
2. Site oficial da empresa (Playwright) — raspagem de páginas de contato/RH
"""
import asyncio
import base64
import logging
import re
import unicodedata
import urllib.parse
from typing import Optional

import httpx
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)
RE_TELEFONE = re.compile(
    r"(?<!\d)(?:\+55[ \t]?)?\(?\d{2}\)?[ \t]?9?\d{4}[ \t\-]?\d{4}(?!\d)",
    re.IGNORECASE
)
RE_TELEFONE_SERVICO = re.compile(
    r"(?<!\d)(?:0800[ \t.\-]?\d{3}[ \t.\-]?\d{4}|(?:3003|3004|4003|4004)[ \t.\-]?\d{4})(?!\d)"
)

EMAILS_IGNORAR = {
    "noreply", "no-reply", "donotreply", "webmaster", "postmaster",
    "example@", "test@", "sentry", "rollbar", "newrelic", "datadog",
    "sendgrid", "@sentry", "@wix", "@godaddy", "@cloudflare",
    "@duckduckgo.com", "@google.com", "@bing.com", "error-lite",
    "@microsoft.com", "@mozilla.org", "abuse@", "privacy@",
    "@reclameaqui.com", "@jusbrasil.com", "@linkedin.com",
}

DOMINIOS_IGNORAR = [
    "duckduckgo.", "google.", "youtube.", "facebook.", "instagram.",
    "linkedin.", "twitter.", "x.com", "wikipedia.", "gov.br",
    "mpt.mp.br", "jusbrasil.", "consulta.", "reclameaqui.",
    "zoominfo.", "contactout.", "aeroleads.", "glassdoor.",
    "indeed.", "vagas.com", "infojobs.", "catho.",
]

PAGINAS_CONTATO = [
    "/contato", "/contact", "/fale-conosco", "/faleconosco",
    "/rh", "/recursos-humanos", "/trabalhe-conosco",
    "/trabalheconosco", "/vagas", "/careers", "/sobre",
    "/atendimento", "/sac", "/ouvidoria", "/institucional",
]

TERMOS_LINK_CONTATO = (
    "contato", "contact", "fale", "atendimento", "sac", "ouvidoria",
    "rh", "recurso", "trabalhe", "carreira", "career", "vaga",
    "sugest", "duvida", "consulta", "legal", "termo",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

HEADERS_DDG = {
    "User-Agent": "Mozilla/5.0 (compatible; contact-enrichment/1.0)",
    "Accept": "application/json",
}


async def _fechar_playwright(*recursos) -> None:
    for recurso in recursos:
        if recurso:
            try:
                await recurso.close()
            except Exception:
                pass

DDDS_VALIDOS = {
    "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "21", "22", "24", "27", "28", "31", "32", "33", "34", "35", "37", "38",
    "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "51", "53", "54", "55", "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "71", "73", "74", "75", "77", "79", "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "91", "92", "93", "94", "95", "96", "97", "98", "99",
}


def _normalizar(txt: str) -> str:
    nfkd = unicodedata.normalize("NFKD", txt or "")
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def _palavras_chave_empresa(razao_social: str) -> list[str]:
    STOPWORDS = {
        "ltda", "sa", "s", "a", "eireli", "me", "epp", "comercio",
        "industria", "servicos", "de", "do", "da", "dos", "das",
        "e", "em", "para", "com", "brasil", "brasileira", "nome",
        "fantasia", "cia", "companhia", "associacao", "sociedade",
        "vale", "alimentos",
    }
    palavras = re.findall(r"[A-Za-zÀ-ÿ]+", razao_social)
    significativas = [
        _normalizar(p) for p in palavras
        if _normalizar(p) not in STOPWORDS and len(p) > 3
    ]
    return significativas[:3]


def _limpar_email(email: str) -> Optional[str]:
    email = email.lower().strip()
    if any(ig in email for ig in EMAILS_IGNORAR):
        return None
    if len(email) > 100:
        return None
    return email


def _limpar_telefone(tel: str) -> Optional[str]:
    digits = re.sub(r"\D", "", tel)
    if digits.startswith("55") and len(digits) in {12, 13}:
        digits = digits[2:]
    if digits.startswith("0800") and len(digits) == 11:
        return f"0800 {digits[4:7]} {digits[7:]}"
    if len(digits) == 8 and digits[:4] in {"3003", "3004", "4003", "4004"}:
        return f"{digits[:4]}-{digits[4:]}"
    if len(digits) < 10 or len(digits) > 11:
        return None
    ddd = digits[:2]
    if ddd not in DDDS_VALIDOS:
        return None
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"


def _priorizar_email_rh(emails: list[str]) -> Optional[str]:
    prioridade_alta = [
        "rh@", "rh.", "recursos.humanos@", "talentos@",
        "recrutamento@", "selecao@", "vagas@", "carreira@",
        "trabalhe@", "emprego@",
    ]
    prioridade_media = [
        "contato@", "contact@", "faleconosco@",
        "atendimento@", "sac@", "ouvidoria@", "compliance@",
    ]
    for pref in prioridade_alta:
        for e in emails:
            if pref in e.lower():
                return e
    for pref in prioridade_media:
        for e in emails:
            if pref in e.lower():
                return e
    return emails[0] if emails else None


def _email_relevante(email: str, palavras_chave: list[str]) -> bool:
    if not palavras_chave:
        return True
    dominio = email.split("@")[-1] if "@" in email else ""
    dominio_norm = _normalizar(dominio)
    return any(p in dominio_norm for p in palavras_chave if len(p) >= 4)


def _dominio_url(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")


def _dominio_base(dominio: str) -> str:
    partes = dominio.lower().removeprefix("www.").split(".")
    if len(partes) >= 3 and partes[-1] == "br" and partes[-2] in {
        "com", "net", "org", "ind", "co",
    }:
        return partes[-3]
    return partes[-2] if len(partes) >= 2 else partes[0]


def _site_compativel(url: str, palavras_chave: list[str]) -> bool:
    dominio = _dominio_url(url)
    if not dominio or any(ig in dominio for ig in DOMINIOS_IGNORAR):
        return False
    nome_dominio = _normalizar(_dominio_base(dominio))
    candidatos = set(palavras_chave)
    candidatos.add("".join(palavras_chave))
    nome_sem_grupo = re.sub(r"^(grupo|group)", "", nome_dominio)
    return nome_dominio in candidatos or nome_sem_grupo in candidatos


def _email_compativel_site(email: str, site: str, palavras_chave: list[str]) -> bool:
    dominio_email = email.rsplit("@", 1)[-1].lower()
    dominio_site = _dominio_url(site)
    nome_dominio_email = _normalizar(_dominio_base(dominio_email))
    return (
        dominio_email == dominio_site
        or dominio_email.endswith("." + dominio_site)
        or _dominio_base(dominio_email) == _dominio_base(dominio_site)
        or nome_dominio_email in palavras_chave
    )


def _normalizar_site_url(url: str) -> str:
    url = (url or "").strip()
    return url if urllib.parse.urlparse(url).scheme else "https://" + url


def _resolver_url_bing(url: str) -> str:
    """Converte o redirecionador do Bing na URL externa, quando presente."""
    parsed = urllib.parse.urlparse(url)
    valor = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
    if "bing.com" not in (parsed.hostname or "") or not valor.startswith("a1"):
        return url
    try:
        payload = valor[2:] + "=" * (-len(valor[2:]) % 4)
        return base64.urlsafe_b64decode(payload).decode("utf-8")
    except Exception:
        return url


def _link_interno_contato(link: str, site: str) -> bool:
    return (
        _dominio_base(_dominio_url(link)) == _dominio_base(_dominio_url(site))
        and any(termo in link.lower() for termo in TERMOS_LINK_CONTATO)
    )


async def _buscar_via_playwright_ddg(query: str, palavras_chave: list[str]) -> dict:
    """
    Usa Playwright para buscar no DuckDuckGo com JavaScript habilitado —
    simula melhor um browser real, menos bloqueio.
    """
    resultados = {"emails": [], "telefones": [], "links": []}

    url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}&kl=br-pt"
    browser = None
    context = None
    page = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1280, "height": 800},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=8_000)
            await asyncio.sleep(1)

            # Pega o texto visível dos resultados
            itens = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('[data-result="web"] .result__body');
                    return Array.from(items).slice(0, 8).map(el => {
                        const a = el.querySelector('a[data-testid="result-title-a"], a.result__a');
                        const s = el.querySelector('[data-result="snippet"], .result__snippet');
                        return {titulo: a?.innerText || '', url: a?.href || '',
                                snippet: s?.innerText || ''};
                    });
                }
            """)

            if not itens:
                # Fallback: pega todo o innerText da página
                itens = []

            resultados["links"] = [item for item in itens if item.get("url")]

    except Exception as e:
        logger.warning(f"Playwright DDG erro: {e}")
    finally:
        await _fechar_playwright(page, context, browser)

    return resultados


async def _buscar_duckduckgo_resultados(query: str) -> list[dict]:
    """Mantem compatibilidade com o script de debug; retorna somente links."""
    return (await _buscar_via_playwright_ddg(query, []))["links"]


async def _buscar_via_playwright_bing(query: str) -> list[dict]:
    """Fonte alternativa somente para descobrir o site oficial."""
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&setlang=pt-BR"
    browser = None
    context = None
    page = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=8_000)
            itens = await page.evaluate("""
                () => Array.from(document.querySelectorAll('li.b_algo')).slice(0, 8).map(el => {
                    const a = el.querySelector('h2 a');
                    const s = el.querySelector('.b_caption p');
                    return {titulo: a?.innerText || '', url: a?.href || '',
                            snippet: s?.innerText || ''};
                })
            """)
            for item in itens:
                item["url"] = _resolver_url_bing(item.get("url", ""))
            return [item for item in itens if item.get("url")]
    except Exception as e:
        logger.warning(f"Playwright Bing erro: {e}")
        return []
    finally:
        await _fechar_playwright(page, context, browser)


async def _extrair_contatos_pagina(page, url: str) -> dict:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass
        await asyncio.sleep(1)
        html = await page.content()
        texto = await page.evaluate("document.body.innerText")
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(el => el.href)"
        )
    except Exception:
        return {}

    emails_encontrados = []
    for m in RE_EMAIL.finditer(html):
        e = _limpar_email(m.group())
        if e and e not in emails_encontrados:
            emails_encontrados.append(e)

    tels_encontrados = []
    texto_telefones = texto + " " + " ".join(
        href.removeprefix("tel:") for href in hrefs if href.startswith("tel:")
    )
    for regex in (RE_TELEFONE_SERVICO, RE_TELEFONE):
        for m in regex.finditer(texto_telefones):
            prefixo = texto_telefones[max(0, m.start() - 6):m.start()]
            codigo_pais = re.search(r"\+(\d{1,3})[ \t(]*$", prefixo)
            if codigo_pais and codigo_pais.group(1) != "55":
                continue
            t = _limpar_telefone(m.group())
            if t and t not in tels_encontrados:
                tels_encontrados.append(t)

    return {
        "emails": emails_encontrados,
        "telefones": tels_encontrados,
        "links": hrefs,
        "url_final": page.url,
    }


async def buscar_contatos_empresa(
    razao_social: str,
    cnpj: str,
    site_conhecido: Optional[str] = None,
) -> dict:
    resultado = {"email": None, "telefone": None, "fonte_contato": None}

    if not razao_social:
        return resultado

    palavras_chave = _palavras_chave_empresa(razao_social)
    site_conhecido = _normalizar_site_url(site_conhecido) if site_conhecido else None

    # ── 1. Playwright no DuckDuckGo (JS habilitado, menos bloqueio) ─────
    # ── 2. Site oficial direto (Playwright) ─────────────────────────────
    if not site_conhecido:
        # Tenta descobrir site via DuckDuckGo
        try:
            query_site = f"{razao_social} site oficial contato"
            busca_site = await _buscar_via_playwright_ddg(query_site, palavras_chave)
            # site é inferido a partir dos links — mas como já pegamos texto,
            # usamos os emails encontrados diretamente
            site_conhecido = next(
                (item["url"] for item in busca_site["links"]
                 if _site_compativel(item["url"], palavras_chave)),
                None,
            )
        except Exception:
            pass

    if not site_conhecido:
        query_site = f"{razao_social} site oficial contato"
        links_bing = await _buscar_via_playwright_bing(query_site)
        site_conhecido = next(
            (item["url"] for item in links_bing
             if _site_compativel(item["url"], palavras_chave)),
            None,
        )

    if site_conhecido:
        browser = None
        context = None
        page = None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                context = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="pt-BR",
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                emails_encontrados = []
                telefones_encontrados = []
                visitadas = set()
                fila = [site_conhecido]
                site_final = site_conhecido

                while fila and len(visitadas) < 8:
                    url = fila.pop(0)
                    if url in visitadas:
                        continue
                    visitadas.add(url)
                    contatos = await _extrair_contatos_pagina(page, url)
                    if not contatos:
                        continue

                    if len(visitadas) == 1:
                        site_final = contatos.get("url_final") or site_conhecido
                        origem = urllib.parse.urlsplit(site_final)
                        base = f"{origem.scheme}://{origem.netloc}"
                        links_reais = [
                            link for link in contatos.get("links", [])
                            if _link_interno_contato(link, site_final)
                        ]
                        fila.extend(links_reais[:5])
                        fila.extend(base + path for path in PAGINAS_CONTATO)

                    for email in contatos.get("emails", []):
                        if email not in emails_encontrados:
                            emails_encontrados.append(email)
                    for telefone in contatos.get("telefones", []):
                        if telefone not in telefones_encontrados:
                            telefones_encontrados.append(telefone)

                emails_validos = [
                    email for email in emails_encontrados
                    if _email_compativel_site(email, site_final, palavras_chave)
                ]
                if emails_validos:
                    resultado["email"] = _priorizar_email_rh(emails_validos)
                if telefones_encontrados:
                    resultado["telefone"] = telefones_encontrados[0]
                if resultado["email"] or resultado["telefone"]:
                    resultado["fonte_contato"] = f"Site oficial: {site_final}"

        except Exception as e:
            logger.warning(f"Erro ao raspar site {site_conhecido}: {e}")
        finally:
            await _fechar_playwright(page, context, browser)

    return resultado
