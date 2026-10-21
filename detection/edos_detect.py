#!/usr/bin/env python3
"""
Detector de EDoS por anomalia — treino SÓ com tráfego benigno.

Dois níveis, porque a investigação mostrou que importam coisas diferentes:

  (1) POR-FLUXO (Mahalanobis). Treina mu/Sigma das features de fluxo no
      benigno e pontua cada requisição ao serviço por D^2. Escopado às
      requisições que chegam ao VIP (Dst = VIP:80), senão o baseline mistura
      cliente (rápido) e servidor (lento) e o ataque "afoga". Resultado:
      AUC ~0.74 — o low-and-slow quase não destoa fluxo-a-fluxo.

  (2) POR-JANELA (agregado). O perigo do low-and-slow é o AGREGADO: muitas
      conexões lentas ao mesmo tempo (o que o autoscaler mede como conns).
      Conta requisições "lentas" (Bytes/s < LIMIAR) por janela de tempo e
      aplica um limiar simples (media + 3*desvio do benigno). Separa limpo.

Uso:
    python3 edos_detect.py
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import chi2
from sklearn.covariance import LedoitWolf
from sklearn.metrics import roc_auc_score, roc_curve, precision_score, recall_score, f1_score
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA = Path("data"); OUT = Path("out"); OUT.mkdir(exist_ok=True)
VIP = "192.168.100.10"
ATTACKERS = {"1atk": {"192.168.100.71"},
             "2atk": {"192.168.100.71", "192.168.100.72"}}
TS_FMT = "%d/%m/%Y %I:%M:%S %p"
SLOW_BYTES = 1000           # requisição "lenta": Flow Bytes/s abaixo disso
WIN = "10s"

FEATS = ["Flow Duration", "Total Fwd Packet", "Total Bwd packets", "Flow Bytes/s",
         "Flow Packets/s", "Flow IAT Mean", "Flow IAT Std", "Fwd Packet Length Mean"]
LOG = {"Flow Duration", "Flow Bytes/s", "Flow Packets/s", "Flow IAT Mean", "Flow IAT Std"}
BLUE, RED, K = "#4C78A8", "#E45756", "#333333"


def load(path):
    df = pd.read_csv(path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    for c in FEATS + ["Dst Port"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[pd.to_numeric(df["Flow Duration"], errors="coerce") >= 0]   # drop dur. negativa
    df = df.replace([np.inf, -np.inf], np.nan)
    df["ts"] = pd.to_datetime(df["Timestamp"], format=TS_FMT, errors="coerce")
    return df


def requests_to_vip(df):
    return df[(df["Dst IP"].astype(str) == VIP) & (df["Dst Port"] == 80)].copy()


def matrix(df):
    X = df[FEATS].astype(float).copy()
    for c in LOG:
        X[c] = np.log1p(X[c].clip(lower=0))
    return X.dropna()


# ---------------------------------------------------------------- nível 1
def build_flow_model(ben):
    X = matrix(requests_to_vip(ben))
    mu = X.mean().values
    VI = np.linalg.inv(LedoitWolf().fit(X.values - mu).covariance_)
    thr = chi2.ppf(0.99, df=len(FEATS))
    return mu, VI, thr


def score_flows(df, mu, VI):
    sub = requests_to_vip(df)
    X = matrix(sub); sub = sub.loc[X.index]
    d = X.values - mu
    sub = sub.assign(d2=np.einsum("ij,jk,ik->i", d, VI, d))
    return sub


# ---------------------------------------------------------------- nível 2
def windowed(df):
    sub = requests_to_vip(df).dropna(subset=["ts", "Flow Bytes/s"]).sort_values("ts")
    sub["slow"] = (sub["Flow Bytes/s"] < SLOW_BYTES).astype(int)
    g = sub.set_index("ts").groupby(pd.Grouper(freq=WIN))
    w = pd.DataFrame({"n_req": g.size(), "n_slow": g["slow"].sum()})
    w = w[w["n_req"] > 0]
    w["frac_slow"] = w["n_slow"] / w["n_req"]
    w["t"] = (w.index - w.index[0]).total_seconds()
    return w


def main():
    runs = {"benign": load(DATA / "run_benign_0atk.csv"),
            "1atk":   load(DATA / "run_1atk.csv"),
            "2atk":   load(DATA / "run_2atk.csv")}
    costs = {k: pd.read_csv(DATA / f"cost_{k}.csv") for k in runs}
    res = {}

    # ---- nível 1: por-fluxo (Mahalanobis) ----
    mu, VI, thr = build_flow_model(runs["benign"])
    res["flow_threshold_chi2_99"] = float(thr)
    for name in ("1atk", "2atk"):
        s = score_flows(runs[name], mu, VI)
        y = s["Src IP"].astype(str).isin(ATTACKERS[name]).astype(int).values
        yhat = (s["d2"].values > thr).astype(int)
        res.setdefault("flow", {})[name] = {
            "n": int(len(y)), "auc": float(roc_auc_score(y, s["d2"].values)),
            "precision": float(precision_score(y, yhat, zero_division=0)),
            "recall": float(recall_score(y, yhat, zero_division=0)),
            "f1": float(f1_score(y, yhat, zero_division=0))}
        fig_anomaly(name, s, y, thr)

    # ---- nível 2: por-janela (agregado) ----
    wb = windowed(runs["benign"])
    base_mu, base_sd = wb["n_slow"].mean(), wb["n_slow"].std()
    wthr = base_mu + 3 * base_sd
    res["window_threshold"] = {"mu": float(base_mu), "sigma": float(base_sd), "thr": float(wthr)}
    wins = {"benign": wb}
    for name in ("1atk", "2atk"):
        w = windowed(runs[name]); wins[name] = w
        flagged = (w["n_slow"] > wthr).mean()
        res.setdefault("window", {})[name] = {
            "n_janelas": int(len(w)), "n_slow_mediana": float(w["n_slow"].median()),
            "n_slow_max": float(w["n_slow"].max()),
            "janelas_flagradas_%": float(100 * flagged)}
    res["window"]["benign_fp_%"] = float(100 * (wb["n_slow"] > wthr).mean())
    fig_windows(wins, wthr)
    fig_cost(costs)
    fig_roc(runs, mu, VI)

    (OUT / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps(res, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------- figuras
def fig_anomaly(name, s, y, thr):
    t = (s["ts"] - s["ts"].min()).dt.total_seconds().values
    fig, ax = plt.subplots(figsize=(9, 3.3))
    ax.scatter(t[y == 0], s["d2"].values[y == 0], s=5, c=BLUE, alpha=.4, label="benigno")
    ax.scatter(t[y == 1], s["d2"].values[y == 1], s=5, c=RED, alpha=.5, label="ataque")
    ax.axhline(thr, color=K, ls="--", lw=1, label="limiar χ² 99%")
    ax.set_yscale("log"); ax.set_xlabel("tempo (s)"); ax.set_ylabel("D² (Mahalanobis)")
    ax.set_title(f"Nível 1 · anomalia por-fluxo — {name}")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout(); fig.savefig(OUT / f"nivel1_anomalia_{name}.png", dpi=130); plt.close(fig)


def fig_windows(wins, thr):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(wins["benign"]["t"], wins["benign"]["n_slow"], "-o", ms=3, c=BLUE, label="benigno")
    ax.plot(wins["1atk"]["t"], wins["1atk"]["n_slow"], "-o", ms=3, c="#F58518", label="1 atacante")
    ax.plot(wins["2atk"]["t"], wins["2atk"]["n_slow"], "-o", ms=3, c=RED, label="2 atacantes")
    ax.axhline(thr, color=K, ls="--", lw=1.2, label=f"limiar μ+3σ = {thr:.0f}")
    ax.set_xlabel("tempo (s)"); ax.set_ylabel(f"requisições lentas / {WIN}")
    ax.set_title("Nível 2 · detecção agregada por janela")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "nivel2_janelas.png", dpi=130); plt.close(fig)


def fig_cost(costs):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for k, c, lab in [("benign", BLUE, "benigno"), ("1atk", "#F58518", "1 atacante"), ("2atk", RED, "2 atacantes")]:
        d = costs[k]; t = d["timestamp"] - d["timestamp"].iloc[0]
        ax.plot(t, d["cumulative_cost"], c=c, label=lab)
    ax.set_xlabel("tempo (s)"); ax.set_ylabel("custo acumulado (réplica-s)")
    ax.set_title("Impacto econômico — custo de autoscaling")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "impacto_custo.png", dpi=130); plt.close(fig)


def fig_roc(runs, mu, VI):
    fig, ax = plt.subplots(figsize=(4.6, 4.4))
    for name, c in [("1atk", "#F58518"), ("2atk", RED)]:
        s = score_flows(runs[name], mu, VI)
        y = s["Src IP"].astype(str).isin(ATTACKERS[name]).astype(int).values
        fpr, tpr, _ = roc_curve(y, s["d2"].values)
        ax.plot(fpr, tpr, c=c, label=f"{name} (AUC={roc_auc_score(y, s['d2'].values):.2f})")
    ax.plot([0, 1], [0, 1], "k--", lw=.8)
    ax.set_xlabel("FPR"); ax.set_ylabel("TPR"); ax.set_title("ROC — detector por-fluxo")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout(); fig.savefig(OUT / "roc_nivel1.png", dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
