"""
Vertex AI Service — Gemini 1.5 Pro
Prompt otimizado para extrair dados de TACs PCD do MPT
baseado na estrutura real dos documentos (Lei 8.213/91, art. 93)
"""
import json
import os
import re
from typing import Optional

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "devsprojects-af12e")
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
    clean = re.sub(r"```(?:json)?|```", "", text).strip()
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"JSON não encontrado: {text[:200]}")


async def extrair_tacs_do_html(conteudo: str, orgao: str = "") -> list[dict]:
    """
    Analisa texto de documento TAC PCD e extrai todos os dados estruturados.
    """
    model = get_model()

    prompt = f"""Você é especialista em análise de Termos de Ajuste de Conduta (TAC) do 
Ministério Público do Trabalho sobre Lei 8.213/91, art. 93 (cotas para PCDs).

Analise o documento e extraia TODOS os campos abaixo com precisão.

CAMPOS OBRIGATÓRIOS:
- razao_social: nome completo da empresa (ex: "RI HAPPY BRINQUEDOS S.A.")
- cnpj: CNPJ no formato XX.XXX.XXX/XXXX-XX (ex: "58.731.662/0001-11")
- endereco: endereço completo com CEP (ex: "Av. Eng. Luiz Carlos Berrini, 105, 16º andar — São Paulo/SP, CEP 04.571-900")
- num_funcionarios: total de funcionários (número inteiro, 0 se não mencionado)
- cota_exigida: número de PCDs exigidos por lei (número inteiro, 0 se não mencionado)
- cota_cumprida: número de PCDs que a empresa possuía quando o TAC foi firmado (número inteiro, 0 se não mencionado)
- prazo_cumprimento: prazo para cumprir a obrigação. Procure por QUALQUER uma dessas formas:
  * "Prazo para o cumprimento desta obrigação: [data]" → extraia a data
  * "no prazo de X anos/meses a partir de [data]" → ex: "2 anos a partir de 18/03/2014"
  * "no prazo suplementar de X anos" → ex: "2 anos suplementares"
  * "até [data]" → extraia a data
  * Cronograma por semestres → ex: "4 semestres (Sem1: 88, Sem2: 88, Sem3: 88, Sem4: 91)"
  * Se não houver prazo explícito, coloque ""
- responsavel_nome: nome completo do representante da EMPRESA que assinou (ex: "Guilherme de Biagi Pereira"). NÃO é o Procurador do MPT
- responsavel_cargo: cargo do representante (ex: "Diretor Financeiro")
- advogado: nome do advogado que representa a empresa + OAB (ex: "Dra. Idaliana Blenda Silva Mota — OAB/SP 392.571")
- email: e-mail da empresa se mencionado (senão "")
- telefone: telefone da empresa se mencionado (senão "")
- situacao: 
  * "TAC descumprido" — descumprimento, inadimplência, empresa não assinou cronograma
  * "TAC em cumprimento" — empresa assinou e tem prazo/cronograma em andamento  
  * "TAC cumprido" — cumprimento total, encerrado, arquivado
- motivo: 2-3 frases descrevendo: total funcionários, cota exigida, cota cumprida, déficit e o que foi acordado
- setor: setor econômico (Varejo, Indústria, Saúde, Logística, Alimentício, Financeiro, Tecnologia, Serviços, Transporte, Construção Civil)

REGRAS:
- Se NÃO for TAC sobre cota PCD (Lei 8.213/91), retorne {{"empresas": []}}
- responsavel_nome é sempre o representante da EMPRESA, nunca o Procurador do MPT
- prazo_cumprimento: procure exatamente pela frase "Prazo para o cumprimento desta obrigação:" ou similar
- Para campos não encontrados: use 0 para números, "" para texto

Órgão MPT: {orgao}

DOCUMENTO:
{conteudo[:12000]}

Retorne APENAS JSON válido:
{{"empresas": [{{
  "razao_social": "",
  "cnpj": "",
  "endereco": "",
  "num_funcionarios": 0,
  "cota_exigida": 0,
  "cota_cumprida": 0,
  "prazo_cumprimento": "",
  "responsavel_nome": "",
  "responsavel_cargo": "",
  "advogado": "",
  "email": "",
  "telefone": "",
  "situacao": "",
  "motivo": "",
  "setor": ""
}}]}}
"""

    cfg = GenerationConfig(temperature=0.1, max_output_tokens=2048)
    response = model.generate_content(prompt, generation_config=cfg)
    result = _parse_json(response.text)
    if isinstance(result, dict):
        return result.get("empresas", [])
    return []


async def enriquecer_empresa(empresa: dict) -> dict:
    """Enriquece dados de contato faltantes usando o Gemini."""
    model = get_model()

    prompt = f"""Com base nos dados desta empresa que tem TAC PCD no MPT, 
sugira dados de contato corporativos plausíveis para os campos vazios.
Não invente informações falsas — use padrões corporativos comuns.

Empresa: {json.dumps(empresa, ensure_ascii=False)}

Preencha apenas os campos vazios: email, telefone, endereco, setor.
Retorne JSON com os mesmos campos. Apenas JSON.
"""
    cfg = GenerationConfig(temperature=0.2, max_output_tokens=512)
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
    Calcula score de oportunidade de prospecção de candidatos PCD.
    Score determinístico baseado no déficit + situação + porte.
    """
    exig  = empresa.get("cota_exigida",    0) or 0
    cumpr = empresa.get("cota_cumprida",   0) or 0
    func  = empresa.get("num_funcionarios",0) or 0
    sit   = empresa.get("situacao",        "") or ""
    deficit = max(0, exig - cumpr)

    # Score determinístico (0-10)
    if exig > 0:
        pct_deficit = deficit / exig
    else:
        pct_deficit = 0.5  # sem dado, assume 50%

    score = round(pct_deficit * 7)  # déficit vale 70%

    # Bônus por situação
    if "descumprido" in sit.lower():
        score += 2
    elif "cumprimento" in sit.lower():
        score += 1

    # Bônus por porte
    if func >= 1000:
        score += 1

    score = min(10, max(1, score))
    nivel = "Alta" if score >= 7 else "Média" if score >= 4 else "Baixa"

    # Usa Gemini só para recomendação e perfis
    model = get_model()
    prompt = f"""Empresa com TAC PCD:
- Razão social: {empresa.get('razao_social','')}
- Setor: {empresa.get('setor','')}
- Funcionários: {func}
- Cota exigida: {exig} PCDs
- Cota cumprida: {cumpr} PCDs  
- Déficit: {deficit} vagas
- Situação: {sit}

Gere em JSON:
{{
  "recomendacao": "1 frase de abordagem comercial para oferecer candidatos PCD",
  "perfis_sugeridos": ["perfil1", "perfil2", "perfil3"]
}}
Apenas JSON."""

    cfg = GenerationConfig(temperature=0.3, max_output_tokens=256)
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
    Busca dados da empresa na BrasilAPI usando o CNPJ.
    Retorna porte, endereço, telefone, e-mail e razão social oficial.
    """
    import httpx
    import re

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

            # Monta endereço
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
