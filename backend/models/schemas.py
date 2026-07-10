from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class SituacaoTAC(str, Enum):
    """
    Enum que mapeia estritamente os três estados possíveis de um TAC no sistema.
    """

    EM_CUMPRIMENTO = "TAC em cumprimento"
    DESCUMPRIDO = "TAC descumprido"
    CUMPRIDO = "TAC cumprido"


class Empresa(BaseModel):
    """
    Modelo utilizado pelo Gemini (response_schema) e pelo backend.
    """

    id: Optional[str] = None

    razao_social: Optional[str] = None
    cnpj: Optional[str] = None

    # Dados básicos do TAC
    orgao: Optional[str] = None
    numero_procedimento: Optional[str] = None
    data_abertura: Optional[str] = None

    motivo: Optional[str] = None
    endereco: Optional[str] = None

    # Contatos
    email: Optional[str] = None
    telefone: Optional[str] = None

    situacao: Optional[SituacaoTAC] = None
    setor: Optional[str] = None

    # ==========================
    # Dados documentais do TAC
    # ==========================

    # null = não informado no PDF
    # 0 = o PDF afirmou explicitamente zero
    num_funcionarios: Optional[int] = None
    cota_exigida: Optional[int] = None
    cota_cumprida: Optional[int] = None
    deficit_pcd: Optional[int] = None

    # ==========================
    # Classificação comercial
    # ==========================

    tipo_lead: Optional[
        Literal[
            "lead_quente",
            "lead_acompanhamento",
            "sem_oportunidade",
        ]
    ] = None

    motivo_lead: Optional[str] = None
    resumo_oportunidade: Optional[str] = None

    # ==========================
    # Informações de prazo
    # ==========================

    prazo_cumprimento: Optional[str] = None
    prazo_cumprimento_data: Optional[str] = None
    prazo_em_aberto: Optional[bool] = None

    # ==========================
    # Evidências do TAC
    # ==========================

    risco_multa: Optional[bool] = None
    obrigacao_contratacao_pcd: Optional[bool] = None
    plano_adequacao_pcd: Optional[bool] = None
    acoes_futuras_inclusao: Optional[bool] = None

    evidencia_textual: Optional[str] = None

    # ==========================
    # Dados externos
    # ==========================

    num_funcionarios_estimado_externo: Optional[int] = None

    origem_num_funcionarios: Optional[
        Literal[
            "pdf",
            "estimativa_externa",
        ]
    ] = None

    # ==========================
    # Persistência
    # ==========================

    pdf_url: Optional[str] = None

    criado_em: Optional[datetime] = Field(default_factory=datetime.utcnow)
    atualizado_em: Optional[datetime] = Field(default_factory=datetime.utcnow)


class ScraperJobRequest(BaseModel):
    """Payload de entrada para iniciar um novo processo assíncrono de raspagem."""

    orgao: Optional[str] = None
    paginas: int = 5
    forcar: bool = False
    buscar_contatos: bool = False


class ScraperJobResponse(BaseModel):
    """Resposta imediata da API ao receber uma solicitação de Job."""

    job_id: str
    status: str
    total_encontrado: int = 0
    message: str


class JobStatus(BaseModel):
    """Modelo para acompanhar a execução de um Job."""

    job_id: str
    status: str
    progresso: int = 0
    log: list[str] = []
    resultado: Optional[dict] = None
