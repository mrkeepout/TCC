#!/usr/bin/env python3
"""
Módulo de Avaliação da Campanha Extensiva:
Processa os 39 cenários empíricos da matriz (N_clients x N_atk x C_atk),
calcula o modelo de Mahalanobis calibrado em cada baseline nulo respectivo,
computa o dano econômico (+NCO, réplicas, EDE) e plota as superfícies 3D e curvas de Pareto.
Gera o relatório final executivo em PDF.
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

def load_data(scen_dir):
    df_path = scen_dir / "final_report.csv"
    cost_path = scen_dir / "edos_cost.csv"
    if not df_path.exists() or not cost_path.exists():
        return None, None
    df = pd.read_csv(df_path, low_memory=False)
    cost = pd.read_csv(cost_path)
    df.columns = [c.strip() for c in df.columns]
    df["Dst Port"] = pd.to_numeric(df["Dst Port"], errors="coerce")
    df = df[(df["Dst IP"].astype(str) == VIP) & (df["Dst Port"] == 80)].copy()
    ts = pd.to_datetime(df["Timestamp"], format="%d/%m/%Y %I:%M:%S %p", errors="coerce")
    df["t"] = (ts - ts.min()).dt.total_seconds()
    dur = pd.to_numeric(df["Flow Duration"], errors="coerce") / 1e6
    bytes_s = pd.to_numeric(df["Flow Bytes/s"], errors="coerce")
    df["valid"] = (dur >= 0) & np.isfinite(bytes_s)
    df["slow"] = df["valid"] & (bytes_s < 1000)
    # Faixa dinâmica de IPs de atacantes: 192.168.100.71 a 192.168.100.95
    df["atk"] = df["Src IP"].astype(str).str.contains(r"\.(?:7[1-9]|8[0-9]|9[0-5])")
    return df[df["valid"]].copy(), cost

def extract_windows(df, j=10, t_max=70):
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
    print("[EVAL-CAMPAIGN] Analisando dados da campanha extensiva...")
    progress_file = BASE_DIR / "campaign_progress.csv"
    if not progress_file.exists():
        print(f"[ERRO] Arquivo de progresso não encontrado em {progress_file}")
        return

    df_prog = pd.read_csv(progress_file)
    print(f"[OK] Cenários registrados até o momento: {len(df_prog)}")

    # 1. Carregar baselines por contagem de clientes para calibração estatística
    baselines = {}
    for n_cli in [5, 10, 20]:
        base_name = f"base_c{n_cli}_a0_k0"
        df_b, cost_b = load_data(BASE_DIR / base_name)
        if df_b is not None and cost_b is not None:
            X_b, _ = extract_windows(df_b, j=10)
            mu_b = X_b.mean(axis=0)
            cov_b = LedoitWolf().fit(X_b - mu_b).covariance_
            VI_b = np.linalg.pinv(cov_b)
            d_b = X_b - mu_b
            maha_b = np.einsum("ij,jk,ik->i", d_b, VI_b, d_b)
            thr_b = np.percentile(maha_b, 95)
            c_base = float(cost_b["cumulative_cost"].iloc[-1])
            baselines[n_cli] = {
                "mu": mu_b,
                "VI": VI_b,
                "thr": thr_b,
                "cost_base": c_base
            }
            print(f"[BASELINE] c={n_cli}: thr_95% = {thr_b:.3f}, cost_base = ${c_base:.4f}")

    results = []
    for scen_dir in sorted(BASE_DIR.glob("*")):
        if not scen_dir.is_dir() or scen_dir.name.startswith("."):
            continue
        scen_name = scen_dir.name
        df, cost = load_data(scen_dir)
        if df is None or cost is None:
            continue

        parts = scen_name.split("_")
        # Ex: base_c5_a0_k0 ou exp_c10_a4_k15
        is_base = parts[0] == "base"
        n_cli = int(parts[1][1:])
        n_atk = int(parts[2][1:])
        conns = int(parts[3][1:])

        if n_cli not in baselines:
            continue

        base_info = baselines[n_cli]
        mu = base_info["mu"]
        VI = base_info["VI"]
        thr = base_info["thr"]
        cost_base = base_info["cost_base"]

        final_cost = float(cost["cumulative_cost"].iloc[-1])
        max_rep = int(cost["active_replicas"].max())
        nco = ((final_cost - cost_base) / cost_base) * 100 if cost_base > 0 else 0.0

        # Avaliar Mahalanobis
        X_test, y_test = extract_windows(df, j=10)
        d_test = X_test - mu
        maha_test = np.einsum("ij,jk,ik->i", d_test, VI, d_test)
        preds = (maha_test > thr).astype(int)

        atk_w = (y_test == 1)
        if is_base:
            det_rate = (np.sum(preds == 1) / len(preds)) * 100 # FPR
            evasion = 100.0 - det_rate
            med_maha = float(np.median(maha_test))
        else:
            if np.sum(atk_w) > 0:
                det_rate = (np.sum(preds[atk_w] == 1) / np.sum(atk_w)) * 100
                evasion = 100.0 - det_rate
                med_maha = float(np.median(maha_test[atk_w]))
            else:
                det_rate = 0.0
                evasion = 100.0
                med_maha = 0.0

        total_atk_conns = n_atk * conns
        stealth_score = nco * (evasion / 100.0)

        results.append({
            "scenario": scen_name,
            "is_baseline": is_base,
            "n_clients": n_cli,
            "n_atk": n_atk,
            "conns_atk": conns,
            "total_atk_conns": total_atk_conns,
            "max_replicas": max_rep,
            "final_cost": final_cost,
            "nco_percent": max(0.0, nco),
            "det_rate_j10": det_rate,
            "evasion_rate_j10": evasion,
            "median_maha": med_maha,
            "thr_j10": thr,
            "stealth_score": stealth_score
        })

    res_df = pd.DataFrame(results)
    res_df.to_csv(OUT_DIR / "matriz_campanha_extensiva.csv", index=False)
    print(f"\n[OK] Matriz experimental computada com {len(res_df)} registros!")
    print(res_df.head(10).to_string())

if __name__ == "__main__":
    main()
