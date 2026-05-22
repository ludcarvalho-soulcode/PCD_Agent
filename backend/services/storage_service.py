"""
Storage + PDF Service
- Gera PDF de fichas individuais e relatórios consolidados (ReportLab)
- Armazena no Cloud Storage com URL assinada
"""
import io
import os
from datetime import datetime, timedelta
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

PROJECT_ID  = os.getenv("GCP_PROJECT_ID", "seu-projeto-gcp")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "tac-pcd-relatorios")

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

    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(days=7),
        method="GET",
    )
    return url


def _situacao_cor(situacao: str) -> colors.Color:
    s = (situacao or "").lower()
    if "descumprido" in s: return VERMELHO
    if "cumprido" in s and "em" not in s: return VERDE
    return LARANJA


def _nivel_cor(nivel: str) -> colors.Color:
    n = (nivel or "").lower()
    if "alta"  in n: return VERMELHO
    if "média" in n: return LARANJA
    return VERDE


# ──────────────────────────────
# FICHA INDIVIDUAL
# ──────────────────────────────

def gerar_pdf_empresa(empresa: dict) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()

    def s(name, **kw):
        base = styles[name]
        return ParagraphStyle(base.name + "_custom", parent=base, **kw)

    titulo_style    = s("Heading1", fontSize=16, textColor=AZUL_MPT, spaceAfter=4)
    subtitulo_style = s("Normal",   fontSize=10, textColor=CINZA)
    label_style     = s("Normal",   fontSize=9,  textColor=CINZA, spaceBefore=8)
    valor_style     = s("Normal",   fontSize=11, textColor=colors.black)
    secao_style     = s("Heading2", fontSize=12, textColor=AZUL_MPT, spaceBefore=16, spaceAfter=6)

    oport = empresa.get("oportunidade", {})
    situacao = empresa.get("situacao", "")

    story = [
        # Cabeçalho
        Paragraph("MINISTÉRIO PÚBLICO DO TRABALHO — PRT2 SÃO PAULO", s("Normal", fontSize=9, textColor=CINZA)),
        Paragraph("Ficha de Empresa com TAC PCD", titulo_style),
        Paragraph(f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}", subtitulo_style),
        HRFlowable(width="100%", thickness=2, color=AZUL_MPT, spaceAfter=16),

        # Situação
        Table(
            [[
                Paragraph("Situação do TAC", s("Normal", fontSize=9, textColor=colors.white)),
                Paragraph(situacao, s("Normal", fontSize=10, textColor=colors.white, fontName="Helvetica-Bold")),
                Paragraph("Score de Oportunidade", s("Normal", fontSize=9, textColor=colors.white)),
                Paragraph(f"{oport.get('score_oportunidade', '?')}/10 — {oport.get('nivel', '?')}",
                          s("Normal", fontSize=10, textColor=colors.white, fontName="Helvetica-Bold")),
            ]],
            colWidths=["22%", "28%", "25%", "25%"],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), AZUL_MPT),
                ("ROWPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]),
        ),
        Spacer(1, 16),

        # Dados da empresa
        Paragraph("Dados da Empresa", secao_style),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),

        Paragraph("Razão Social", label_style),
        Paragraph(empresa.get("razao_social", "-"), s("Normal", fontSize=13, fontName="Helvetica-Bold")),

        Paragraph("CNPJ", label_style),
        Paragraph(empresa.get("cnpj", "-"), valor_style),

        Paragraph("Setor / Segmento", label_style),
        Paragraph(empresa.get("setor", "-"), valor_style),

        Paragraph("Número de Funcionários", label_style),
        Paragraph(str(empresa.get("num_funcionarios", "-")), valor_style),

        # Procedimento
        Paragraph("Dados do Procedimento", secao_style),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),

        Paragraph("Número do Procedimento", label_style),
        Paragraph(empresa.get("numero_procedimento", "-"), valor_style),

        Paragraph("Órgão Responsável", label_style),
        Paragraph(empresa.get("orgao", "-"), valor_style),

        Paragraph("Data de Abertura", label_style),
        Paragraph(empresa.get("data_abertura", "-"), valor_style),

        Paragraph("Motivo / Infração", label_style),
        Paragraph(empresa.get("motivo", "-"), s("Normal", fontSize=10, textColor=VERMELHO)),

        # Cota PCD
        Paragraph("Situação da Cota PCD", secao_style),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),
        Spacer(1, 8),

        Table(
            [
                ["PCDs Exigidos por Lei", "PCDs Cumpridos", "Déficit"],
                [
                    str(empresa.get("cota_exigida", "-")),
                    str(empresa.get("cota_cumprida", "-")),
                    str(max(0, (empresa.get("cota_exigida") or 0) - (empresa.get("cota_cumprida") or 0))),
                ],
            ],
            colWidths=["33%", "33%", "34%"],
            style=TableStyle([
                ("BACKGROUND",   (0, 0), (-1, 0), AZUL_MPT),
                ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
                ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",     (0, 0), (-1, -1), 11),
                ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
                ("ROWPADDING",   (0, 0), (-1, -1), 10),
                ("GRID",         (0, 0), (-1, -1), 0.5, CINZA),
                ("BACKGROUND",   (2, 1), (2, 1), colors.HexColor("#FEF2F2")),
                ("TEXTCOLOR",    (2, 1), (2, 1), VERMELHO),
                ("FONTNAME",     (2, 1), (2, 1), "Helvetica-Bold"),
            ]),
        ),

        # Contato
        Paragraph("Dados de Contato", secao_style),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),

        Paragraph("Endereço", label_style),
        Paragraph(empresa.get("endereco", "Não informado"), valor_style),
        Paragraph("E-mail", label_style),
        Paragraph(empresa.get("email", "Não informado"), s("Normal", fontSize=11, textColor=AZUL_MPT)),
        Paragraph("Telefone", label_style),
        Paragraph(empresa.get("telefone", "Não informado"), valor_style),

        # Oportunidade
        Paragraph("Análise de Oportunidade de Prospecção", secao_style),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),
        Spacer(1, 8),

        Paragraph(oport.get("recomendacao", "-"), s("Normal", fontSize=11, textColor=colors.HexColor("#1F2937"))),
        Spacer(1, 8),
        Paragraph("Perfis sugeridos para prospecção:", label_style),
        *[Paragraph(f"• {p}", s("Normal", fontSize=10)) for p in (oport.get("perfis_sugeridos") or [])],

        Spacer(1, 24),
        HRFlowable(width="100%", thickness=0.5, color=CINZA),
        Paragraph(
            "Este documento é gerado automaticamente pelo Agente TAC PCD para fins de prospecção de candidatos com deficiência.",
            s("Normal", fontSize=8, textColor=CINZA),
        ),
    ]

    doc.build(story)
    buf.seek(0)
    return buf


# ──────────────────────────────
# RELATÓRIO CONSOLIDADO
# ──────────────────────────────

def gerar_relatorio_consolidado(empresas: list[dict]) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()

    def s(name, **kw):
        return ParagraphStyle(styles[name].name + "_r", parent=styles[name], **kw)

    story = [
        Paragraph("MINISTÉRIO PÚBLICO DO TRABALHO — PRT2 SÃO PAULO", s("Normal", fontSize=9, textColor=CINZA)),
        Paragraph("Relatório de Empresas com TAC PCD", s("Heading1", fontSize=18, textColor=AZUL_MPT)),
        Paragraph(f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} · {len(empresas)} empresas",
                  s("Normal", fontSize=10, textColor=CINZA)),
        HRFlowable(width="100%", thickness=2, color=AZUL_MPT, spaceAfter=20),

        # Resumo
        Paragraph("Resumo Executivo", s("Heading2", fontSize=13, textColor=AZUL_MPT)),
        Spacer(1, 8),
        Table(
            [
                ["Total de TACs", "Com e-mail", "Com telefone", "Score médio"],
                [
                    str(len(empresas)),
                    str(sum(1 for e in empresas if e.get("email"))),
                    str(sum(1 for e in empresas if e.get("telefone"))),
                    f"{sum(e.get('oportunidade', {}).get('score_oportunidade', 0) for e in empresas) / max(len(empresas), 1):.1f}",
                ],
            ],
            colWidths=["25%", "25%", "25%", "25%"],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), AZUL_MPT),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE",   (0, 0), (-1, -1), 12),
                ("ROWPADDING", (0, 0), (-1, -1), 10),
                ("GRID",       (0, 0), (-1, -1), 0.5, CINZA),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F0F9FF")),
            ]),
        ),
        Spacer(1, 24),

        # Tabela de empresas
        Paragraph("Lista de Empresas", s("Heading2", fontSize=13, textColor=AZUL_MPT)),
        Spacer(1, 8),
        Table(
            [["Empresa", "CNPJ", "Órgão", "Situação", "Score"]] + [
                [
                    Paragraph(e.get("razao_social", "-")[:35], s("Normal", fontSize=8)),
                    Paragraph(e.get("cnpj", "-"),              s("Normal", fontSize=8)),
                    Paragraph(e.get("orgao", "-")[:20],        s("Normal", fontSize=8)),
                    Paragraph(e.get("situacao", "-"),           s("Normal", fontSize=8)),
                    Paragraph(str(e.get("oportunidade", {}).get("score_oportunidade", "-")),
                              s("Normal", fontSize=9, fontName="Helvetica-Bold")),
                ]
                for e in sorted(empresas,
                                key=lambda x: x.get("oportunidade", {}).get("score_oportunidade", 0),
                                reverse=True)
            ],
            colWidths=["32%", "18%", "20%", "20%", "10%"],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), AZUL_MPT),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",   (0, 0), (-1, 0), 9),
                ("GRID",       (0, 0), (-1, -1), 0.3, CINZA),
                ("ROWPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
                ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ]),
        ),
        Spacer(1, 24),
        PageBreak(),
    ]

    # Uma página por empresa
    for emp in empresas:
        story += [
            Paragraph(emp.get("razao_social", "-"), s("Heading2", fontSize=13, textColor=AZUL_MPT)),
            Paragraph(f"{emp.get('numero_procedimento', '')} · {emp.get('orgao', '')}",
                      s("Normal", fontSize=9, textColor=CINZA)),
            HRFlowable(width="100%", thickness=0.5, color=CINZA, spaceAfter=8),
            Paragraph(emp.get("motivo", "-"), s("Normal", fontSize=10, textColor=VERMELHO)),
            Spacer(1, 6),
            Paragraph(f"Endereço: {emp.get('endereco', '-')}", s("Normal", fontSize=10)),
            Paragraph(f"E-mail: {emp.get('email', 'N/A')}  |  Tel: {emp.get('telefone', 'N/A')}",
                      s("Normal", fontSize=10)),
            Spacer(1, 6),
            Paragraph(
                emp.get("oportunidade", {}).get("recomendacao", "-"),
                s("Normal", fontSize=10, textColor=colors.HexColor("#1D4ED8")),
            ),
            Spacer(1, 20),
        ]

    doc.build(story)
    buf.seek(0)
    return buf


# ──────────────────────────────
# API PÚBLICA
# ──────────────────────────────

async def gerar_e_salvar_pdf_empresa(empresa: dict) -> str:
    buf      = gerar_pdf_empresa(empresa)
    nome     = (empresa.get("razao_social") or "empresa").replace(" ", "_")[:40]
    blob     = f"fichas/{nome}_{empresa.get('id', 'x')}.pdf"
    return _upload_pdf(buf, blob)


async def gerar_e_salvar_relatorio(empresas: list[dict], job_id: str) -> str:
    buf  = gerar_relatorio_consolidado(empresas)
    blob = f"relatorios/relatorio_tac_pcd_{job_id}.pdf"
    return _upload_pdf(buf, blob)
