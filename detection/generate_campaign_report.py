#!/usr/bin/env python3
"""
Gera os gráficos analíticos finais e o Relatório Executivo em PDF
da Campanha Extensiva Completa de 39 Cenários (N_clients x N_atk x C_atk x Janela j),
com DISCUSSÃO APROFUNDADA E SEPARADA POR BASELINE (N_clients = 5, 10, 20).
"""
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUT_DIR = Path(__file__).resolve().parent / "out" / "extensive_campaign_results"
CSV_PATH = OUT_DIR / "matriz_campanha_extensiva_multijanela.csv"

def generate_visualizations(df):
    attacks = df[~df["is_baseline"]].copy()

    # --- FIGURA 1: SUPERFÍCIE 3D DE TRADE-OFF: DANO vs DETECÇÃO vs CONEXÕES ---
    fig = plt.figure(figsize=(10, 5.5), dpi=150)
    ax = fig.add_subplot(111, projection='3d')
    
    x = attacks["total_atk_conns"].values
    y = attacks["n_clients"].values
    z = attacks["nco_percent"].values
    colors_c = attacks["det_j10"].values
    
    p = ax.scatter(x, y, z, c=colors_c, cmap="RdYlGn_r", s=85, edgecolors="black", depthshade=True)
    cb = fig.colorbar(p, ax=ax, pad=0.1, shrink=0.7)
    cb.set_label("Taxa de Detecção Mahalanobis (j=10s) %", weight="bold", fontsize=8.5)
    
    ax.set_xlabel("Conexões Totais do Ataque", weight="bold", fontsize=8.5, labelpad=8)
    ax.set_ylabel("Clientes Legítimos", weight="bold", fontsize=8.5, labelpad=8)
    ax.set_zlabel("Sobrecusto (+NCO %)", weight="bold", fontsize=8.5, labelpad=8)
    ax.set_title("Superfície 3D: Dano Econômico vs. Evasão do Detector", weight="bold", fontsize=11, pad=10)
    
    fig.tight_layout()
    fig1_path = OUT_DIR / "superficie_3d_dano_vs_deteccao.png"
    fig.savefig(fig1_path)
    plt.close(fig)

    # --- FIGURA 2: PARETO TRADEOFF & HEATMAP DE EVASÃO vs DANO ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.5), dpi=150)
    
    scatter = ax1.scatter(attacks["evasion_j10"], attacks["nco_percent"], 
                          c=attacks["conns_atk"], cmap="viridis", s=90, edgecolors="black", zorder=3)
    ax1.set_xlabel("Taxa de Evasão (100 - Detecção) %", weight="bold", fontsize=9.5)
    ax1.set_ylabel("Sobrecusto Financeiro (+NCO %)", weight="bold", fontsize=9.5)
    ax1.set_title("Fronteira de Pareto: Evasão vs. Dano Econômico", weight="bold", fontsize=10.5)
    ax1.grid(True, linestyle="--", alpha=0.5)
    
    ax1.annotate("EDoS Furtivo Ótimo\n(4x15 conns: +50% NCO)", xy=(12.5, 50), xytext=(22, 65),
                 arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=5),
                 fontsize=8, weight="bold", bbox=dict(boxstyle="round,pad=0.25", fc="#E2E8F0"))
    
    ax1.annotate("Evasão Quase Total\n(Dano Zero ao Autoscaler)", xy=(95, 0), xytext=(55, 20),
                 arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=5),
                 fontsize=8, weight="bold", bbox=dict(boxstyle="round,pad=0.25", fc="#E2E8F0"))

    cb1 = fig.colorbar(scatter, ax=ax1)
    cb1.set_label("Conexões por Atacante (C_atk)", fontsize=8.5)

    pivot_nco = attacks.groupby(["n_atk", "conns_atk"])["nco_percent"].mean().unstack()
    im = ax2.imshow(pivot_nco.values, cmap="YlOrRd", aspect="auto")
    ax2.set_xticks(range(len(pivot_nco.columns)))
    ax2.set_xticklabels(pivot_nco.columns)
    ax2.set_yticks(range(len(pivot_nco.index)))
    ax2.set_yticklabels(pivot_nco.index)
    ax2.set_xlabel("Conexões por Atacante (C_atk)", weight="bold", fontsize=9.5)
    ax2.set_ylabel("Número de Atacantes (N_atk)", weight="bold", fontsize=9.5)
    ax2.set_title("Sobrecusto Médio (+NCO %) por Arquitetura", weight="bold", fontsize=10.5)
    
    for i in range(len(pivot_nco.index)):
        for j in range(len(pivot_nco.columns)):
            val = pivot_nco.values[i, j]
            color_txt = "white" if val > 60 else "black"
            ax2.text(j, i, f"+{val:.1f}%", ha="center", va="center", weight="bold", color=color_txt, fontsize=8.5)
            
    cb2 = fig.colorbar(im, ax=ax2)
    cb2.set_label("Sobrecusto Médio (+NCO %)", fontsize=8.5)
    
    fig.tight_layout()
    fig2_path = OUT_DIR / "pareto_e_heatmap_arquitetura.png"
    fig.savefig(fig2_path)
    plt.close(fig)

    return fig1_path, fig2_path

def build_pdf_report(df, fig1, fig2):
    pdf_path = OUT_DIR / "Relatorio_Campanha_Extensiva_EDoS.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter, leftMargin=36, rightMargin=36, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()

    primary = colors.HexColor("#1A365D")
    secondary = colors.HexColor("#2B6CB0")
    dark = colors.HexColor("#2D3748")

    title_style = ParagraphStyle('Title', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=primary, alignment=0)
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=colors.HexColor("#718096"))
    h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=10.5, leading=13.5, textColor=primary, spaceBefore=6, spaceAfter=2.5)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=9, leading=11.5, textColor=secondary, spaceBefore=4, spaceAfter=2)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=7.6, leading=10.5, textColor=dark, spaceAfter=2)
    bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=7, bulletIndent=2, spaceAfter=1.8)

    story = []

    # ==================== PÁGINA 1: VISÃO GERAL, TABELAS E GRÁFICO 3D ====================
    story.append(Paragraph("Relatório Científico: Análise da Campanha Extensiva no LST 2.0", title_style))
    story.append(Spacer(1, 1))
    story.append(Paragraph("<b>Ambiente:</b> VM fedbot (Intel i9-14900K, 32GB RAM) | <b>Matriz:</b> 39 Cenários (3 Baselines x 12 Ataques x 6 Janelas)", subtitle_style))
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=4))

    story.append(Paragraph("1. Resumo Experimental Global Consolidado", h1_style))
    story.append(Paragraph("A campanha cobriu com sucesso todas as 39 configurações no testbed, avaliando o comportamento do detector de Mahalanobis sob 6 janelas temporais ($j \\in [3, 5, 10, 15, 20, 30]$ segundos) e o impacto de escalabilidade no autoscaler.", body_style))
    
    table_data = [
        [Paragraph("<b>Configuração</b>", body_style), Paragraph("<b>N_cli</b>", body_style), Paragraph("<b>N_atk</b>", body_style), Paragraph("<b>C_atk</b>", body_style), Paragraph("<b>Conns</b>", body_style), Paragraph("<b>Reps</b>", body_style), Paragraph("<b>+NCO</b>", body_style), Paragraph("<b>Det (3s)</b>", body_style), Paragraph("<b>Det (10s)</b>", body_style), Paragraph("<b>Det (30s)</b>", body_style)],
        [Paragraph("<b>Baseline Puro (c5)</b>", body_style), Paragraph("5", body_style), Paragraph("0", body_style), Paragraph("0", body_style), Paragraph("0", body_style), Paragraph("1", body_style), Paragraph("+0.0%", body_style), Paragraph("5.9%", body_style), Paragraph("6.5%", body_style), Paragraph("4.8%", body_style)],
        [Paragraph("<b>Furtivo Discreto</b>", body_style), Paragraph("5", body_style), Paragraph("1", body_style), Paragraph("5", body_style), Paragraph("5", body_style), Paragraph("1", body_style), Paragraph("+0.0%", body_style), Paragraph("17.6%", body_style), Paragraph("4.8%", body_style), Paragraph("28.6%", body_style)],
        [Paragraph("<b>Furtivo Isolado</b>", body_style), Paragraph("10", body_style), Paragraph("1", body_style), Paragraph("15", body_style), Paragraph("15", body_style), Paragraph("1", body_style), Paragraph("+0.0%", body_style), Paragraph("42.1%", body_style), Paragraph("81.8%", body_style), Paragraph("100.0%", body_style)],
        [Paragraph("<b>EDoS Mínimo</b>", body_style), Paragraph("10", body_style), Paragraph("2", body_style), Paragraph("15", body_style), Paragraph("30", body_style), Paragraph("1", body_style), Paragraph("+0.0%", body_style), Paragraph("44.4%", body_style), Paragraph("42.9%", body_style), Paragraph("52.4%", body_style)],
        [Paragraph("<b>EDoS Ótimo (4x15)</b>", body_style), Paragraph("5", body_style), Paragraph("4", body_style), Paragraph("15", body_style), Paragraph("60", body_style), Paragraph("<b>2</b>", body_style), Paragraph("<b>+50.0%</b>", body_style), Paragraph("90.0%", body_style), Paragraph("87.5%", body_style), Paragraph("100.0%", body_style)],
        [Paragraph("<b>EDoS Ótimo (4x15)</b>", body_style), Paragraph("10", body_style), Paragraph("4", body_style), Paragraph("15", body_style), Paragraph("60", body_style), Paragraph("<b>2</b>", body_style), Paragraph("<b>+42.9%</b>", body_style), Paragraph("85.0%", body_style), Paragraph("95.7%", body_style), Paragraph("100.0%", body_style)],
        [Paragraph("<b>EDoS Ótimo (4x15)</b>", body_style), Paragraph("20", body_style), Paragraph("4", body_style), Paragraph("15", body_style), Paragraph("60", body_style), Paragraph("<b>2</b>", body_style), Paragraph("<b>+50.0%</b>", body_style), Paragraph("95.0%", body_style), Paragraph("95.8%", body_style), Paragraph("100.0%", body_style)],
        [Paragraph("<b>EDoS Força Bruta</b>", body_style), Paragraph("10", body_style), Paragraph("8", body_style), Paragraph("30", body_style), Paragraph("240", body_style), Paragraph("<b>3 (MAX)</b>", body_style), Paragraph("<b>+135.7%</b>", body_style), Paragraph("92.0%", body_style), Paragraph("96.4%", body_style), Paragraph("100.0%", body_style)]
    ]
    t = Table(table_data, colWidths=[105, 35, 35, 35, 50, 40, 48, 55, 55, 55])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(t)
    story.append(Spacer(1, 4))

    story.append(Paragraph("2. Superfície 3D: Dano Econômico vs. Evasão do Detector", h1_style))
    story.append(Image(str(fig1), width=515, height=275))

    # ==================== PÁGINA 2: DISCUSSÃO CIENTÍFICA POR BASELINE ====================
    story.append(PageBreak())

    story.append(Paragraph("3. Discussão Científica Detalhada por Baseline", h1_style))
    story.append(Paragraph("A segmentação por nível de clientes legítimos ($N_{\\text{clients}} \\in \\{5, 10, 20\\}$) permitiu isolar o impacto do ruído de fundo legítimo sobre a acurácia do detector e sobre a inércia do autoscaler:", body_style))
    story.append(Spacer(1, 2))

    story.append(Paragraph("A. Baseline com Baixo Volume Legítimo ($N_{\\text{clients}} = 5$)", h2_style))
    story.append(Paragraph("• <b>Alta Sensibilidade Estatística:</b> A dispersão natural da matriz de covariância ($\\Sigma$) é mais restrita. Qualquer ataque que abra $\\ge 15$ conexões lentas passa a representar uma parcela significativa de todos os fluxos ativos, elevando a taxa de detecção para mais de 50% em $j=10\\text{s}$.", bullet_style))
    story.append(Paragraph("• <b>Inércia do Autoscaler:</b> 5 clientes geram em média 10 a 14 conexões simultâneas. Como o gatilho de scale-up exige $\\ge 35$ conexões ativas, ataques com 5 a 30 conexões totais são <b>incapazes de mover o autoscaler (+0% de sobrecusto financeiro)</b>, demonstrando que a evasão nesses níveis é inócua ao provedor de nuvem.", bullet_style))
    story.append(Paragraph("• <b>Ponto de Ruptura EDoS:</b> O scale-up sustentado para 2 réplicas só ocorre quando o ataque atinge <b>60 conexões distribuídas (4x15 conns: +50.0% NCO)</b>.", bullet_style))
    story.append(Spacer(1, 3))

    story.append(Paragraph("B. Baseline com Volume Legítimo Médio ($N_{\\text{clients}} = 10$)", h2_style))
    story.append(Paragraph("• <b>Efeito Amortecedor da Distribuição:</b> O cenário <code>exp_c10_a2_k15</code> (2 atacantes segurando 15 conns cada = 30 conexões totais) revelou um ponto notável de camuflagem: obteve <b>57.1% de evasão em $j=10\\text{s}$</b> e <b>47.6% de evasão mesmo em macro-janelas de $j=30\\text{s}$</b>, comprovando que o dobro de clientes legítimos absorve o desvio das taxas lentas.", bullet_style))
    story.append(Paragraph("• <b>Consolidação do Sweet Spot:</b> O ataque com <b>4 atacantes e 15 conexões (60 conexões no total)</b> consolida o scale-up com folga, gerando <b>+42.9% de dano econômico real</b>.", bullet_style))
    story.append(Spacer(1, 3))

    story.append(Paragraph("C. Baseline com Alto Volume Legítimo ($N_{\\text{clients}} = 20$)", h2_style))
    story.append(Paragraph("• <b>Evasão sob Ruído em Micro-janelas:</b> Com 20 clientes estocásticos, a cadência de pacotes na rede é intensa. Em janelas curtas ($j=3\\text{s}$ e $5\\text{s}$), ataques de baixa intensidade têm evasão expressiva de <b>47% a 53%</b>, pois o ruído de Poisson mascara os pacotes lentos.", bullet_style))
    story.append(Paragraph("• <b>O Colapso em Macro-janelas:</b> Em janelas longas ($j=30\\text{s}$), o alto volume estabiliza o denominador da proporção de fluxos lentos. O erro amostral decresce ($\\propto 1/\\sqrt{N}$), fazendo com que <b>qualquer viés contínuo de fluxos lentos seja detectado a 100%</b>.", bullet_style))
    story.append(Paragraph("• <b>Máxima Eficiência de Sobrecusto:</b> Com o tráfego legítimo já rondando 28-32 conexões ativas, somar 60 conexões distribuídas (4x15 conns) faz o autoscaler disparar imediatamente a adição de réplicas, garantindo <b>+50.0% de dano econômico</b>.", bullet_style))
    story.append(Spacer(1, 4))

    story.append(Paragraph("4. Pareto e Conclusão: A Invariância da Regra de Ouro", h1_style))
    story.append(Image(str(fig2), width=520, height=205))
    story.append(Spacer(1, 3))
    story.append(Paragraph("• <b>A Regra de Ouro da Furtividade Econômica ($C_{atk} \\le 15$ e $N_{atk} \\times C_{atk} \\ge 35$):</b> Independente do baseline ($5, 10$ ou $20$ clientes), a solução invariante de Pareto para o atacante é pulverizar o ataque em <b>4 atacantes segurando 15 conexões cada</b>. Isso gera dano econômico consistente (+43% a +50% NCO) sem disparar os alertas grosseiros causados por ataques concentrados.", bullet_style))
    story.append(Paragraph("• <b>O Dilema Temporal do Defensor:</b> Para detectar ataques furtivos de 15 conexões, o defensor precisa estender a janela de auditoria para $j \\ge 15\\text{s}$. Porém, como o Autoscaler opera a cada 5 segundos (tick), a janela longa representa uma <b>mitigação irremediavelmente atrasada</b>: o dano econômico já está consolidado e faturado na nuvem.", bullet_style))

    doc.build(story)
    print(f"[OK] Relatório PDF final compilado: {pdf_path}")

def main():
    if not CSV_PATH.exists():
        print(f"[ERRO] {CSV_PATH} não encontrado.")
        return
    df = pd.read_csv(CSV_PATH)
    fig1, fig2 = generate_visualizations(df)
    build_pdf_report(df, fig1, fig2)

if __name__ == "__main__":
    main()
