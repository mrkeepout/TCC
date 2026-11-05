#!/usr/bin/env python3
"""
Gera relatorio em PDF (Relatorio_Experimento_Stealth_Surface.pdf)
detalhando a metodologia, o modelo conceitual do quadro, a calibracao no testbed
e os graficos 3D e 2D gerados.
"""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUT_DIR = Path(__file__).resolve().parent / "out" / "stealth_surface"
PDF_PATH = OUT_DIR / "Relatorio_Experimento_Stealth_Surface.pdf"

doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter, leftMargin=45, rightMargin=45, topMargin=45, bottomMargin=45)
styles = getSampleStyleSheet()

primary = colors.HexColor("#1A365D")
secondary = colors.HexColor("#2B6CB0")
dark = colors.HexColor("#2D3748")

title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=primary, alignment=0)
subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor("#718096"))
h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=primary, spaceBefore=14, spaceAfter=6)
h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=secondary, spaceBefore=10, spaceAfter=4)
body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=14, textColor=dark, spaceAfter=6)
bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=12, bulletIndent=4, spaceAfter=4)

story = []

# Cabecalho
story.append(Paragraph("Relatório Técnico de Experimentação: Superfície de Furtividade EDoS", title_style))
story.append(Spacer(1, 4))
story.append(Paragraph("<b>Projeto:</b> Caracterização e Evasão de Ataques Low-and-Slow em Ambientes SDN/EDoS (LST 2.0)<br/>"
                       "<b>Coorientador:</b> Jefferson Abreu | <b>Alunos:</b> Gabriel Lucas & Matheus Alves | <b>Data:</b> Novembro 2026", subtitle_style))
story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=12))

# Secao 1: Formalizacao do Quadro
story.append(Paragraph("1. Formalização do Modelo Conceitual (Quadro)", h1_style))
story.append(Paragraph(
    "Durante a reunião técnica, formulou-se a hipótese de que a detecção de anomalias por distância de Mahalanobis "
    "apresenta uma resposta não-linear convexa (curva em 'U') em relação à cadência temporal do atacante (Δt), "
    "parametrizada pela janela de observação do detector (j):", body_style))

story.append(Paragraph("• <b>Δt pequeno (Δt &lt; 1.0s - Lado Esquerdo do 'U'):</b> Comportamento anômalo clássico de rajada. Dispara métricas de taxa do CICFlowMeter (Flow Bytes/s, Flow Packets/s) e estoura contadores de conexões concorrentes. Acurácia de detecção → 100%.", bullet_style))
story.append(Paragraph("• <b>Δt grande (Δt &gt; 15.0s - Lado Direito do 'U'):</b> Comportamento de retenção excessiva. As conexões acumulam tempos anômalos (Flow Duration elevado, Flow IAT Std extremo) e sofrem quebras na rotação de janelas (Bwd Packets = 0). Acurácia de detecção → 100%.", bullet_style))
story.append(Paragraph("• <b>O Vale de Furtividade (Stealth Valley - Fundo do 'U'):</b> Região ótima onde Δt coincide com o tempo de pensar (think-time) e IAT dos clientes legítimos (~3s a 6s). Nessa zona, a separação estatística desaba, permitindo que o ataque force o autoscaling sem acionar alarmes.", bullet_style))
story.append(Paragraph("• <b>Janela de Observação (j):</b> Janelas maiores (ex: 60s) acumulam maior suporte estatístico e estreitam o vale de evasão, enquanto janelas curtas (ex: 5s–10s) sofrem de miopia, alargando a margem de ataque.", bullet_style))

story.append(Spacer(1, 8))

# Secao 2: Resultados e Figuras
story.append(Paragraph("2. Mapeamento da Superfície e Dados Empíricos", h1_style))
story.append(Paragraph(
    "O modelo foi calibrado com a base experimental coletada no testbed LST 2.0 (50 rodadas da bateria de calibração, "
    "abrangendo janelas j ∈ [5s, 10s, 20s, 30s, 60s] e ataques de 25 a 200 conexões):", body_style))

img3d = str(OUT_DIR / "superficie_stealth_3d.png")
if Path(img3d).exists():
    story.append(Image(img3d, width=480, height=270))
    story.append(Paragraph("<i>Figura 1: Superfície 3D de Detecção (Acurácia = f(Δt, j)). Pontos vermelhos indicam a operação atual do testbed (Δt = 3s).</i>", subtitle_style))
    story.append(Spacer(1, 8))

img2d = str(OUT_DIR / "contorno_stealth_2d.png")
if Path(img2d).exists():
    story.append(Image(img2d, width=450, height=250))
    story.append(Paragraph("<i>Figura 2: Mapa de contorno destacando a Zona Ótima de Evasão (Stealth Valley) entre 3.5s e 6.5s.</i>", subtitle_style))
    story.append(Spacer(1, 10))

# Secao 3: Conclusoes e Recomendacoes
story.append(Paragraph("3. Conclusões e Próximos Passos para Gabriel e Matheus", h1_style))
table_data = [
    [Paragraph("<b>Achado Experimental</b>", body_style), Paragraph("<b>Ação Recomendada para o TCC</b>", body_style)],
    [Paragraph("O ataque atual (Δt = 3s fixo) cai na borda de transição, sendo pego com >94% de acurácia pelo Mahalanobis.", body_style),
     Paragraph("Implementar jitter aleatório no intervalo de envio (Δt ~ Uniform(3.5s, 6.5s)) em vez de taxa constante.", body_style)],
    [Paragraph("Janelas curtas (j = 5s) têm FPR maior e menor capacidade de distinguir fluxos no vale.", body_style),
     Paragraph("Documentar o trade-off de defesa: janelas longas detectam melhor, mas atrasam a reação do autoscaler.", body_style)],
    [Paragraph("Persistência em Keep-Alive mascara o payload.", body_style),
     Paragraph("Manter a mitigação de completar 1 requisição GET antes de segurar o socket (Keep-Alive piggybacking).", body_style)]
]
t = Table(table_data, colWidths=[240, 280])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
    ('TEXTCOLOR', (0,0), (-1,0), primary),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('TOPPADDING', (0,0), (-1,-1), 5),
]))
story.append(t)

doc.build(story)
print(f"Relatório PDF gerado: {PDF_PATH}")
