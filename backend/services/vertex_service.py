"""
Serviço Vertex AI — Gemini 2.5 Flash Lite
Prompt otimizado para extrair dados de TACs PCD do MPT
baseado na estrutura real dos documentos (Lei 8.213/91, art. 93)
Integrado nativamente com Schemas Pydantic.
"""
import json
import logging
import os
import re
from typing import Optional, List

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# IMPORTANTE: Altere o caminho abaixo para corresponder ao seu arquivo de schemas
from models.schemas import Empresa, SituacaoTAC

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tutores-lms")
LOCATION   = os.getenv("GCP_LOCATION",   "us-central1")
MODEL_NAME = "gemini-2.5-flash-lite"

_model: Optional[GenerativeModel] = None


def get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _model = GenerativeModel(MODEL_NAME)
    return _model


def _parse_json(text: str) -> dict | list:
    """Corrige a formatação do JSON recebido da API do Gemini sem usar crases triplas literais."""
    clean = re.sub(r"`{3}(?:json)?|`{3}", "", text).strip()
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"JSON não encontrado: {text[:200]}")


def _cnpj_valido(cnpj: str) -> bool:
    cnpj_limpo = re.sub(r"[^\d]", "", cnpj or "")
    return len(cnpj_limpo) == 14 and cnpj_limpo != "00000000000000"


def _empresa_extraida_valida(empresa: dict) -> bool:
    if not empresa:
        return False

    razao = (empresa.get("razao_social") or "").strip()
    situacao = (empresa.get("situacao") or "").strip()
    motivo = (empresa.get("motivo") or "").lower()

    bloqueios_motivo = [
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

        # NOVOS
    "não trata de cota pcd",
    "nao trata de cota pcd",
    "não aborda especificamente a cota pcd",
    "nao aborda especificamente a cota pcd",
    "trabalho infantil",
    "aprendizagem",
    "aprendizes",
    "assédio moral",
    "assedio moral",
    "assédio sexual",
    "assedio sexual",
    ]

    if any(b in motivo for b in bloqueios_motivo):
        return False

    if not razao or not situacao:
        return False

    nomes_invalidos = {
        "são paulo", "sao paulo", "campinas", "santos", "guarulhos",
        "osasco", "barueri", "santo andré", "santo andre", "sorocaba",
        "ministério público do trabalho", "ministerio publico do trabalho",
        "mpt", "procuradoria regional do trabalho"
    }

    if razao.lower() in nomes_invalidos:
        return False

    for campo in ["num_funcionarios", "cota_exigida", "cota_cumprida"]:
        valor = empresa.get(campo, 0)
        try:
            valor_int = int(valor)
            if valor_int < 0:
                return False
        except (ValueError, TypeError):
            return False

    cota_exigida = int(empresa.get("cota_exigida", 0) or 0)
    cota_cumprida = int(empresa.get("cota_cumprida", 0) or 0)

    if cota_exigida > 0 and cota_cumprida > cota_exigida:
        return False

    cnpj = empresa.get("cnpj") or ""
    if not _cnpj_valido(cnpj):
        return False

    return True


def _normalizar_texto_para_busca(texto: str) -> str:
    """
    Normaliza texto extraído de PDF para busca por regex.
    Alguns PDFs do MPT podem vir com trechos invertidos/espelhados na extração.
    Por isso a busca considera texto normal + texto invertido.
    """
    if not texto:
        return ""

    texto_normal = texto.lower()

    try:
        texto_invertido = texto[::-1].lower()
    except Exception:
        texto_invertido = ""

    combinado = f"{texto_normal}\n{texto_invertido}"

    # Remove excesso de espaços, mas mantém acentos.
    combinado = re.sub(r"\s+", " ", combinado)
    return combinado.strip()


def _eh_documento_tac_pcd(texto: str) -> bool:
    """
    Pré-filtro local para decidir se vale chamar o Gemini.

    Estratégia:
    1. O documento precisa parecer um TAC/TAC ajustamento.
    2. Precisa ter evidência objetiva de PCD/cota legal/reabilitados/art. 93.
    3. Exclusões só eliminam o documento quando NÃO houver evidência forte de PCD.
       Isso evita descartar um TAC PCD só porque o texto também menciona CLT, OIT,
       segurança do trabalho ou outro termo genérico.
    """
    if not texto or len(texto.strip()) < 50:
        logger.info("Pré-filtro PCD: texto vazio ou muito curto.")
        return False

    t = _normalizar_texto_para_busca(texto)

    padroes_tac = [
        r"termo\s+de\s+ajuste\s+de\s+conduta",
        r"termo\s+de\s+ajustamento\s+de\s+conduta",
        r"termo\s+de\s+compromisso\s+de\s+ajustamento\s+de\s+conduta",
        r"termo\s+de\s+compromisso\s+e\s+ajustamento\s+de\s+conduta",
        r"\btac\b",
    ]
    tem_tac = any(re.search(p, t, flags=re.I) for p in padroes_tac)

    if not tem_tac:
        logger.info("Pré-filtro PCD: descartado porque não parece TAC.")
        return False

    # Evidências fortes de TAC PCD / cota legal.
    evidencias_fortes = [
        r"art\.?\s*93\s+da\s+lei\s*n?[ºo]?\s*8\.?\s*213",
        r"artigo\s*93\s+da\s+lei\s*n?[ºo]?\s*8\.?\s*213",
        r"lei\s*n?[ºo]?\s*8\.?\s*213.{0,80}art\.?\s*93",
        r"lei\s*n?[ºo]?\s*8\.?\s*213.{0,80}artigo\s*93",
        r"reserva\s+legal\s+de\s+cargos",
        r"cota\s+legal\s+(?:de|para)\s+(?:pessoas\s+com\s+defici[eê]ncia|pcd|reabilitad)",
        r"cotas?\s+(?:de|para)\s+(?:pessoas\s+com\s+defici[eê]ncia|pcd|reabilitad)",
        r"contrata[cç][aã]o\s+de\s+pessoas\s+com\s+defici[eê]ncia",
        r"benefici[aá]rios?\s+reabilitad[oa]s?\s+da\s+previd[eê]ncia\s+social",
    ]

    # Evidências médias. Duas ou mais médias também podem liberar o Gemini.
    evidencias_medias = [
        r"pessoa[s]?\s+com\s+defici[eê]ncia",
        r"pessoa[s]?\s+portadora[s]?\s+de\s+defici[eê]ncia",
        r"portador(?:a|es|as)?\s+de\s+defici[eê]ncia",
        r"\bpcd\b",
        r"\bpcds\b",
        r"deficiente[s]?",
        r"reabilitad[oa]s?",
        r"lei\s+de\s+cotas?",
        r"cota[s]?\s+pcd",
        r"aprendiz(?:es)?.{0,80}pessoas\s+com\s+defici[eê]ncia",
        r"empregados?\s+com\s+defici[eê]ncia",
        r"trabalhadores?\s+com\s+defici[eê]ncia",
        r"vagas?\s+(?:para|destinadas?\s+a)\s+(?:pcd|pessoas\s+com\s+defici[eê]ncia)",
        r"art\.?\s*93\b",
        r"artigo\s*93\b",
        r"8\.?\s*213\s*/?\s*91",
        r"8\.?\s*213",
    ]

    fortes = [p for p in evidencias_fortes if re.search(p, t, flags=re.I)]
    medias = [p for p in evidencias_medias if re.search(p, t, flags=re.I)]

    tem_evidencia_pcd = bool(fortes) or len(medias) >= 2

    if not tem_evidencia_pcd:
        logger.info(
            "Pré-filtro PCD: TAC sem evidência suficiente de PCD. "
            f"fortes={len(fortes)} medias={len(medias)}"
        )
        return False

    # Exclusões de assuntos que geram falso positivo.
    # Só bloqueiam se não houver evidência forte.
    padroes_exclusao = [
        r"trabalho\s+infantil",
        r"cota[s]?\s+de\s+aprendizagem",
        r"cota[s]?\s+(?:para\s+)?aprendiz(?:es)?",
        r"jovem\s+aprendiz",
        r"lei\s+de\s+aprendizagem",
        r"art\.?\s*429\b",
        r"artigo\s*429\b",
        r"comunica[cç][aã]o\s+de\s+acidente\s+de\s+trabalho",
        r"\bcat\b",
        r"acidente[s]?\s+d[eou]\s+trabalho",
        r"meio\s+ambiente\s+do\s+trabalho",
        r"seguran[cç]a\s+do\s+trabalho",
        r"\bcipa\b",
        r"\bpgr\b",
        r"\bpcmso\b",
        r"\bppra\b",
        r"\bnr-\d+",
        r"ass[eé]dio\s+moral",
        r"ass[eé]dio\s+sexual",
        r"jornada\s+de\s+trabalho",
        r"horas?\s+extra",
        r"verbas\s+rescis[oó]rias",
        r"fgts",
    ]

    tem_exclusao = any(re.search(p, t, flags=re.I) for p in padroes_exclusao)

    if tem_exclusao and not fortes:
        logger.info(
            "Pré-filtro PCD: descartado por exclusão sem evidência forte de PCD. "
            f"fortes={len(fortes)} medias={len(medias)}"
        )
        return False

    logger.info(
        "Pré-filtro PCD: aceito para Gemini. "
        f"fortes={len(fortes)} medias={len(medias)} exclusao={tem_exclusao}"
    )
    return True

async def extrair_tacs_do_html(conteudo: str, orgao: str = "") -> list[dict]:
    print("ENTROU NO GEMINI - extrair_tacs_do_html")

    # Não bloqueia aqui.
    # O filtro regex pode falhar em PDFs do MPT.
    # A decisão final fica com o Gemini pelo prompt.
    model = get_model()

    # 2. Prompt com instrução de Sistema altamente restritiva no início
    prompt = f"""SISTEMA:
Você é um especialista sênior em análise de TACs do Ministério Público do Trabalho.
Sua tarefa é EXTRAIR dados somente quando o documento for um TAC sobre COTA PCD
(Lei 8.213/91, art. 93, pessoas com deficiência ou reabilitados da Previdência Social).

RETORNE [] quando:
- O documento não for TAC/TAC ajustamento.
- O assunto principal for apenas aprendizagem, trabalho infantil, segurança do trabalho,
  assédio, jornada, FGTS, verbas rescisórias, CAT/acidente de trabalho ou fraude processual.
- Não houver evidência explícita de PCD, pessoa com deficiência, reabilitado,
  art. 93 ou Lei 8.213/91 relacionada à reserva/cota de cargos.

IMPORTANTE:
- Não invente dados.
- Não use cidade, MPT, procuradoria, comarca, endereço ou órgão público como empresa.
- A empresa deve ser a compromissária/inquirida/requerida/empregadora associada a CNPJ.
- Se não tiver certeza de que é TAC PCD, retorne [].

CAMPOS A EXTRAIR:
- razao_social: nome jurídico completo da empresa compromissária/inquirida
- cnpj: CNPJ no formato XX.XXX.XXX/XXXX-XX, se existir
- endereco: endereço completo, se existir
- num_funcionarios: total de empregados citado no TAC, ou 0
- cota_exigida: número de PCDs/reabilitados exigidos, ou 0
- cota_cumprida: número de PCDs/reabilitados já cumpridos, ou 0
- motivo: resumo curto explicando a obrigação PCD, déficit e prazo/conduta assumida
- situacao: exatamente "TAC em cumprimento", "TAC descumprido" ou "TAC cumprido"
- setor: um destes quando possível: Varejo, Indústria, Saúde, Logística, Alimentício,
  Financeiro, Tecnologia, Serviços, Transporte, Construção Civil
- orgao: use exatamente "{orgao}"
- numero_procedimento: número do procedimento/inquérito civil, se citado
- data_abertura: data de assinatura/firmamento no formato DD/MM/AAAA, se citada

COMO CLASSIFICAR SITUAÇÃO:
- Se o texto falar em descumprimento, inadimplemento, execução, multa por não cumprir,
  use "TAC descumprido".
- Se for um TAC recém firmado com obrigações e prazos, use "TAC em cumprimento".
- Se disser integralmente cumprido, arquivado por cumprimento ou encerrado, use "TAC cumprido".

RETORNE APENAS JSON válido seguindo o schema. Não explique.

Órgão MPT: {orgao}

DOCUMENTO:
{conteudo[:12000]}
"""

    # O Vertex AI lê dinamicamente a estrutura da sua classe List[Empresa] do Pydantic
    cfg = GenerationConfig(
        temperature=0.0, 
        max_output_tokens=2048,
        response_mime_type="application/json",
    )
    
    # Adição de Debug para verificar o status
    print(f"DEBUG: Enviando prompt para o Gemini (tamanho: {len(prompt)})...")
    try:
        response = model.generate_content(prompt, generation_config=cfg)
        print("DEBUG: Resposta recebida do Gemini com sucesso!")
        
        result = _parse_json(response.text)
        
        if isinstance(result, list):
            return [
                empresa for empresa in result
                if _empresa_extraida_valida(empresa)
            ]
            
    except Exception as e:
        print(f"ERRO CRÍTICO NA CHAMADA DO GEMINI: {e}")
        
    return []


async def enriquecer_empresa(empresa: dict) -> dict:
    """Enriquece dados de contacto faltantes usando o Gemini."""
    model = get_model()

    prompt = f"""Com base nos dados desta empresa que tem TAC PCD no MPT, 
sugira dados de contato corporativos plausíveis para os campos vazios.
Não invente informações falsas — use padrões corporativos comuns.

Empresa: {json.dumps(empresa, ensure_ascii=False)}

Preencha apenas os campos vazios: email, telefone, endereco, setor.
Retorne JSON com os mesmos campos. Apenas JSON.
"""
    cfg = GenerationConfig(
        temperature=0.2, 
        max_output_tokens=512,
        response_mime_type="application/json"
    )
    try:
        response = model.generate_content(prompt, generation_config=cfg)
        enriched = _parse_json(response.text)
        if isinstance(enriched, dict):
            for k, v in enriched.items():
                if v and not empresa.get(k):
                    empresa[k] = v
    except Exception:
        pass
    return empresa


async def classificar_oportunidade(empresa: dict) -> dict:
    """
    Calcula o score de oportunidade de prospecção de candidatos PCD.
    Score determinístico baseado no défice + situação + dimensão da empresa.
    """
    exig  = empresa.get("cota_exigida",    0) or 0
    cumpr = empresa.get("cota_cumprida",   0) or 0
    func  = empresa.get("num_funcionarios",0) or 0
    sit   = empresa.get("situacao",        "") or ""
    
    # Faz o tratamento se a situação vier como o objeto Enum do Pydantic
    if isinstance(sit, SituacaoTAC):
        sit_str = sit.value
    else:
        sit_str = str(sit)
        
    deficit = max(0, exig - cumpr)

    # Score determinístico (0-10)
    if exig > 0:
        pct_deficit = deficit / exig
    else:
        pct_deficit = 0.5  # sem dados, assume 50%

    score = round(pct_deficit * 7)  # défice vale 70%

    # Bónus por situação
    if "descumprido" in sit_str.lower():
        score += 2
    elif "cumprimento" in sit_str.lower():
        score += 1

    # Bónus por dimensão
    if func >= 1000:
        score += 1

    score = min(10, max(1, score))
    nivel = "Alta" if score >= 7 else "Média" if score >= 4 else "Baixa"

    # Usa o Gemini apenas para recomendações e perfis recomendados
    model = get_model()
    prompt = f"""Empresa com TAC PCD:
- Razão social: {empresa.get('razao_social','')}
- Setor: {empresa.get('setor','')}
- Funcionários: {func}
- Cota exigida: {exig} PCDs
- Cota cumprida: {cumpr} PCDs  
- Déficit: {deficit} vagas
- Situação: {sit_str}

Gere em JSON:
{{
  "recomendacao": "1 frase de abordagem comercial para oferecer candidatos PCD",
  "perfis_sugeridos": ["perfil1", "perfil2", "perfil3"]
}}
Apenas JSON."""

    cfg = GenerationConfig(
        temperature=0.3, 
        max_output_tokens=256,
        response_mime_type="application/json"
    )
    rec = "Empresa com déficit de PCDs. Oportunidade para prospecção imediata."
    perfis = ["Auxiliar administrativo", "Operador de caixa", "Assistente de estoque"]
    try:
        resp = model.generate_content(prompt, generation_config=cfg)
        data = _parse_json(resp.text)
        if isinstance(data, dict):
            rec    = data.get("recomendacao", rec)
            perfis = data.get("perfis_sugeridos", perfis)
    except Exception:
        pass

    return {
        "score_oportunidade": score,
        "nivel":              nivel,
        "deficit_pcd":        deficit,
        "recomendacao":       rec,
        "perfis_sugeridos":   perfis,
    }


async def buscar_dados_cnpj(cnpj: str) -> dict:
    """
    Procura dados da empresa na BrasilAPI usando o CNPJ.
    Retorna a dimensão, endereço, telefone, e-mail e razão social oficial.
    """
    import httpx

    # Limpa o CNPJ
    cnpj_limpo = re.sub(r"[^\d]", "", cnpj)
    if len(cnpj_limpo) != 14:
        return {}

    PORTE_FUNC = {
        "ME": 9,
        "EPP": 50,
        "MEDIO PORTE": 250,
        "GRANDE PORTE": 1000,
        "NAO INFORMADO": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}",
                headers={"Accept": "application/json"},
            )
            if not resp.ok:
                return {}

            data = resp.json()

            porte = (data.get("porte") or "NAO INFORMADO").upper()
            func_estimado = PORTE_FUNC.get(porte, 0)

            # Estrutura o endereço
            end_parts = [
                data.get("logradouro", ""),
                data.get("numero", ""),
                data.get("complemento", ""),
                data.get("bairro", ""),
                f"{data.get('municipio','')}/{data.get('uf','')}",
                f"CEP {data.get('cep','')}" if data.get("cep") else "",
            ]
            endereco = ", ".join(p for p in end_parts if p and p.strip())

            # Telefone
            tel = data.get("ddd_telefone_1", "") or data.get("ddd_telefone_2", "")

            return {
                "razao_social_oficial": data.get("razao_social", ""),
                "nome_fantasia":        data.get("nome_fantasia", ""),
                "porte":                porte,
                "num_funcionarios_estimado": func_estimado,
                "cnpj_situacao":        data.get("descricao_situacao_cadastral", ""),
                "endereco_receita":     endereco,
                "telefone_receita":     tel,
                "email_receita":        data.get("email", ""),
                "municipio":            data.get("municipio", ""),
                "uf":                   data.get("uf", ""),
                "data_abertura_receita":data.get("data_inicio_atividade", ""),
            }

    except Exception as e:
        logger.warning(f"BrasilAPI erro para CNPJ {cnpj}: {e}")
        return {}

        