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
from typing import Optional

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

from models.schemas import Empresa, SituacaoTAC

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tutores-lms")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
MODEL_NAME = "gemini-2.5-flash-lite"

_model: Optional[GenerativeModel] = None


def get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _model = GenerativeModel(MODEL_NAME)
    return _model


def _parse_json(text: str) -> dict | list:
    clean = re.sub(r"`{3}(?:json)?|`{3}", "", text).strip()
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"JSON não encontrado: {text[:200]}")


def _cnpj_valido(cnpj: str) -> bool:
    cnpj_limpo = re.sub(r"[^\d]", "", cnpj or "")
    return len(cnpj_limpo) == 14 and cnpj_limpo != "00000000000000"


def _normalizar_numero_ou_none(valor):
    if valor is None:
        return None
    if valor == "":
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    try:
        return int(valor)
    except (ValueError, TypeError):
        return None


def calcular_deficit_pcd(empresa: dict) -> dict:
    cota_exigida = _normalizar_numero_ou_none(empresa.get("cota_exigida"))
    cota_cumprida = _normalizar_numero_ou_none(empresa.get("cota_cumprida"))

    empresa["cota_exigida"] = cota_exigida
    empresa["cota_cumprida"] = cota_cumprida
    empresa["num_funcionarios"] = _normalizar_numero_ou_none(empresa.get("num_funcionarios"))

    if isinstance(cota_exigida, int) and isinstance(cota_cumprida, int):
        empresa["deficit_pcd"] = max(cota_exigida - cota_cumprida, 0)
    else:
        empresa["deficit_pcd"] = None

    return empresa


def avaliar_oportunidade_pcd(empresa: dict) -> dict:
    situacao = empresa.get("situacao")

    if isinstance(situacao, SituacaoTAC):
        situacao = situacao.value

    situacao = str(situacao or "").strip()
    deficit_pcd = empresa.get("deficit_pcd")

    prazo_em_aberto = empresa.get("prazo_em_aberto") is True
    obrigacao_contratacao = empresa.get("obrigacao_contratacao_pcd") is True
    plano_adequacao = empresa.get("plano_adequacao_pcd") is True
    acoes_futuras = empresa.get("acoes_futuras_inclusao") is True
    risco_multa = empresa.get("risco_multa") is True

    prazo = empresa.get("prazo_cumprimento_data") or empresa.get("prazo_cumprimento")

    if situacao == "TAC descumprido":
        empresa["tipo_lead"] = "lead_quente"
        empresa["motivo_lead"] = (
            "TAC com descumprimento, inadimplemento, obrigação violada, "
            "execução ou multa aplicada/cobrada."
        )

        if risco_multa:
            empresa["resumo_oportunidade"] = "TAC descumprido com risco de multa"
        else:
            empresa["resumo_oportunidade"] = "TAC descumprido com oportunidade de atuação imediata"

        return empresa

    if situacao == "TAC em cumprimento":
        tem_deficit = isinstance(deficit_pcd, int) and deficit_pcd > 0
        tem_obrigacao_futura = prazo_em_aberto and obrigacao_contratacao

        if tem_deficit or tem_obrigacao_futura or plano_adequacao or acoes_futuras:
            empresa["tipo_lead"] = "lead_acompanhamento"

            if tem_deficit and prazo:
                empresa["motivo_lead"] = "TAC em cumprimento com déficit PcD e prazo/documento de adequação."
                empresa["resumo_oportunidade"] = f"Faltam contratar {deficit_pcd} PcDs até {prazo}"
            elif tem_deficit:
                empresa["motivo_lead"] = "TAC em cumprimento com déficit PcD confirmado."
                empresa["resumo_oportunidade"] = f"Faltam contratar {deficit_pcd} PcDs"
            elif tem_obrigacao_futura:
                empresa["motivo_lead"] = (
                    "TAC em cumprimento com obrigação futura de contratação PcD "
                    "e prazo em aberto."
                )
                empresa["resumo_oportunidade"] = "Obrigação futura de contratação PcD com prazo em aberto"
            elif plano_adequacao:
                empresa["motivo_lead"] = "TAC em cumprimento com plano de adequação PcD."
                empresa["resumo_oportunidade"] = "Cota PcD em adequação"
            elif acoes_futuras:
                empresa["motivo_lead"] = (
                    "TAC em cumprimento com ações futuras de inclusão, "
                    "acessibilidade ou contratação."
                )
                empresa["resumo_oportunidade"] = "Ações futuras de inclusão PcD previstas"

            return empresa

    empresa["tipo_lead"] = "sem_oportunidade"
    empresa["motivo_lead"] = (
        "Documento sem evidência explícita de descumprimento atual "
        "ou oportunidade futura PcD."
    )
    empresa["resumo_oportunidade"] = None

    return empresa


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
        "são paulo",
        "sao paulo",
        "campinas",
        "santos",
        "guarulhos",
        "osasco",
        "barueri",
        "santo andré",
        "santo andre",
        "sorocaba",
        "ministério público do trabalho",
        "ministerio publico do trabalho",
        "mpt",
        "procuradoria regional do trabalho",
    }

    if razao.lower() in nomes_invalidos:
        return False

    for campo in ["num_funcionarios", "cota_exigida", "cota_cumprida", "deficit_pcd"]:
        valor = empresa.get(campo)
        if valor is None or valor == "":
            empresa[campo] = None
            continue

        try:
            valor_int = int(valor)
            if valor_int < 0:
                return False
            empresa[campo] = valor_int
        except (ValueError, TypeError):
            empresa[campo] = None

    cota_exigida = empresa.get("cota_exigida")
    cota_cumprida = empresa.get("cota_cumprida")

    if isinstance(cota_exigida, int) and isinstance(cota_cumprida, int):
        if cota_exigida > 0 and cota_cumprida > cota_exigida:
            return False

    cnpj = empresa.get("cnpj") or ""
    if not _cnpj_valido(cnpj):
        return False

    return True


def _normalizar_texto_para_busca(texto: str) -> str:
    if not texto:
        return ""

    texto_normal = texto.lower()

    try:
        texto_invertido = texto[::-1].lower()
    except Exception:
        texto_invertido = ""

    combinado = f"{texto_normal}\n{texto_invertido}"
    combinado = re.sub(r"\s+", " ", combinado)
    return combinado.strip()


def _eh_documento_tac_pcd(texto: str) -> bool:
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

    model = get_model()

    prompt = f"""SISTEMA:
Você é um especialista sênior em análise de TACs do Ministério Público do Trabalho.

Sua tarefa é EXTRAIR dados somente quando o documento for um TAC sobre COTA PCD:
Lei 8.213/91, art. 93, pessoas com deficiência, PcD ou reabilitados da Previdência Social.

RETORNE [] quando:
- O documento não for TAC/TAC ajustamento.
- O assunto principal for apenas aprendizagem, trabalho infantil, segurança do trabalho,
  assédio, jornada, FGTS, verbas rescisórias, CAT/acidente de trabalho ou fraude processual.
- Não houver evidência explícita de PCD, pessoa com deficiência, reabilitado,
  art. 93 ou Lei 8.213/91 relacionada à reserva/cota de cargos.
- For TAC em cumprimento genérico sem déficit, prazo aberto, obrigação futura,
  plano de adequação ou ações futuras de inclusão/contratação/acessibilidade.

REGRAS CRÍTICAS:
1. Não invente dados.
2. Se o PDF não informar um campo, retorne null.
3. Diferencie:
   - null = não informado no PDF
   - 0 = o PDF afirmou explicitamente zero
4. Não use estimativas externas como fato documental.
5. Não use cidade, MPT, procuradoria, comarca, endereço ou órgão público como empresa.
6. A empresa deve ser a compromissária, inquirida, requerida ou empregadora associada a CNPJ.
7. Extraia um trecho literal curto do PDF em evidencia_textual.

REGRA SOBRE MULTA:
- Não classifique multa preventiva, futura ou condicional como descumprimento.
- Exemplo de multa condicional:
  "em caso de descumprimento será aplicada multa"
  Isso NÃO é TAC descumprido.
  Nesse caso use risco_multa=true, mas mantenha a situação correta.
- Classifique como "TAC descumprido" somente se o PDF afirmar explicitamente:
  descumprimento constatado, inadimplemento, obrigação violada, execução,
  multa aplicada ou multa cobrada.

COMO CLASSIFICAR SITUAÇÃO:
- "TAC descumprido": somente quando houver descumprimento atual/constatado,
  inadimplemento, obrigação violada, execução ou multa aplicada/cobrada.
- "TAC em cumprimento": quando o TAC estiver vigente, recém firmado,
  em adequação, com obrigações futuras ou prazos em aberto.
- "TAC cumprido": quando disser integralmente cumprido, arquivado por cumprimento
  ou encerrado por cumprimento.

SINAIS DE OPORTUNIDADE:
Marque os campos abaixo somente quando houver evidência textual explícita:
- prazo_em_aberto: true se houver prazo futuro ou prazo ainda em andamento.
- risco_multa: true se houver multa prevista, mesmo condicional.
- obrigacao_contratacao_pcd: true se houver obrigação de contratar PcDs/reabilitados.
- plano_adequacao_pcd: true se houver plano, cronograma ou adequação da cota PcD.
- acoes_futuras_inclusao: true se houver ações futuras de inclusão, acessibilidade,
  busca ativa, capacitação ou contratação PcD.

CAMPOS NUMÉRICOS:
- num_funcionarios: total de empregados citado no TAC, ou null.
- cota_exigida: número de PcDs/reabilitados exigidos citado no TAC, ou null.
- cota_cumprida: número de PcDs/reabilitados já cumpridos citado no TAC, ou null.
- deficit_pcd: só preencha se o PDF informar explicitamente o déficit.
  Caso contrário, retorne null. O sistema calculará depois se possível.

RETORNE APENAS JSON válido no formato abaixo:

[
  {{
    "razao_social": string | null,
    "cnpj": string | null,
    "endereco": string | null,
    "num_funcionarios": int | null,
    "cota_exigida": int | null,
    "cota_cumprida": int | null,
    "deficit_pcd": int | null,
    "motivo": string | null,
    "situacao": "TAC em cumprimento" | "TAC descumprido" | "TAC cumprido" | null,
    "setor": string | null,
    "orgao": "{orgao}",
    "numero_procedimento": string | null,
    "data_abertura": string | null,
    "prazo_cumprimento": string | null,
    "prazo_cumprimento_data": string | null,
    "prazo_em_aberto": boolean | null,
    "risco_multa": boolean | null,
    "obrigacao_contratacao_pcd": boolean | null,
    "plano_adequacao_pcd": boolean | null,
    "acoes_futuras_inclusao": boolean | null,
    "evidencia_textual": string | null
  }}
]

Órgão MPT: {orgao}

DOCUMENTO:
{conteudo[:12000]}
"""

    cfg = GenerationConfig(
        temperature=0.0,
        max_output_tokens=4096,
        response_mime_type="application/json",
    )

    print(f"DEBUG: Enviando prompt para o Gemini (tamanho: {len(prompt)})...")

    try:
        response = model.generate_content(prompt, generation_config=cfg)
        print("DEBUG: Resposta recebida do Gemini com sucesso!")

        result = _parse_json(response.text)

        if isinstance(result, dict):
            result = [result]

        if isinstance(result, list):
            empresas_validas = []

            for empresa in result:
                if not isinstance(empresa, dict):
                    continue

                empresa["orgao"] = empresa.get("orgao") or orgao

                for campo in [
                    "num_funcionarios",
                    "cota_exigida",
                    "cota_cumprida",
                    "deficit_pcd",
                ]:
                    if empresa.get(campo) == "":
                        empresa[campo] = None

                empresa = calcular_deficit_pcd(empresa)
                empresa = avaliar_oportunidade_pcd(empresa)

                if _empresa_extraida_valida(empresa):
                    empresas_validas.append(empresa)

            return empresas_validas

    except Exception as e:
        print(f"ERRO CRÍTICO NA CHAMADA DO GEMINI: {e}")

    return []


async def enriquecer_empresa(empresa: dict) -> dict:
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
        response_mime_type="application/json",
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
    Mantido para compatibilidade com o fluxo atual.

    Agora o déficit documental só é calculado quando cota_exigida e cota_cumprida
    são conhecidos. Estimativa externa não entra no cálculo.
    """
    empresa = calcular_deficit_pcd(empresa)
    empresa = avaliar_oportunidade_pcd(empresa)

    deficit = empresa.get("deficit_pcd")
    sit = empresa.get("situacao") or ""

    if isinstance(sit, SituacaoTAC):
        sit_str = sit.value
    else:
        sit_str = str(sit)

    if empresa.get("tipo_lead") == "lead_quente":
        score = 9
        nivel = "Alta"
    elif empresa.get("tipo_lead") == "lead_acompanhamento":
        score = 6
        nivel = "Média"
    else:
        score = 1
        nivel = "Baixa"

    rec = empresa.get("resumo_oportunidade") or "Sem oportunidade comercial explícita no TAC."
    perfis = ["Auxiliar administrativo", "Operador de caixa", "Assistente de estoque"]

    return {
        "score_oportunidade": score,
        "nivel": nivel,
        "deficit_pcd": deficit,
        "recomendacao": rec,
        "perfis_sugeridos": perfis,
        "tipo_lead": empresa.get("tipo_lead"),
        "motivo_lead": empresa.get("motivo_lead"),
        "resumo_oportunidade": empresa.get("resumo_oportunidade"),
        "situacao": sit_str,
    }


async def buscar_dados_cnpj(cnpj: str) -> dict:
    """
    Procura dados da empresa na BrasilAPI usando o CNPJ.

    Importante:
    - num_funcionarios_estimado_externo é estimativa externa.
    - Não deve ser usado para calcular cota, déficit ou classificar oportunidade documental.
    """
    import httpx

    cnpj_limpo = re.sub(r"[^\d]", "", cnpj or "")

    if len(cnpj_limpo) != 14:
        return {}

    PORTE_FUNC = {
        "ME": 9,
        "EPP": 50,
        "MEDIO PORTE": 250,
        "GRANDE PORTE": 1000,
        "NAO INFORMADO": None,
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
            func_estimado = PORTE_FUNC.get(porte)

            end_parts = [
                data.get("logradouro", ""),
                data.get("numero", ""),
                data.get("complemento", ""),
                data.get("bairro", ""),
                f"{data.get('municipio', '')}/{data.get('uf', '')}",
                f"CEP {data.get('cep', '')}" if data.get("cep") else "",
            ]

            endereco = ", ".join(p for p in end_parts if p and str(p).strip())

            tel = data.get("ddd_telefone_1", "") or data.get("ddd_telefone_2", "")

            return {
                "razao_social_oficial": data.get("razao_social", ""),
                "nome_fantasia": data.get("nome_fantasia", ""),
                "porte": porte,
                "num_funcionarios_estimado_externo": func_estimado,
                "origem_num_funcionarios": "estimativa_externa" if func_estimado is not None else None,
                "cnpj_situacao": data.get("descricao_situacao_cadastral", ""),
                "endereco_receita": endereco,
                "telefone_receita": tel,
                "email_receita": data.get("email", ""),
                "municipio": data.get("municipio", ""),
                "uf": data.get("uf", ""),
                "data_abertura_receita": data.get("data_inicio_atividade", ""),
            }

    except Exception as e:
        logger.warning(f"BrasilAPI erro para CNPJ {cnpj}: {e}")
        return {}