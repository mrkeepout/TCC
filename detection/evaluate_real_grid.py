#!/usr/bin/env python3
"""
Avalia a matriz de deteccao 100% real:
Carrega os fluxos gerados ao vivo para cada Delta t in [1, 3, 6, 12],
calcula as janelas de observacao j in [5, 10, 15, 20, 30, 45, 60]s,
aplica o detector de Mahalanobis e gera as figuras com dados reais.
"""
import os
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from sklearn.covariance import LedoitWolf

DATA_DIR = Path(__file__).resolve().parent / "out" / "real_grid"
OUT_DIR = Path(__file__).resolve().parent / "out" / "real_grid_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VIP = "192.168.100.10"
ATK_IPS = {"192.168.100.71", "192.168.100.72", "192.168.100.103", "192.168.100.104"}
JS = [5, 10, 15, 20, 30, 45, 60]
DTS = [1, 3, 6, 12]
SLOW_THRESH = 1000

def load_flows(csv_path):
    df = pd.read_csv(csv_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df["Dst Port"] = pd.to_numeric(df["Dst Port"], errors="coerce")
    # Filtra chegadas no VIP:80
    df = df[(df["Dst IP"].astype(str) == VIP) & (df["Dst Port"] == 80)].copy()
    ts = pd.to_datetime(df["Timestamp"], format="%d/%m/%Y %I:%M:%S %p", errors="coerce")
    df["t"] = (ts - ts.min()).dt.total_seconds()
    dur = pd.to_numeric(df["Flow Duration"], errors="coerce") / 1e6
    bytes_s = pd.to_numeric(df["Flow Bytes/s"], errors="coerce")
    df["valid"] = (dur >= 0) & np.isfinite(bytes_s)
    df["slow"] = df["valid"] & (bytes_s < SLOW_THRESH)
    df["atk"] = df["Src IP"].astype(str).isin(ATK_IPS)
    return df[df["valid"]].copy()

def extract_windows(df, j, t_max=65):
    t = df["t"].values
    feats = []
    labels = []
    step = 2.0
    for s in np.arange(0, t_max - j + 1e-6, step):
        w = df[(t >= s) & (t < s + j)]
        n = len(w)
        ns = int(w["slow"].sum())
        n_atk = int(w["atk"].sum())
        feats.append([
            np.log1p(n),
            np.log1p(ns),
            (ns / n) if n > 0 else 0.0,
            np.log1p(w["Flow Duration"].median()) if n > 0 else 0.0
        ])
        labels.append(1 if n_atk > 0 else 0)
    return np.array(feats), np.array(labels)

def main():
    print("[EVAL] Processando fluxos das emulacoes reais...")
    
    # 1. Carrega os datasets
    datasets = {dt: load_flows(DATA_DIR / f"dt_{dt}s" / "final_report.csv") for dt in DTS}
    
    # 2. Treinar modelo base Mahalanobis com trafego benigno
    # Usando os fluxos legítimos de dt=3s como baseline
    benign_df = datasets[3][~datasets[3]["atk"]]
    
    results = []
    
    for j in JS:
        X_train, _ = extract_windows(benign_df, j)
        mu = X_train.mean(axis=0)
        cov = LedoitWolf().fit(X_train - mu).covariance_
        VI = np.linalg.pinv(cov)
        
        # Limiar empirico de 95% no benigno
        d_train = X_train - mu
        maha_train = np.einsum("ij,jk,ik->i", d_train, VI, d_train)
        thr = np.percentile(maha_train, 95)
        
        for dt in DTS:
            df = datasets[dt]
            X_test, y_test = extract_windows(df, j)
            if len(X_test) == 0:
                continue
            d_test = X_test - mu
            maha_test = np.einsum("ij,jk,ik->i", d_test, VI, d_test)
            preds = (maha_test > thr).astype(int)
            
            # Acurácia empírica real
            acc = np.mean(preds == y_test)
            tpr = np.sum((preds == 1) & (y_test == 1)) / max(1, np.sum(y_test == 1))
            fpr = np.sum((preds == 1) & (y_test == 0)) / max(1, np.sum(y_test == 0))
            
            results.append({
                "delta_t": dt,
                "j": j,
                "accuracy": acc,
                "tpr": tpr,
                "fpr": fpr,
                "thr": thr
            })
            
    res_df = pd.DataFrame(results)
    res_df.to_csv(OUT_DIR / "real_grid_metrics.csv", index=False)
    print(res_df.to_string())
    
    # 3. Gerar a Matriz Real e Plotar Superficie 3D Real
    pivot = res_df.pivot(index="j", columns="delta_t", values="accuracy")
    print("\n--- MATRIZ REAL DE ACURACIA EMPIRICA ---")
    print(pivot)
    
    # Grid 3D Real
    fig = plt.figure(figsize=(10, 7), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    
    X_grid, Y_grid = np.meshgrid(DTS, JS)
    Z_grid = pivot.values
    
    surf = ax.plot_surface(X_grid, Y_grid, Z_grid, cmap="viridis_r", edgecolor="k", lw=0.5, alpha=0.9)
    ax.scatter(res_df["delta_t"], res_df["j"], res_df["accuracy"], color="red", s=40, label="Execução Real (LST)")
    
    ax.set_xlabel(r"Intervalo do Ataque $\Delta t$ (s)", fontsize=11, labelpad=10)
    ax.set_ylabel(r"Janela do Detector $j$ (s)", fontsize=11, labelpad=10)
    ax.set_zlabel("Acurácia Real", fontsize=11, labelpad=10)
    ax.set_title("Superfície 3D Real: Dados da Emulação ao Vivo na VM", fontsize=12, weight="bold")
    ax.set_zlim(0.4, 1.05)
    ax.view_init(elev=28, azim=135)
    
    cbar = fig.colorbar(surf, ax=ax, shrink=0.5, aspect=10)
    cbar.set_label("Acurácia", fontsize=10)
    ax.legend()
    
    fig.tight_layout()
    fig.savefig(OUT_DIR / "superficie_3d_real.png")
    plt.close(fig)
    print(f"\n[OK] Grafico 3D Real Salvo em: {OUT_DIR / 'superficie_3d_real.png'}")

if __name__ == "__main__":
    main()
