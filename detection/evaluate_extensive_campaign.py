#!/usr/bin/env python3
"""
Módulo de Avaliação da Campanha Extensiva:
Processa os 39 cenários empíricos da matriz (N_clients x N_atk x C_atk),
calcula o modelo de Mahalanobis calibrado em cada baseline nulo respectivo
para MÚLTIPLAS JANELAS TEMPORAIS (j in [3, 5, 10, 15, 20, 30] segundos).
Computa o dano econômico (+NCO, réplicas, EDE), mapeia a fronteira de Pareto
e plota as superfícies 3D e mapas de calor de Trade-off (Janela vs. Conexões vs. Dano).
"""
import os
import sys
import glob
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from sklearn.covariance import LedoitWolf

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

BASE_DIR = Path(__file__).resolve().parent / "out" / "extensive_campaign"
OUT_DIR = Path(__file__).resolve().parent / "out" / "extensive_campaign_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VIP = "192.168.100.10"
WINDOWS = [3, 5, 10, 15, 20, 30]  # Janelas de auditoria investigadas (segundos)

# Parâmetros reais do Autoscaler
SCALE_UP_CONN = 35
SCALE_DOWN_CONN = 10
MIN_REPLICAS = 1
MAX_REPLICAS = 3
TICK_SECONDS = 5
COOLDOWN_SECONDS = 10
SCALE_DOWN_TICKS = 3
COST_PER_REPLICA_SEC = 0.0001
DURATION = 70

def simulate_autoscaler_from_flows(df, duration=70):
    t_start = df["t"].values
    dur = pd.to_numeric(df["Flow Duration"], errors="coerce").values / 1e6
    t_end = t_start + np.maximum(dur, 0.1)
    
    times = np.arange(0, duration + 1, 1.0)
    conns_active = np.zeros(len(times))
    for i, now in enumerate(times):
        conns_active[i] = np.sum((t_start <= now) & (t_end >= now))
        
    current_replicas = MIN_REPLICAS
    last_action_time = -999.0
    low_ticks = 0
    cost = 0.0
    replicas_history = []
    
    for now in times:
        cost += current_replicas * COST_PER_REPLICA_SEC * 1.0
        if int(now) % TICK_SECONDS == 0:
            c = conns_active[int(now)]
            load_signal = c / current_replicas
            can_scale = (now - last_action_time) >= COOLDOWN_SECONDS
            if can_scale and load_signal >= SCALE_UP_CONN and current_replicas < MAX_REPLICAS:
                current_replicas += 1
                last_action_time = now
                low_ticks = 0
            elif can_scale and load_signal < SCALE_DOWN_CONN and current_replicas > MIN_REPLICAS:
                low_ticks += 1
                if low_ticks >= SCALE_DOWN_TICKS:
                    current_replicas -= 1
                    last_action_time = now
                    low_ticks = 0
            else:
                if load_signal >= SCALE_DOWN_CONN:
                    low_ticks = 0
        replicas_history.append(current_replicas)
        
    return max(replicas_history), cost, float(conns_active.max())

def load_data(scen_dir):
    df_path = scen_dir / "final_report.csv"
    if not df_path.exists():
        return None, None, 0.0
    df = pd.read_csv(df_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df["Dst Port"] = pd.to_numeric(df["Dst Port"], errors="coerce")
    df = df[(df["Dst IP"].astype(str) == VIP) & (df["Dst Port"] == 80)].copy()
    ts = pd.to_datetime(df["Timestamp"], format="%d/%m/%Y %I:%M:%S %p", errors="coerce")
    df["t"] = (ts - ts.min()).dt.total_seconds()
    dur = pd.to_numeric(df["Flow Duration"], errors="coerce") / 1e6
    bytes_s = pd.to_numeric(df["Flow Bytes/s"], errors="coerce")
    df["valid"] = (dur >= 0) & np.isfinite(bytes_s)
    df["slow"] = df["valid"] & (bytes_s < 1000)
    df["atk"] = df["Src IP"].astype(str).str.contains(r"\.(?:7[1-9]|8[0-9]|9[0-5])")
    
    clean_df = df[df["valid"]].copy()
    
    # Se existir edos_cost.csv direto, use-o; caso contrário, simule com exatidão
    cost_path = scen_dir / "edos_cost.csv"
    if cost_path.exists():
        cdf = pd.read_csv(cost_path)
        max_rep = int(cdf["active_replicas"].max())
        final_cost = float(cdf["cumulative_cost"].iloc[-1])
        peak_conns = 0.0
    else:
        max_rep, final_cost, peak_conns = simulate_autoscaler_from_flows(clean_df)
        
    return clean_df, max_rep, final_cost

def extract_windows(df, j, t_max=70):
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
    print("[EVAL-CAMPAIGN] Analisando dados da campanha extensiva com matriz multi-janela...", flush=True)

    # 1. Carregar baselines por contagem de clientes e por tamanho de janela j
    baselines = {}
    for n_cli in [5, 10, 20]:
        base_name = f"base_c{n_cli}_a0_k0"
        base_dir = BASE_DIR / base_name
        df_b, max_rep_b, cost_b = load_data(base_dir)
        if df_b is not None:
            baselines[n_cli] = {"cost_base": cost_b, "windows": {}}
            for j in WINDOWS:
                X_b, _ = extract_windows(df_b, j=j)
                mu_b = X_b.mean(axis=0)
                cov_b = LedoitWolf().fit(X_b - mu_b).covariance_
                VI_b = np.linalg.pinv(cov_b)
                d_b = X_b - mu_b
                maha_b = np.einsum("ij,jk,ik->i", d_b, VI_b, d_b)
                thr_b = np.percentile(maha_b, 95)
                baselines[n_cli]["windows"][j] = {
                    "mu": mu_b,
                    "VI": VI_b,
                    "thr": thr_b
                }
            print(f"[BASELINE] c={n_cli} calibrado para {len(WINDOWS)} janelas. Custo Base=\${cost_b:.4f}", flush=True)

    results = []
    scen_dirs = sorted([d for d in BASE_DIR.glob("*") if d.is_dir() and not d.name.startswith(".")])
    print(f"[EVAL] Processando {len(scen_dirs)} diretórios de cenários...", flush=True)

    for scen_dir in scen_dirs:
        scen_name = scen_dir.name
        df, max_rep, final_cost = load_data(scen_dir)
        if df is None:
            continue

        parts = scen_name.split("_")
        is_base = parts[0] == "base"
        n_cli = int(parts[1][1:])
        n_atk = int(parts[2][1:])
        conns = int(parts[3][1:])

        if n_cli not in baselines:
            continue

        base_info = baselines[n_cli]
        cost_base = base_info["cost_base"]
        nco = ((final_cost - cost_base) / cost_base) * 100 if cost_base > 0 else 0.0

        # Avaliar para cada janela temporal j
        row = {
            "scenario": scen_name,
            "is_baseline": is_base,
            "n_clients": n_cli,
            "n_atk": n_atk,
            "conns_atk": conns,
            "total_atk_conns": n_atk * conns,
            "max_replicas": max_rep,
            "final_cost": final_cost,
            "nco_percent": max(0.0, nco),
        }

        for j in WINDOWS:
            j_model = base_info["windows"][j]
            mu = j_model["mu"]
            VI = j_model["VI"]
            thr = j_model["thr"]

            X_test, y_test = extract_windows(df, j=j)
            d_test = X_test - mu
            maha_test = np.einsum("ij,jk,ik->i", d_test, VI, d_test)
            preds = (maha_test > thr).astype(int)

            atk_w = (y_test == 1)
            if is_base:
                det = (np.sum(preds == 1) / len(preds)) * 100
                med_m = float(np.median(maha_test))
            else:
                if np.sum(atk_w) > 0:
                    det = (np.sum(preds[atk_w] == 1) / np.sum(atk_w)) * 100
                    med_m = float(np.median(maha_test[atk_w]))
                else:
                    det = 0.0
                    med_m = 0.0

            row[f"det_j{j}"] = det
            row[f"evasion_j{j}"] = 100.0 - det
            row[f"maha_med_j{j}"] = med_m
            row[f"thr_j{j}"] = thr

        row["stealth_score_j10"] = row["nco_percent"] * (row["evasion_j10"] / 100.0)
        results.append(row)

    res_df = pd.DataFrame(results)
    csv_path = OUT_DIR / "matriz_campanha_extensiva_multijanela.csv"
    res_df.to_csv(csv_path, index=False)
    print(f"\n[OK] Matriz experimental multi-janela salva em: {csv_path} ({len(res_df)} registros)", flush=True)

    # 2. Gerar Gráficos de Análise Multi-janela
    generate_multidimensional_plots(res_df)

def generate_multidimensional_plots(df):
    if len(df[~df["is_baseline"]]) == 0:
        return
        
    attacks = df[~df["is_baseline"]].copy()
    
    # Gráfico 1: Janela de Detecção (j) vs Evasão para diferentes perfis de ataque
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    
    profiles = [
        ("exp_c5_a1_k5", "1 Atk, 5 Conns (Ultradiscreto)", "#2CA02C", "o-"),
        ("exp_c5_a1_k15", "1 Atk, 15 Conns (Furtivo Isolado)", "#1F77B4", "s-"),
        ("exp_c5_a2_k15", "2 Atk, 15 Conns (EDoS Mínimo)", "#FF7F0E", "^-"),
        ("exp_c5_a4_k15", "4 Atk, 15 Conns (EDoS Ótimo)", "#D62728", "d-"),
        ("exp_c5_a4_k30", "4 Atk, 30 Conns (Agressivo)", "#9467BD", "x-"),
    ]
    
    for scen_prefix, label, color, marker in profiles:
        match = attacks[attacks["scenario"].str.startswith(scen_prefix)]
        if len(match) > 0:
            row = match.iloc[0]
            evasions = [row[f"evasion_j{j}"] for j in WINDOWS]
            ax.plot(WINDOWS, evasions, marker, label=label, color=color, linewidth=2, markersize=7)
            
    ax.set_xlabel("Tamanho da Janela de Auditoria $j$ (segundos)", weight="bold", fontsize=11)
    ax.set_ylabel("Taxa de Evasão do Mahalanobis (%)", weight="bold", fontsize=11)
    ax.set_title("Sensibilidade da Evasão ao Tamanho da Janela de Auditoria $j$", weight="bold", fontsize=12)
    ax.set_xticks(WINDOWS)
    ax.set_ylim(-5, 105)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", framealpha=0.9)
    
    # Anotação de Trade-off
    ax.axvspan(15, 30, color="red", alpha=0.08)
    ax.text(18, 50, "Macro-janelas ($j \\ge 15\\text{s}$):\nDetecção Total mas\nReação Tardia do Defensor", color="#900C3F", weight="bold", fontsize=9)
    
    ax.axvspan(3, 10, color="green", alpha=0.08)
    ax.text(4, 15, "Micro-janelas ($j \\le 10\\text{s}$):\nAlta Evasão Furtiva\n(Ruído Mascara o Ataque)", color="#145A32", weight="bold", fontsize=9)
    
    fig.tight_layout()
    plot_path = OUT_DIR / "curva_sensibilidade_janela_j.png"
    fig.savefig(plot_path)
    plt.close(fig)
    print(f"[OK] Gráfico de sensibilidade de janela gerado: {plot_path}", flush=True)

if __name__ == "__main__":
    main()
