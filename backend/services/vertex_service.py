"""
Serviço Vertex AI — Gemini 2.5 Flash Lite
Prompt otimizado para extrair dados de TACs PCD do MPT
baseado na estrutura real dos documentos (Lei 8.213/91, art. 93)
"""
import json
import logging
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
        if not isinstance(valor, int) or valor < 0:
            return False

    cota_exigida = empresa.get("cota_exigida", 0)
    cota_cumprida = empresa.get("cota_cumprida", 0)

    if cota_exigida > 0 and cota_cumprida > cota_exigida:
        return False

    cnpj = empresa.get("cnpj", "")
    if cnpj and not _cnpj_valido(cnpj):
        return False

    return True


def _eh_documento_tac_pcd(texto: str) -> bool:
    """
    Pré-filtro de alta precisão em Python.
    Utiliza exclusão estrita combinada com um sistema de pontuação por relevância
    para garantir que o assunto principal do documento é a cota de PCD (Lei 8.213/91).
    """
    if not texto:
        return False
        
    texto = texto.lower()

    # 1. Validação obrigatória de formato: Precisa de ser um TAC
    padroes_tac = [
        r"termo\s+de\s+ajuste\s+de\s+conduta",
        r"termo\s+de\s+ajustamento\s+de\s+conduta",
        r"termo\s+de\s+compromisso\s+de\s+ajustamento\s+de\s+conduta",
        r"\btac\b",
    ]
    tem_tac = any(re.search(p, texto) for p in padroes_tac)
    if not tem_tac:
        return False

    # 2. Termos de exclusão altamente estritos (Se contiver QUALQUER um destes, descarta no imediato)
    padroes_exclusao = [
        # Fraudes Processuais / Lides Simuladas / Quitações judiciais
        r"lide[s]?\s+simulada[s]?",
        r"fraude[s]?\s+processual[is]?",
        r"acordo[s]?\s+extrajudicial[is]?",
        r"homologa[cç][aã]o\s+de\s+transa[cç][aã]o",
        r"homologa[cç][aã]o\s+de\s+acordo",
        r"jurisdi[cç][aã]o\s+volunt[aá]ria",
        r"quita[cç][aã]o\s+geral",
        r"verbas\s+rescis[oó]rias",
        
        # Saúde, Higiene e Segurança do Trabalho (Acidentes de Trabalho e NRs)
        r"art\.?\s*22\s+da\s+lei\s*n?[ºo]?\s*8\.?213\/?91", # CAT / Acidente
        r"comunica[cç][aã]o\s+de\s+acidente\s+de\s+trabalho",
        r"\bcat\b",
        r"acidente[s]?\s+d[eou]\s+trabalho",
        r"doen[cç]a[s]?\s+(?:ocupacional|do\s+trabalho|profissional)",
        r"sa[uú]de\s+(?:e|com)\s+seguran[cç]a",
        r"seguran[cç]a\s+do\s+trabalho",
        r"meio\s+ambiente\s+do\s+trabalho",
        r"\bcipa\b",
        r"equipamento[s]?\s+de\s+prote[cç][aã]o",
        r"ergon[oô]mic",
        r"risco[s]?\s+ocupaciona",
        r"exame[s]?\s+m[eé]dico[s]?",
        r"\baso\b",
        r"\bpgr\b",
        r"programa\s+de\s+gerenciamento\s+de\s+riscos",
        r"\bpcmso\b",
        r"\bppra\b",
        r"\bnr-\d+",
        r"atividade[s]?\s+insalubre[s]?",
        r"atividade[s]?\s+perigosa[s]?",
        
        # Legislação de Aprendizagem / Trabalho Infantil / Menores
        r"lei\s*n?[ºo]?\s*8\.?069",                         # Lei do ECA (Estatuto da Criança e do Adolescente)
        r"estatuto\s+da\s+crian[cç]a\s+e\s+do\s+adolescente",
        r"\beca\b",
        r"conven[cç][aã]o\s+(?:138|182)",                    # Convenções da OIT sobre Trabalho Infantil
        r"\boit\b",                                          # Organização Internacional do Trabalho
        r"trabalho\s+(?:de\s+)?menor(?:es)?",
        r"trabalho\s+infantil",
        r"menor(?:es)?\s+de\s+1[468]",
        r"adolescente",
        r"criança[s]?",
        r"crianca[s]?",
        r"jovem\s+aprendiz",
        r"aprendiz",
        r"aprendizado",
        r"cota[s]?\s+de\s+aprendizagem",
        r"cota[s]?\s+(?:para\s+)?aprendiz(?:es)?",
        r"lei\s+de\s+aprendizagem",
        r"art\.?\s*429",                                     # Art. 429 CLT (Cota de Aprendizagem)
        r"artigo\s*429",
        r"art\.?\s*403",                                     # Art. 403 CLT (Proibição de trabalho a menor)
        r"art\.?\s*7[ºo]?\s*,\s*xxxiii",                     # Inciso da CF contra Trabalho Infantil
        r"explora[cç][aã]o\s+de\s+menor",
        
        # Outros Assuntos Irrelevantes (Assédio, Jornada e Direitos Individuais)
        r"ass[eé]dio\s+moral",
        r"ass[eé]dio\s+sexual",
        r"jornada\s+de\s+trabalho",
        r"horas?\s+extra(?:ordin[aá]ria)?s?",
        r"intervalo\s+intrajornada",
        r"descanso\s+semanal",
        r"atraso\s+no\s+pagamento",
        r"sal[aá]rio[s]?",
        r"fgts",
        r"est[aá]gio[s]?",
        r"estagi[aá]rio[s]?",
    ]
    tem_exclusao = any(re.search(p, texto) for p in padroes_exclusao)
    if tem_exclusao:
        return False

    # 3. Sistema de Pontuação por Relevância de PCD
    score = 0
    
    # Menções à Lei Federal de Cotas (Lei 8.213) -> Relevância Máxima
    if re.search(r"8\.?213", texto):
        score += 2
        
    # Menções ao Artigo da Cota (Artigo 93) -> Relevância Máxima
    if re.search(r"art\.?\s*93|artigo\s*93", texto):
        score += 3
        
    # Menções diretas a termos de deficiência -> Relevância Média
    if re.search(r"pessoa[s]?\s+com\s+defici[eê]ncia|\bpcd\b|\bpcds\b|deficiente[s]?", texto):
        score += 3
        
    # Menções a trabalhadores reabilitados -> Relevância Média
    if re.search(r"reabilitad[oa]s?", texto):
        score += 2
        
    # Menções gerais a reservas ou cotas -> Relevância Baixa
    if re.search(r"cota[s]?|reserva\s+legal", texto):
        score += 1

    # O documento só é aceite se acumular pelo menos 5 pontos de relevância.
    tem_art93 = re.search(r"art\.?\s*93|artigo\s*93", texto)
    tem_pcd = re.search(
    r"pessoa[s]?\s+com\s+defici[eê]ncia|\bpcd\b|\bpcds\b|reabilitad[oa]s?|lei\s+de\s+cotas|cota[s]?\s+(?:pcd|para\s+pessoas?\s+com\s+defici[eê]ncia)",
    texto
   )

    return bool(tem_art93 and tem_pcd)

async def extrair_tacs_do_html(conteudo: str, orgao: str = "") -> list[dict]:
    print("ENTROU NO GEMINI - extrair_tacs_do_html")
    """
    Analisa texto de documento TAC PCD e extrai todos os dados estruturados.
    """
    model = get_model()

# 2. Prompt com instrução de Sistema altamente restritiva no início
    prompt = f"""SISTEMA: VOCÊ É UM FILTRO DE SEGURANÇA. 
    SE O DOCUMENTO NÃO FOR UM "TERMO DE AJUSTAMENTO DE CONDUTA" (TAC) ESPECÍFICO SOBRE A LEI 8.213/91 (COTA DE PCD), VOCÊ DEVE IGNORAR TODO O TEXTO E RETORNAR APENAS: {{"empresas": []}}.
    
    SE O DOCUMENTO FOR SOBRE TRABALHO INFANTIL, APRENDIZAGEM, SEGURANÇA DO TRABALHO, ASSÉDIO OU FRAUDE PROCESSUAL, RETORNE APENAS: {{"empresas": []}}.

    Você é especialista em análise de Termos de Ajuste de Conduta (TAC) do Ministério Público do Trabalho sobre Lei 8.213/91, art. 93 (cotas para PCDs).

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
- Se NÃO for TAC sobre cota PCD (Lei 8.213/91, art. 93, pessoa com deficiência, PCD, reabilitados ou cota legal), retorne {{"empresas": []}}
- Não invente dados. Extraia somente informações explícitas no documento.
- Não estime número de funcionários, cota exigida ou cota cumprida.
- Se não houver evidência documental para números, retorne 0.
- Se não houver evidência documental para textos, retorne "".

- A razao_social deve ser o nome jurídico real da empresa compromissária, requerida, investigada ou empregadora.
- Se houver cidade e empresa no documento, escolha sempre a empresa.
- Não use município, localidade, endereço, comarca, estado ou local de assinatura como razao_social.
- Nunca use cidade, órgão público, comarca, vara, procuradoria ou unidade do MPT como razão social da empresa.
- Nunca retorne valores como "Empresa — PETRÓPOLIS", "Empresa — RIO DE JANEIRO" ou qualquer variação semelhante.
- Priorize nomes que estejam associados a CNPJ, empregadora, empresa, compromissária, requerida ou representante legal.
- Se não for possível identificar claramente a empresa responsável pelo TAC, retorne {{"empresas": []}}.

- responsavel_nome é sempre o representante da EMPRESA, nunca o Procurador do MPT.
- prazo_cumprimento: procure exatamente pela frase "Prazo para o cumprimento desta obrigação:" ou similar.

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
    cfg = GenerationConfig(temperature=0.0, max_output_tokens=2048)
    response = model.generate_content(prompt, generation_config=cfg)

    result = _parse_json(response.text)

    if isinstance(result, dict):
        empresas = result.get("empresas", [])
        return [
            empresa for empresa in empresas
            if _empresa_extraida_valida(empresa)
        ]

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
    Calcula o score de oportunidade de prospecção de candidatos PCD.
    Score determinístico baseado no défice + situação + dimensão da empresa.
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
        pct_deficit = 0.5  # sem dados, assume 50%

    score = round(pct_deficit * 7)  # défice vale 70%

    # Bónus por situação
    if "descumprido" in sit.lower():
        score += 2
    elif "cumprimento" in sit.lower():
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
    Procura dados da empresa na BrasilAPI usando o CNPJ.
    Retorna a dimensão, endereço, telefone, e-mail e razão social oficial.
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