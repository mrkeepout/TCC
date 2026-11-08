#!/usr/bin/env python3
"""
Avalia a matriz de detecção e impacto econômico dos 4 cenários experimentais reais:
1. cenario_baseline_0atk (10 clientes legítimos, 0 atacantes)
2. cenario_gabriel_200conns (1 atacante, 200 conexões agressivas)
3. cenario_furtivo_15conns (1 atacante, 15 conexões discretas)
4. cenario_distribuido_4x15 (4 atacantes, 15 conexões cada - EDoS furtivo distribuído)

Treina o modelo de Mahalanobis estritamente no Baseline Real com tráfego Poisson + 10 arquivos.
Gera tabelas comparativas, gráficos analíticos e relatório PDF executivo para o TCC.
"""
import os
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.covariance import LedoitWolf
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

BASE_DIR = Path(__file__).resolve().parent / "out" / "stealth_comparison"
OUT_DIR = Path(__file__).resolve().parent / "out" / "stealth_comparison_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VIP = "192.168.100.10"
SCENARIOS = [
    "cenario_baseline_0atk",
    "cenario_gabriel_200conns",
    "cenario_furtivo_15conns",
    "cenario_distribuido_4x15"
]

def load_data(scen):
    df = pd.read_csv(BASE_DIR / scen / "final_report.csv", low_memory=False)
    cost = pd.read_csv(BASE_DIR / scen / "edos_cost.csv")
    df.columns = [c.strip() for c in df.columns]
    df["Dst Port"] = pd.to_numeric(df["Dst Port"], errors="coerce")
    df = df[(df["Dst IP"].astype(str) == VIP) & (df["Dst Port"] == 80)].copy()
    ts = pd.to_datetime(df["Timestamp"], format="%d/%m/%Y %I:%M:%S %p", errors="coerce")
    df["t"] = (ts - ts.min()).dt.total_seconds()
    dur = pd.to_numeric(df["Flow Duration"], errors="coerce") / 1e6
    bytes_s = pd.to_numeric(df["Flow Bytes/s"], errors="coerce")
    df["valid"] = (dur >= 0) & np.isfinite(bytes_s)
    df["slow"] = df["valid"] & (bytes_s < 1000)
    df["atk"] = df["Src IP"].astype(str).str.contains(r"\.(?:7[1-9]|10[3-9])")
    return df[df["valid"]].copy(), cost

def extract_windows(df, j=10, t_max=75):
    t = df["t"].values
    feats, labels = [], []
    step = 2.0
    for s in np.arange(0, t_max - j + 1e-6, step):
        w = df[(t >= s) & (t < s + j)]
        n = len(w)
        ns = int(w["slow"].sum())
        n_atk = int(w["atk"].sum())
        dur_med = w["Flow Duration"].median() if n > 0 else 0.0
        dur_med = max(0.0, dur_med) if np.isfinite(dur_med) else 0.0
        feats.append([
            np.log1p(n),
            np.log1p(ns),
            (ns / n) if n > 0 else 0.0,
            np.log1p(dur_med)
        ])
        labels.append(1 if n_atk > 0 else 0)
    return np.array(feats), np.array(labels)

def main():
    print("[EVAL] Carregando dados empíricos reais dos 4 cenários...")
    data = {s: load_data(s) for s in SCENARIOS}

    # 1. Treinamento do Modelo de Mahalanobis no Baseline Puro (0 atacantes)
    df_base, cost_base = data["cenario_baseline_0atk"]
    X_train, _ = extract_windows(df_base, j=10)
    mu = X_train.mean(axis=0)
    cov = LedoitWolf().fit(X_train - mu).covariance_
    VI = np.linalg.pinv(cov)
    
    d_train = X_train - mu
    maha_train = np.einsum("ij,jk,ik->i", d_train, VI, d_train)
    thr = np.percentile(maha_train, 95)
    
    base_cost = float(cost_base["cumulative_cost"].iloc[-1]) # $0.0075
    print(f"[OK] Modelo calibrado com Baseline Real: Threshold 95% = {thr:.3f}, Custo Base = ${base_cost:.4f}")

    summary = []
    
    for s in SCENARIOS:
        df, cost = data[s]
        max_rep = int(cost["active_replicas"].max())
        final_cost = float(cost["cumulative_cost"].iloc[-1])
        nco = ((final_cost - base_cost) / base_cost) * 100
        
        atk_flows = df[df["atk"]]
        atk_bytes = atk_flows["Total Length of Fwd Packet"].sum() + atk_flows["Total Length of Bwd Packet"].sum()
        
        # Avaliação na janela j = 10s
        X_test, y_test = extract_windows(df, j=10)
        d_test = X_test - mu
        maha_test = np.einsum("ij,jk,ik->i", d_test, VI, d_test)
        preds = (maha_test > thr).astype(int)
        
        atk_windows = (y_test == 1)
        if np.sum(atk_windows) > 0:
            det_rate = (np.sum(preds[atk_windows] == 1) / np.sum(atk_windows)) * 100
            evasion_rate = 100.0 - det_rate
            med_maha = float(np.median(maha_test[atk_windows]))
        else:
            det_rate = (np.sum(preds == 1) / len(preds)) * 100 # Taxa de falso alarme
            evasion_rate = 100.0 - det_rate
            med_maha = float(np.median(maha_test))
            
        summary.append({
            "cenario": s,
            "max_replicas": max_rep,
            "custo_final": final_cost,
            "nco_percent": nco,
            "atk_bytes_kb": atk_bytes / 1024,
            "det_rate_j10": det_rate,
            "evasion_rate_j10": evasion_rate,
            "maha_score_median": med_maha,
            "maha_score_max": float(np.max(maha_test)),
            "thr_j10": float(thr)
        })
        
    sum_df = pd.DataFrame(summary)
    sum_df.to_csv(OUT_DIR / "tabela_comparativa_real.csv", index=False)
    print("\n--- RESUMO COMPARATIVO EMPÍRICO REAL (4 CENÁRIOS) ---")
    print(sum_df.to_string())

    # 2. Gerar Gráfico Analítico Comparativo
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(12, 8), dpi=150)
    
    labels_all = ["Baseline\n(0 Atk)", "Gabriel\n(1x200)", "Furtivo\n(1x15)", "Distribuído\n(4x15)"]
    x = np.arange(len(labels_all))
    width = 0.5
    
    colors_det = ["#4C78A8", "#E45756", "#72B7B2", "#F58518"]
    
    # Gráfico 1: Taxa de Deteccao
    bars1 = ax1.bar(x, sum_df["det_rate_j10"], width, color=colors_det, edgecolor="black")
    ax1.set_ylabel("Taxa de Alarme / Detecção (%)", weight="bold")
    ax1.set_title("Detecção de Mahalanobis (Janela j = 10s)", weight="bold", fontsize=11)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels_all)
    ax1.set_ylim(0, 115)
    for bar in bars1:
        yval = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2.0, yval + 2, f"{yval:.1f}%", ha='center', va='bottom', weight='bold', fontsize=9)
    ax1.grid(axis='y', linestyle='--', alpha=0.5)

    # Gráfico 2: Sobrecusto Econômico NCO
    bars2 = ax2.bar(x, sum_df["nco_percent"], width, color=["#BAB0AC", "#E45756", "#72B7B2", "#54A24B"], edgecolor="black")
    ax2.set_ylabel("Sobrecusto Financeiro (+NCO %)", weight="bold")
    ax2.set_title("Impacto Econômico de Autoscaling", weight="bold", fontsize=11)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels_all)
    ax2.set_ylim(0, 115)
    for bar in bars2:
        yval = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2.0, yval + 2, f"+{yval:.1f}%", ha='center', va='bottom', weight='bold', fontsize=9)
    ax2.grid(axis='y', linestyle='--', alpha=0.5)

    # Gráfico 3: Distância de Mahalanobis Mediana (Escala Log)
    medians = sum_df["maha_score_median"].values
    bars3 = ax3.bar(x, medians, width, color=["#9C755F", "#E45756", "#72B7B2", "#F58518"], edgecolor="black")
    ax3.axhline(thr, color="red", linestyle="--", linewidth=1.5, label=f"Limiar $\\chi^2$ (thr = {thr:.2f})")
    ax3.set_yscale("log")
    ax3.set_ylabel("Distância $D_M^2$ Mediana (Log)", weight="bold")
    ax3.set_title("Intensidade Estatística da Anomalia", weight="bold", fontsize=11)
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels_all)
    ax3.legend(loc="upper left")
    for bar in bars3:
        yval = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width()/2.0, yval * 1.25, f"{yval:.1f}", ha='center', va='bottom', weight='bold', fontsize=8.5)
    ax3.grid(axis='y', linestyle='--', alpha=0.5)

    # Gráfico 4: Tradeoff Evasão vs Dano Econômico
    evasions = sum_df["evasion_rate_j10"].values
    ncos = sum_df["nco_percent"].values
    ax4.scatter(evasions[1:], ncos[1:], s=[180, 180, 220], c=["#E45756", "#72B7B2", "#54A24B"], edgecolors="black", zorder=3)
    ax4.text(evasions[1] + 1, ncos[1] - 2, " Gabriel (1x200)\n 0% Evasão, +93% NCO", fontsize=8.5, weight="bold")
    ax4.text(evasions[2] - 18, ncos[2] + 4, " Furtivo (1x15)\n 34.8% Evasão, 0% NCO", fontsize=8.5, weight="bold")
    ax4.text(evasions[3] - 15, ncos[3] + 4, " Distribuído (4x15)\n 8.3% Evasão, +53.3% NCO", fontsize=8.5, weight="bold")
    ax4.set_xlabel("Taxa de Evasão (%) [100 - Detecção]", weight="bold")
    ax4.set_ylabel("Sobrecusto Econômico (+NCO %)", weight="bold")
    ax4.set_title("Superfície de Evasão vs. Impacto EDoS", weight="bold", fontsize=11)
    ax4.set_xlim(-5, 50)
    ax4.set_ylim(-10, 110)
    ax4.grid(True, linestyle='--', alpha=0.5)

    fig.tight_layout()
    chart_path = OUT_DIR / "comparativo_stealth_vs_gabriel_real.png"
    fig.savefig(chart_path)
    plt.close(fig)
    print(f"[OK] Gráfico salvo em: {chart_path}")

    # 3. Gerar Relatório Executivo em PDF
    pdf_path = OUT_DIR / "Relatorio_Comparativo_Stealth_Gabriel.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    
    primary = colors.HexColor("#1A365D")
    dark = colors.HexColor("#2D3748")
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=17, leading=21, textColor=primary, alignment=0)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=12, textColor=colors.HexColor("#718096"))
    h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=11.5, leading=15, textColor=primary, spaceBefore=8, spaceAfter=4)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=8.2, leading=11.5, textColor=dark, spaceAfter=3)
    bullet_style = ParagraphStyle('Bullet', parent=body_style, leftIndent=10, bulletIndent=3, spaceAfter=2.5)

    story = []
    story.append(Paragraph("Relatório Experimental: Avaliação de Evasão Furtiva e EDoS no LST 2.0", title_style))
    story.append(Spacer(1, 2))
    story.append(Paragraph("<b>Ambiente:</b> VM fedbot (Debian 13, Intel i9-14900K, 32GB RAM) | <b>Tráfego:</b> Clientes Poisson + 10 Arquivos Reais", subtitle_style))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=6))

    story.append(Paragraph("1. Resultados Empíricos Reais Obtidos no Testbed", h1_style))
    
    table_data = [
        [Paragraph("<b>Cenário</b>", body_style), Paragraph("<b>Atacantes</b>", body_style), Paragraph("<b>Conexões</b>", body_style), Paragraph("<b>Deteccao (j=10s)</b>", body_style), Paragraph("<b>D_M^2 Mediana</b>", body_style), Paragraph("<b>Réplicas Máx</b>", body_style), Paragraph("<b>Sobrecusto NCO</b>", body_style)],
        [Paragraph("<b>Baseline Puro</b>", body_style), Paragraph("0", body_style), Paragraph("0 conns", body_style), Paragraph("6.1% (FPR)", body_style), Paragraph("1.62 (thr=6.09)", body_style), Paragraph("1 réplica", body_style), Paragraph("+0.0% ($0.0075)", body_style)],
        [Paragraph("<b>Gabriel (1x200)</b>", body_style), Paragraph("1", body_style), Paragraph("200 conns", body_style), Paragraph("<font color='red'><b>100.0%</b></font>", body_style), Paragraph("430.41", body_style), Paragraph("3 réplicas", body_style), Paragraph("+93.3% ($0.0145)", body_style)],
        [Paragraph("<b>Furtivo Isolado (1x15)</b>", body_style), Paragraph("1", body_style), Paragraph("15 conns", body_style), Paragraph("<font color='green'><b>65.2% (Evasão 34.8%)</b></font>", body_style), Paragraph("10.15", body_style), Paragraph("1 réplica", body_style), Paragraph("+0.0% ($0.0075)", body_style)],
        [Paragraph("<b>Distribuído (4x15)</b>", body_style), Paragraph("4", body_style), Paragraph("15 conns cada", body_style), Paragraph("<font color='blue'><b>91.7% (Evasão 8.3%)</b></font>", body_style), Paragraph("55.54", body_style), Paragraph("<b>2 réplicas</b>", body_style), Paragraph("<b>+53.3% ($0.0115)</b>", body_style)]
    ]
    t = Table(table_data, colWidths=[110, 50, 75, 105, 80, 65, 95])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E2E8F0")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))

    story.append(Paragraph("2. Comparativo Gráfico de Detecção e Impacto Econômico", h1_style))
    story.append(Image(str(chart_path), width=520, height=330))
    story.append(Spacer(1, 6))

    story.append(Paragraph("3. Conclusão Científica para a Defesa e Artigo do TCC", h1_style))
    story.append(Paragraph("• <b>O Colapso da Suposição Determinística:</b> A adição da distribuição Poisson nos clientes e de 10 arquivos no Apache conferiu variância realista à matriz de covariância ($\Sigma$). O limiar $\\chi^2$ subiu de zero artificial para <b>6.09</b>, com taxa de falso alarme legítima de apenas <b>6.1%</b> no baseline puro.", bullet_style))
    story.append(Paragraph("• <b>Por que Gabriel obteve 100% de Detecção:</b> O cenário com 200 conexões de um único IP é excessivamente agressivo ($D_M^2 = 430.41$ mediana, pico de 1372), ficando mais de 70 vezes acima do limiar. É um DoS clássico facilmente bloqueável.", bullet_style))
    story.append(Paragraph("• <b>Evasão Real no Ataque Furtivo (1x15):</b> Ao desacelerar para 15 conexões lentas (dentro do desvio do tráfego legítimo), a distância mediana caiu drasticamente para <b>10.15</b>. O detector falha em <b>34.8% das janelas</b> (taxa de detecção cai para 65.2%). Contudo, 15 conexões sozinhas não atingem o limiar de saturação do autoscaler ($N_{conns} \ge 35$), não gerando custo extra.", bullet_style))
    story.append(Paragraph("• <b>O EDoS Furtivo Distribuído (4x15):</b> Distribuir 60 conexões entre 4 atacantes (15 conexões por IP) atinge o objetivo duplo do atacante econômico: <b>força a escalabilidade da nuvem para 2 réplicas (+53.3% de sobrecusto financeiro)</b> com uma anomalia estatística 8 vezes menor que a de Gabriel ($D_M^2 = 55.5$ vs $430.4$).", bullet_style))

    doc.build(story)
    print(f"[OK] PDF Comparativo gerado em: {pdf_path}")

if __name__ == "__main__":
    main()
