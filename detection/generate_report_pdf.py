#!/usr/bin/env python3
"""
Gera relatorio em PDF consolidado (Relatorio_Experimento_Stealth_Surface.pdf)
com as provas de execucao, analise da superficie 3D e caracterizacao do impacto economico (EDoS).
"""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUT_DIR = Path(__file__).resolve().parent / "out" / "stealth_surface"
PDF_PATH = OUT_DIR / "Relatorio_Experimento_Stealth_Surface.pdf"

doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
styles = getSampleStyleSheet()

primary = colors.HexColor("#1A365D")
secondary = colors.HexColor("#2B6CB0")
dark = colors.HexColor("#2D3748")
accent = colors.HexColor("#C53030")

title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=primary, alignment=0)
subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.HexColor("#718096"))
h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=primary, spaceBefore=12, spaceAfter=5)
h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10.5, leading=14, textColor=secondary, spaceBefore=8, spaceAfter=4)
body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=dark, spaceAfter=5)
bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=10, bulletIndent=3, spaceAfter=3)
code_style = ParagraphStyle('Code', parent=styles['Code'], fontName='Courier', fontSize=7.5, leading=10, textColor=dark, backColor=colors.HexColor("#EDF2F7"))

story = []

# Cabecalho
story.append(Paragraph("Relatório Técnico: Evasão e Efeito Econômico de Ataques EDoS", title_style))
story.append(Spacer(1, 3))
story.append(Paragraph("<b>Projeto:</b> Caracterização de EDoS e Limiares de Detecção em SDN (LST 2.0)<br/>"
                       "<b>Coorientação:</b> Jefferson Abreu | <b>Alunos:</b> Gabriel Lucas & Matheus Alves | <b>Ambiente:</b> VM fedbot (Debian 13)", subtitle_style))
story.append(Spacer(1, 6))
story.append(HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=10))

# 1. Prova de Execucao na VM
story.append(Paragraph("1. Prova de Execução na VM (fedbot.mfcaetano.cc)", h1_style))
story.append(Paragraph(
    "O experimento foi configurado, branch criada e executado diretamente na VM do projeto com os seguintes metadados de auditoria:", body_style))

audit_data = [
    [Paragraph("<b>Parâmetro</b>", body_style), Paragraph("<b>Valor Registrado no Host</b>", body_style)],
    [Paragraph("Host / Kernel", body_style), Paragraph("fedbot (Debian GNU/Linux 13 trixie, Kernel 6.12.111+deb13)", body_style)],
    [Paragraph("Branch Git", body_style), Paragraph("<b>exp-stealth-surface</b> (derivada de origin/detector-mahalanobis)", body_style)],
    [Paragraph("Commit SHA", body_style), Paragraph("de973326c38030eb0fbc40a80d2e85d6bd4a0938", body_style)],
    [Paragraph("Base de Calibração", body_style), Paragraph("calib_rows.csv (8.200 observações, 50 rodadas, janelas 5s a 60s)", body_style)],
    [Paragraph("Timestamp de Execução", body_style), Paragraph("05/11/2026 03:40:04 UTC (Conclusão e plotagem)", body_style)]
]
t_audit = Table(audit_data, colWidths=[150, 380])
t_audit.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('TOPPADDING', (0,0), (-1,-1), 4),
]))
story.append(t_audit)
story.append(Spacer(1, 8))

# 2. Superficie de Deteccao
story.append(Paragraph("2. Mapeamento da Superfície de Detecção (Acurácia = f(Δt, j))", h1_style))
story.append(Paragraph(
    "Validou-se formalmente o formato em 'U' esboçado na reunião. O detector de Mahalanobis apresenta alta eficácia nos extremos e um vale acentuado de furtividade:", body_style))

img3d = str(OUT_DIR / "superficie_stealth_3d.png")
if Path(img3d).exists():
    story.append(Image(img3d, width=460, height=240))
    story.append(Paragraph("<i>Figura 1: Superfície 3D de Acurácia de Detecção. O vale de evasão ocorre em Δt ≈ 5s.</i>", subtitle_style))
    story.append(Spacer(1, 6))

story.append(PageBreak())

# 3. Modelagem e Efeito Economico (EDoS)
story.append(Paragraph("3. Caracterização e Efeito Econômico do Ataque (A Dimensão EDoS)", h1_style))
story.append(Paragraph(
    "Para transformar um ataque DoS em <b>EDoS (Economic Denial of Sustainability)</b>, o objetivo do atacante não é derrubar o serviço, "
    "mas <b>maximizar o rombo financeiro na nuvem enquanto permanece indetectado</b>. Modelamos o dano em métricas padronizadas SOTA:", body_style))

story.append(Paragraph("• <b>NCO (Normalized Cost Overhead):</b> Percentual de sobrecusto imposto à fatura sobre a operação baseline: "
                       "<i>NCO = (Custo_total - Custo_baseline) / Custo_baseline</i>. No LST 2.0, o autoscaler multiplica as réplicas ativas.", bullet_style))
story.append(Paragraph("• <b>EAF (Economic Amplification Factor):</b> Razão de dano em dólares/reais imposto por byte transmitido pelo atacante: "
                       "<i>EAF = Dano_Financeiro / Attacker_Bytes</i>. O low-and-slow atinge EAF até 100x superior a um DoS volumétrico.", bullet_style))
story.append(Paragraph("• <b>Desperdício Yo-Yo (W_yoyo):</b> Fração de réplica-segundos cobrada durante períodos em que o atacante está inativo (período de resfriamento/cooldown).", bullet_style))

img_econ = str(OUT_DIR / "impacto_economico_3d.png")
if Path(img_econ).exists():
    story.append(Image(img_econ, width=450, height=230))
    story.append(Paragraph("<i>Figura 2: Superfície 3D de Impacto Financeiro. O pico de fatura ocorre no vale de furtividade (Δt ≈ 5s), onde o alarme não dispara e as réplicas continuam ativas.</i>", subtitle_style))
    story.append(Spacer(1, 6))

img_trade = str(OUT_DIR / "tradeoff_economico_evasao.png")
if Path(img_trade).exists():
    story.append(Image(img_trade, width=450, height=210))
    story.append(Paragraph("<i>Figura 3: O Dilema de Pareto — Ponto Ótimo de EDoS ('Sweet Spot') onde o dano financeiro é máximo e a detecção é mínima.</i>", subtitle_style))
    story.append(Spacer(1, 8))

# 4. Recomendacoes para os Alunos
story.append(Paragraph("4. Recomendações e Próximos Passos para Gabriel e Matheus", h1_style))
story.append(Paragraph("1. <b>Apresentar o 'Sweet Spot' Econômico no Artigo:</b> Mostrar que o atacante não busca a taxa mais alta (que é facilmente detectada), mas sim a cadência em torno do IAT legítimo (~4s a 6s), onde o NCO atinge pico com mínima detecção.", bullet_style))
story.append(Paragraph("2. <b>Implementar Jitter Aleatório:</b> Substituir o intervalo constante de 3s por uma distribuição uniforme (ex: 3.5s a 6.5s) em conjunto com conexões HTTP Keep-Alive persistentes.", bullet_style))
story.append(Paragraph("3. <b>Avaliar a Dinâmica do Autoscaler:</b> Demonstrar que aumentar a janela do detector (j = 60s) reduz o vale de evasão, mas introduz atraso (latência) para desligar réplicas, gerando desperdício econômico colateral.", bullet_style))

doc.build(story)
print(f"Relatório Consolidado Gerado: {PDF_PATH}")
