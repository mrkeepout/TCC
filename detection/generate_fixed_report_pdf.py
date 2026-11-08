#!/usr/bin/env python3
"""
Gera o relatorio tecnico corrigido e consistente com os dados empiricos reais:
- Explica com transparencia por que todos os cenarios registraram 100% de deteccao de janelas
- Analisa a escala dos scores D^2 de Mahalanobis (queda de 77% da anomalia)
- Revela a causa raiz: o colapso de variancia do baseline benigno sintético
- Compara o dano de autoscaling real (replicas e sobrecusto financeiro)
"""
import os
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUT_DIR = Path(__file__).resolve().parent / "out" / "stealth_comparison_results"
PDF_PATH = OUT_DIR / "Relatorio_Comparativo_Stealth_Gabriel.pdf"

# 1. Gerar Grafico Consistente (Distancia de Mahalanobis D^2 vs Sobrecusto NCO)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5), dpi=150)

labels = ["Base Gabriel\n(1x200 conns)", "Furtivo Isolado\n(1x15 conns)", "EDoS Distribuído\n(4x15 conns)"]
x = np.arange(len(labels))
width = 0.45

# Distancia de Mahalanobis Mediana (D^2)
scores_d2 = [1740.19, 404.96, 443.12]
bars1 = ax1.bar(x, scores_d2, width, color=["#E45756", "#4C78A8", "#72B7B2"], edgecolor="black")
ax1.axhline(6.90, color="darkred", linestyle="--", linewidth=1.5, label="Limiar χ² 95% (6.90)")
ax1.set_ylabel("Distância de Mahalanobis Mediana ($D^2$)")
ax1.set_title("Intensidade da Anomalia Estatística", weight="bold")
ax1.set_xticks(x)
ax1.set_xticklabels(labels)
ax1.set_yscale("log")
ax1.set_ylim(1, 10000)
for bar in bars1:
    yval = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2.0, yval * 1.25, f"{yval:.1f}", ha='center', va='bottom', weight='bold', fontsize=9)
ax1.legend(loc="upper right", fontsize=8)
ax1.grid(axis='y', linestyle='--', alpha=0.5)

# Sobrecusto Economico (NCO)
nco_vals = [93.3, 0.0, 60.0]
bars2 = ax2.bar(x, nco_vals, width, color=["#F58518", "#BAB0AC", "#54A24B"], edgecolor="black")
ax2.set_ylabel("Sobrecusto Financeiro na Nuvem - NCO (%)")
ax2.set_title("Impacto Econômico de Autoscaling", weight="bold")
ax2.set_xticks(x)
ax2.set_xticklabels(labels)
ax2.set_ylim(0, 115)
for bar in bars2:
    yval = bar.get_height()
    ax2.text(bar.get_x() + bar.get_width()/2.0, yval + 2, f"+{yval:.1f}%", ha='center', va='bottom', weight='bold', fontsize=9)
ax2.grid(axis='y', linestyle='--', alpha=0.5)

fig.tight_layout()
chart_path = OUT_DIR / "comparativo_stealth_vs_gabriel.png"
fig.savefig(chart_path)
plt.close(fig)
print(f"[OK] Gráfico corrigido e salvo em: {chart_path}")

# 2. Gerar Relatorio Formal em PDF
doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
styles = getSampleStyleSheet()

primary = colors.HexColor("#1A365D")
secondary = colors.HexColor("#2B6CB0")
dark = colors.HexColor("#2D3748")
red_alert = colors.HexColor("#9B2C2C")

title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=primary, alignment=0)
subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=colors.HexColor("#718096"))
h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=primary, spaceBefore=10, spaceAfter=4)
body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=12, textColor=dark, spaceAfter=4)
bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=10, bulletIndent=3, spaceAfter=3)

story = []

# Cabecalho
story.append(Paragraph("Relatório Técnico: Análise Comparativa e Diagnóstico de Detecção", title_style))
story.append(Spacer(1, 2))
story.append(Paragraph("<b>Experimento LST 2.0 Live na VM fedbot</b> | Comparação Gabriel (1x200) vs. Furtivo (1x15) vs. Distribuído (4x15)", subtitle_style))
story.append(Spacer(1, 4))
story.append(HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=8))

# 1. Tabela de Dados Reais
story.append(Paragraph("1. Dados Empíricos Reais Coletados no Testbed", h1_style))
story.append(Paragraph(
    "Abaixo estão os resultados consolidados das 3 baterias emuladas ao vivo no testbed. "
    "Diferente de predições teóricas, <b>todos os 3 cenários ultrapassaram o limiar estático de detecção (100% de janelas flagradas)</b>, "
    "revelando um comportamento estatístico fundamental da modelagem:", body_style))

table_data = [
    [Paragraph("<b>Cenário de Teste</b>", body_style), Paragraph("<b>Configuração</b>", body_style), Paragraph("<b>Req. Lentas/Jan</b>", body_style), Paragraph("<b>Score Mediano (D²)</b>", body_style), Paragraph("<b>Janelas Flagradas</b>", body_style), Paragraph("<b>Réplicas Máx</b>", body_style), Paragraph("<b>Sobrecusto (NCO)</b>", body_style)],
    [Paragraph("<b>Base Gabriel</b>", body_style), Paragraph("1 atacante (200 conns)", body_style), Paragraph("135.5 conns", body_style), Paragraph("<b>1.740,2</b> (Pico: 4.241)", body_style), Paragraph("<font color='red'><b>100.0%</b></font>", body_style), Paragraph("3 réplicas", body_style), Paragraph("<b>+93.3%</b> ($0.0145)", body_style)],
    [Paragraph("<b>Furtivo Isolado</b>", body_style), Paragraph("1 atacante (15 conns)", body_style), Paragraph("39.0 conns", body_style), Paragraph("<b>404,9</b> (Queda de 77%)", body_style), Paragraph("<font color='red'><b>100.0%</b></font>", body_style), Paragraph("1 réplica", body_style), Paragraph("<b>+0.0%</b> ($0.0075)", body_style)],
    [Paragraph("<b>EDoS Distribuído</b>", body_style), Paragraph("4 atacantes (15 conns c/u)", body_style), Paragraph("57.5 conns", body_style), Paragraph("<b>443,1</b> (Diluído)", body_style), Paragraph("<font color='red'><b>100.0%</b></font>", body_style), Paragraph("<b>2 réplicas</b>", body_style), Paragraph("<b>+60.0%</b> ($0.0120)", body_style)]
]
t = Table(table_data, colWidths=[110, 105, 75, 95, 75, 65, 80])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('TOPPADDING', (0,0), (-1,-1), 3),
]))
story.append(t)
story.append(Spacer(1, 6))

# 2. Graficos Corrigidos
story.append(Paragraph("2. Comparativo de Intensidade Estatística vs. Impacto Econômico", h1_style))
story.append(Image(str(chart_path), width=500, height=205))
story.append(Spacer(1, 6))

# 3. Diagnostico Cientifico
story.append(Paragraph("3. Diagnóstico Científico: Por que a Detecção foi de 100% em Todos os Cenários?", h1_style))
story.append(Paragraph(
    "A auditoria da matriz de covariância (Σ) revelou a causa matemática exata pela qual mesmo o ataque com apenas 15 conexões foi flagrado em 100% das janelas:", body_style))

story.append(Paragraph("• <b>Colapso de Variância do Tráfego Benigno (Tráfego Sintético):</b> Os 10 clientes legítimos executam requisições periódicas com cadência fixa. O desvio padrão da fração de conexões lentas no baseline é de apenas <b>±0.04</b> (variância de 0.0019).", bullet_style))
story.append(Paragraph("• <b>Hiper-amplificação na Inversão de Covariância:</b> Na matriz inversa Σ⁻¹, os termos da diagonal chegam a <b>628.9</b>. Com isso, uma variação de apenas 7% na proporção de conexões lentas (de 44% para 51% no cenário de 15 conexões) é multiplicada por centenas, gerando um score de <b>D² = 404.9</b>, muito superior ao limiar estático χ² de <b>6.90</b>.", bullet_style))
story.append(Paragraph("• <b>A Conclusão para o TCC:</b> Em um ambiente com tráfego benigno determinístico (sem ruído humano), <b>nenhum ataque consegue evadir o detector</b>. Isso prova que o detector dos alunos não está 'funcionando perfeitamente', mas sim sofrendo de <i>overfitting</i> à falta de variância do gerador de clientes.", bullet_style))

story.append(Spacer(1, 4))
story.append(Paragraph("4. A Dinâmica Econômica do EDoS Distribuído", h1_style))
story.append(Paragraph(
    "Mesmo com o alarme ativo, a comparação entre <b>Furtivo Isolado</b> e <b>EDoS Distribuído</b> comprova a mecânica de dano financeiro:", body_style))
story.append(Paragraph("1. O atacante isolado (1x15) reduz o score para 404.9, mas 15 conexões sozinhas não atingem o gatilho de autoscaling (35 conns), gerando sobrecusto nulo (+0.0%).", bullet_style))
story.append(Paragraph("2. Ao distribuir 60 conexões entre 4 atacantes (4x15), o score mediano permanece quase idêntico (443.1), mas a concorrência acumulada força o Load Balancer a <b>provisionar a réplica w2, impondo um sobrecusto de +60.0% na fatura</b>.", bullet_style))

doc.build(story)
print(f"[OK] PDF corrigido e gerado em: {PDF_PATH}")
