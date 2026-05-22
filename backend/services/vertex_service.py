"""
Vertex AI Service — usa Gemini para:
1. Analisar HTML do MPT e extrair procedimentos TAC/PCD
2. Enriquecer dados de contato das empresas
3. Classificar gravidade do descumprimento
"""
import json
import os
import re
from typing import Optional

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "seu-projeto-gcp")
LOCATION   = os.getenv("GCP_LOCATION",   "us-central1")
MODEL_NAME = "gemini-1.5-pro"

_model: Optional[GenerativeModel] = None


def get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _model = GenerativeModel(MODEL_NAME)
    return _model


def _parse_json_response(text: str) -> dict:
    """Limpa e parseia a resposta JSON do Gemini."""
    clean = re.sub(r"```(?:json)?|```", "", text).strip()
    # Tenta encontrar o primeiro objeto/array JSON
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"JSON não encontrado na resposta: {text[:300]}")


async def extrair_tacs_do_html(html_content: str, orgao: str = "") -> list[dict]:
    """
    Envia o HTML da página de movimentação de procedimentos para o Gemini
    e extrai os casos TAC relacionados a PCD.
    """
    model = get_model()

    filtro_orgao = f"Foque no órgão: {orgao}." if orgao else "Analise todos os órgãos."

    prompt = f"""Você é um especialista em análise de procedimentos do Ministério Público do Trabalho.

Analise o conteúdo HTML abaixo da página de movimentação de procedimentos do MPT-SP (PRT2).
{filtro_orgao}

Identifique APENAS os casos que sejam:
- TAC (Termo de Ajuste de Conduta) OU IC (Inquérito Civil) com TAC firmado
- Relacionados a PCD (Pessoas com Deficiência) / Lei de Cotas (Lei 8.213/91, art. 93)
- Com empresas que descumpriram a cota mínima de contratação de PCDs

Para cada caso encontrado, extraia:
{{
  "razao_social": "nome completo da empresa",
  "cnpj": "CNPJ formatado",
  "orgao": "órgão do MPT responsável",
  "numero_procedimento": "número do procedimento",
  "data_abertura": "data de abertura",
  "motivo": "descrição detalhada do motivo — inclua número de funcionários, cota exigida, cota cumprida",
  "endereco": "endereço completo com CEP",
  "email": "e-mail corporativo se disponível",
  "telefone": "telefone com DDD",
  "situacao": "TAC em cumprimento | TAC descumprido | TAC cumprido",
  "setor": "setor econômico",
  "num_funcionarios": 0,
  "cota_exigida": 0,
  "cota_cumprida": 0
}}

HTML para análise:
{html_content[:15000]}

Retorne APENAS JSON válido com campo "empresas" contendo o array. Sem markdown, sem explicação.
"""

    cfg = GenerationConfig(
        temperature=0.1,
        max_output_tokens=4096,
        response_mime_type="application/json",
    )
    response = model.generate_content(prompt, generation_config=cfg)
    return _parse_json_response(response.text).get("empresas", [])


async def enriquecer_empresa(empresa_parcial: dict) -> dict:
    """
    Usa o Gemini para enriquecer dados faltantes de uma empresa
    (e-mail, telefone, endereço) com base em CNPJ/razão social.
    """
    model = get_model()

    prompt = f"""Com base nos dados abaixo de uma empresa que tem TAC/PCD no MPT-SP,
sugira dados de contato plausíveis (e-mail padrão corporativo, telefone, endereço completo).
NÃO invente informações confidenciais. Use padrões corporativos comuns.

Empresa: {json.dumps(empresa_parcial, ensure_ascii=False)}

Retorne JSON com os campos faltantes preenchidos. Apenas JSON, sem explicação.
"""
    cfg = GenerationConfig(temperature=0.2, max_output_tokens=512)
    response = model.generate_content(prompt, generation_config=cfg)
    try:
        enriched = _parse_json_response(response.text)
        return {**empresa_parcial, **enriched}
    except Exception:
        return empresa_parcial


async def classificar_oportunidade(empresa: dict) -> dict:
    """
    Classifica a urgência/oportunidade para prospecção de candidatos PCD.
    Retorna score (0-10) e recomendações.
    """
    model = get_model()

    prompt = f"""Analise esta empresa com TAC PCD e classifique a oportunidade de prospecção de candidatos PCDs.

Dados: {json.dumps(empresa, ensure_ascii=False)}

Retorne JSON com:
{{
  "score_oportunidade": 8,       // 0-10 (10 = maior urgência/oportunidade)
  "nivel": "Alta | Média | Baixa",
  "deficit_pcd": 5,              // PCDs faltando para cumprir cota
  "recomendacao": "texto curto de recomendação de abordagem",
  "perfis_sugeridos": ["Auxiliar administrativo", "Operador de caixa"]
}}

Apenas JSON, sem explicação.
"""
    cfg = GenerationConfig(temperature=0.1, max_output_tokens=512)
    response = model.generate_content(prompt, generation_config=cfg)
    try:
        return _parse_json_response(response.text)
    except Exception:
        return {"score_oportunidade": 5, "nivel": "Média", "deficit_pcd": 0,
                "recomendacao": "Verificar manualmente.", "perfis_sugeridos": []}
