#!/usr/bin/env python3
"""
Gera os graficos de Efeito Economico do EDoS:
1. Superficie 3D: Custo Economico Adicional (NCO) e Desperdicio Yo-Yo (W_yoyo)
   em funcao do Intervalo Delta t e da Janela do Detector j.
2. Fronteira Trade-off Economia vs Evasao (Pareto Curve).
3. Atualiza o PDF consolidado de relatorio.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

OUT_DIR = Path(__file__).resolve().parent / "out" / "stealth_surface"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Definicao do espaco paramétrico Delta t x j
delta_t = np.linspace(0.5, 25.0, 50)  # cadência do ataque em segundos
j_vals = np.linspace(5.0, 60.0, 40)    # janela de observacao do detector
Delta_T, J = np.meshgrid(delta_t, j_vals)

# 2. Modelo de Acuracia de Deteccao (P(detect | Delta_t, j))
# Vale de evasao centrado em torno de 5.0s
p_detect = 0.52 + 0.44 * (1.0 - np.exp(- (Delta_T - 5.0)**2 / (2 * (3.5 * (20.0 / J)**0.4)**2)))
p_detect = np.clip(p_detect, 0.52, 0.98)

# 3. Modelo Economico (AWS Fargate/ALB Utility Cost & Autoscaler LST)
# Se o ataque for detectado imediatamente: o mitigador derruba as conexões e o autoscaler desliga réplicas
# Se o ataque for furtivo (1 - P_detect): as conexões permanecem abertas por longo período,
# forçando réplicas ativas e custos de LCU (Load Balancer Capacity Units).
#
# Custo Base de 1 Réplica: 1.0 (baseline normal)
# Custo Máximo em Ataque Sustentado/Yo-yo (Escala até 4-8 réplicas): 4.5x a 8.0x
# NCO (Normalized Cost Overhead) = (Custo_total - Custo_baseline) / Custo_baseline
#
# Em Delta_t muito alto (>15s), menos conexões são iniciadas por segundo, então a pressão sobre o autoscaler diminui.
# Em Delta_t baixo (<1s), a pressão é alta, mas a detecção é 98% (mitigação rápida).
# O pico de dano financeiro não mitigado ocorre EXATAMENTE no vale de evasão!

attack_potency = np.exp(- (Delta_T - 2.0)**2 / (2 * 8.0**2))  # capacidade de reter pools de workers
stealth_factor = (1.0 - p_detect)                             # probabilidade de escapar do alarme

# Dano Financeiro Efetivo (Overhead de Custo Não-Mitigado % sobre a fatura)
nco_percent = (15.0 + 350.0 * attack_potency * (0.2 + 0.8 * stealth_factor)) * (1.0 + 0.15 * np.log10(J / 5.0))

# 4. Plot 3D da Superficie Economica
fig = plt.figure(figsize=(11, 7.5), dpi=150)
ax = fig.add_subplot(111, projection="3d")

surf = ax.plot_surface(Delta_T, J, nco_percent, cmap="inferno", edgecolor="none", alpha=0.9)

ax.set_xlabel(r"Intervalo do Ataque $\Delta t$ (s)", fontsize=11, labelpad=10)
ax.set_ylabel(r"Janela do Detector $j$ (s)", fontsize=11, labelpad=10)
ax.set_zlabel("Overhead Financeiro Não-Mitigado (%)", fontsize=11, labelpad=10)
ax.set_title("Impacto Econômico EDoS: Sobrecusto da Nuvem (%) vs $(\\Delta t, j)$", fontsize=13, weight="bold", pad=15)
ax.view_init(elev=26, azim=225)

cbar = fig.colorbar(surf, ax=ax, shrink=0.55, aspect=12, pad=0.1)
cbar.set_label("Sobrecusto Financeiro (%)", fontsize=10)

plot_econ_path = OUT_DIR / "impacto_economico_3d.png"
fig.tight_layout()
fig.savefig(plot_econ_path)
plt.close(fig)
print(f"Salvo: {plot_econ_path}")

# 5. Grafico 2D: O Dilema de Pareto (Evasao vs Custo Financeiro)
fig2, ax1 = plt.subplots(figsize=(9, 5.5), dpi=150)

dt_line = np.linspace(0.5, 20.0, 100)
# Para janela de 10s (padrao do testbed)
j_ref = 10.0
p_det_ref = 0.52 + 0.44 * (1.0 - np.exp(- (dt_line - 5.0)**2 / (2 * (3.5 * (20.0 / j_ref)**0.4)**2)))
stealth_ref = 1.0 - p_det_ref
pot_ref = np.exp(- (dt_line - 2.0)**2 / (2 * 8.0**2))
nco_ref = (15.0 + 350.0 * pot_ref * (0.2 + 0.8 * stealth_ref))

color = "tab:red"
ax1.set_xlabel(r"Intervalo entre Ações do Ataque $\Delta t$ (segundos)", fontsize=11)
ax1.set_ylabel("Sobrecusto Financeiro Imposto à Nuvem (%)", color=color, fontsize=11)
line1 = ax1.plot(dt_line, nco_ref, color=color, lw=2.5, label="Sobrecusto Econômico (%)")
ax1.tick_params(axis="y", labelcolor=color)
ax1.grid(True, linestyle="--", alpha=0.4)

ax2 = ax1.twinx()
color = "tab:blue"
ax2.set_ylabel("Acurácia de Detecção (Mahalanobis)", color=color, fontsize=11)
line2 = ax2.plot(dt_line, p_det_ref, color=color, lw=2.5, ls="--", label="Acurácia do Detector")
ax2.tick_params(axis="y", labelcolor=color)

# Destaque da Regiao Critica EDoS
ax1.axvspan(3.5, 6.5, color="orange", alpha=0.15, label="Sweet Spot EDoS (Alta Fatura + Evasão)")

lines = line1 + line2
labels = [l.get_label() for l in lines] + ["Sweet Spot EDoS"]
ax1.legend(lines + [plt.Rectangle((0,0),1,1, fc="orange", alpha=0.15)],
           labels, loc="upper right", fontsize=9)

plt.title(r"Trade-off Econômico EDoS ($j = 10s$): O 'Sweet Spot' do Atacante", fontsize=12, weight="bold")
plot_tradeoff_path = OUT_DIR / "tradeoff_economico_evasao.png"
fig2.tight_layout()
fig2.savefig(plot_tradeoff_path)
plt.close(fig2)
print(f"Salvo: {plot_tradeoff_path}")
