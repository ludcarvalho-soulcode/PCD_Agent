"""
Storage + PDF Service
- Gera PDF de fichas individuais e relatórios consolidados (ReportLab)
- Armazena no Cloud Storage com URL assinada
Localização: backend/services/storage_service.py
"""
import io
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import storage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak,
)

PROJECT_ID  = os.getenv("GCP_PROJECT_ID", "tutores-lms")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "tac-pcd-relatorios")

# Cores MPT
AZUL_MPT  = colors.HexColor("#003E7E")
CINZA     = colors.HexColor("#6B7280")
LARANJA   = colors.HexColor("#D97706")
VERMELHO  = colors.HexColor("#DC2626")
VERDE     = colors.HexColor("#16A34A")

_storage_client: Optional[storage.Client] = None

def get_storage() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client

def _upload_pdf(buffer: io.BytesIO, blob_name: str) -> str:
    """Faz upload do PDF para o GCS e retorna URL assinada (7 dias)."""
    client = get_storage()
    bucket = client.bucket(BUCKET_NAME)
    blob   = bucket.blob(blob_name)

    buffer.seek(0)
    blob.upload_from_file(buffer, content_type="application/pdf")

    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(days=7),
        method="GET",
    )

def gerar_pdf_empresa(empresa: dict) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()

    def s(name, **kw):
        return ParagraphStyle(styles[name].name + "_c", parent=styles[name], **kw)

    titulo_style = s("Heading1", fontSize=16, textColor=AZUL_MPT, spaceAfter=4)
    secao_style = s("Heading2", fontSize=12, textColor=AZUL_MPT, spaceBefore=16, spaceAfter=6)
    
    oport = empresa.get("oportunidade", {})
    
    story = [
        Paragraph("MINISTÉRIO PÚBLICO DO TRABALHO", s("Normal", fontSize=9, textColor=CINZA)),
        Paragraph("Ficha de Empresa com TAC PCD", titulo_style),
        HRFlowable(width="100%", thickness=2, color=AZUL_MPT),
        Spacer(1, 12),
        Paragraph("Razão Social", s("Normal", fontSize=9, textColor=CINZA)),
        Paragraph(empresa.get("razao_social", "-"), s("Normal", fontSize=13, fontName="Helvetica-Bold")),
        Paragraph("Dados do Procedimento", secao_style),
        Paragraph(f"Nº: {empresa.get('numero_procedimento', '-')}", s("Normal", fontSize=10)),
        Paragraph(f"Órgão: {empresa.get('orgao', '-')}", s("Normal", fontSize=10)),
    ]
    
    doc.build(story)
    buf.seek(0)
    return buf

def gerar_relatorio_consolidado(empresas: list[dict]) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    
    story = [Paragraph("Relatório Consolidado de TACs", styles["Heading1"])]
    # ... adicione aqui o restante da sua lógica de tabela ...
    
    doc.build(story)
    buf.seek(0)
    return buf

async def gerar_e_salvar_pdf_empresa(empresa: dict) -> str:
    buf = gerar_pdf_empresa(empresa)
    nome = (empresa.get("razao_social") or "empresa").replace(" ", "_")[:40]
    blob = f"fichas/{nome}_{empresa.get('id', 'x')}.pdf"
    return _upload_pdf(buf, blob)

async def gerar_e_salvar_relatorio(empresas: list[dict], job_id: str) -> str:
    buf = gerar_relatorio_consolidado(empresas)
    blob = f"relatorios/relatorio_tac_pcd_{job_id}.pdf"
    return _upload_pdf(buf, blob)