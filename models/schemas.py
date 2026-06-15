from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class SituacaoTAC(str, Enum):
    EM_CUMPRIMENTO = "TAC em cumprimento"
    DESCUMPRIDO    = "TAC descumprido"
    CUMPRIDO       = "TAC cumprido"


class Empresa(BaseModel):
    id:                Optional[str]       = None
    razao_social:      str
    cnpj:              str
    orgao:             str
    numero_procedimento: str
    data_abertura:     str
    motivo:            str
    endereco:          str
    email:             Optional[str]       = None
    telefone:          Optional[str]       = None
    situacao:          SituacaoTAC
    setor:             str
    num_funcionarios:  int
    cota_exigida:      int
    cota_cumprida:     int
    pdf_url:           Optional[str]       = None
    criado_em:         Optional[datetime]  = Field(default_factory=datetime.utcnow)
    atualizado_em:     Optional[datetime]  = Field(default_factory=datetime.utcnow)


class ScraperJobRequest(BaseModel):
    orgao:      Optional[str] = None   # filtro opcional por órgão
    paginas:    int            = 5     # quantas páginas raspar
    forcar:     bool           = False # ignorar cache Firestore


class ScraperJobResponse(BaseModel):
    job_id:        str
    status:        str
    total_encontrado: int = 0
    message:       str


class JobStatus(BaseModel):
    job_id:    str
    status:    str           # pending | running | done | error
    progresso: int = 0       # 0-100
    log:       list[str] = []
    resultado: Optional[dict] = None
