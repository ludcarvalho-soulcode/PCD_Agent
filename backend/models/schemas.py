from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class SituacaoTAC(str, Enum):
    """
    Enum que mapeia estritamente os três estados possíveis de um TAC no sistema.
    O Gemini usará estes valores exatos para preencher o campo 'situacao'.
    """
    EM_CUMPRIMENTO = "TAC em cumprimento"
    DESCUMPRIDO    = "TAC descumprido"
    CUMPRIDO       = "TAC cumprido"


class Empresa(BaseModel):
    """
    Modelo que define a estrutura de dados de uma empresa extraída de um TAC de PCD.
    Passado diretamente no 'response_schema' do Vertex AI para forçar o Gemini 
    a responder com um formato JSON idêntico a esta tipagem.
    """
    id:                 Optional[str]       = None
    razao_social:       str
    cnpj:               str
    
    # Campos obrigatórios populados pelo contexto ou extração do Gemini
    orgao:              str  # Fornecido via parâmetro 'orgao' no serviço
    numero_procedimento: str # Número do Inquérito Civil / IC / Procedimento do MPT
    data_abertura:       str  # Data de assinatura/celebração do TAC (DD/MM/AAAA)
    
    motivo:             str  # Resumo de 2-3 frases sobre o cenário do documento
    endereco:           str  # Endereço completo extraído com CEP
    
    # Contatos da empresa (Opcionais, tratados posteriormente no enriquecimento)
    email:              Optional[str]       = None
    telefone:           Optional[str]       = None
    
    situacao:           SituacaoTAC  # Validação estrita baseada no Enum acima
    setor:              str          # Setor econômico identificado na extração
    
    # Dados numéricos e métricas de cotas (validados na camada de negócio)
    num_funcionarios:   int  # Total de funcionários da empresa
    cota_exigida:       int  # Quantidade de PCDs exigida por lei
    cota_cumprida:      int  # Quantidade de PCDs contratados na assinatura do TAC
    
    # Metadados de controle do sistema e banco de dados
    pdf_url:            Optional[str]       = None
    criado_em:          Optional[datetime]  = Field(default_factory=datetime.utcnow)
    atualizado_em:      Optional[datetime]  = Field(default_factory=datetime.utcnow)


class ScraperJobRequest(BaseModel):
    """Payload de entrada para iniciar um novo processo assíncrono de raspagem."""
    orgao:      Optional[str] = None   # Filtro opcional por órgão (ex: "MPT SP")
    paginas:    int           = 5      # Quantidade de páginas a raspar por execução
    forcar:     bool          = False  # Se True, ignora o cache do Firestore e refaz a análise


class ScraperJobResponse(BaseModel):
    """Resposta imediata da API ao receber uma solicitação de Job."""
    job_id:           str  # UUID único do Job criado
    status:           str  # Status inicial (geralmente 'pending' ou 'running')
    total_encontrado: int = 0
    message:          str


class JobStatus(BaseModel):
    """Modelo de consulta para acompanhar o progresso e o log de execução de um Job."""
    job_id:    str
    status:    str           # pending | running | done | error
    progresso: int = 0       # Progresso percentual (0 a 100)
    log:       list[str] = [] # Histórico de logs textuais gerados pelo Worker
    resultado: Optional[dict] = None # Dados finais consolidados após o término do Job