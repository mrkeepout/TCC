#!/usr/bin/env python3
"""
Experimento de Comparacao Rigorosa: Baseline (0 atacantes) vs Gabriel vs Furtivo vs Distribuido
Todos rodando com clientes Poisson e 10 arquivos de tamanhos variados no Apache.
"""
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent / "lst2.0" / "demonstration"
CFG_PATH = BASE_DIR / "edos_config.py"
OUT_BASE = Path(__file__).resolve().parent / "out" / "stealth_comparison"
OUT_BASE.mkdir(parents=True, exist_ok=True)

SCENARIOS = {
    "cenario_baseline_0atk":    {"num_atk": 0, "conns": 0,   "rate": 0,  "interval": 3, "clients": 10},
    "cenario_gabriel_200conns": {"num_atk": 1, "conns": 200, "rate": 50, "interval": 3, "clients": 10},
    "cenario_furtivo_15conns":  {"num_atk": 1, "conns": 15,  "rate": 2,  "interval": 5, "clients": 10},
    "cenario_distribuido_4x15": {"num_atk": 4, "conns": 15,  "rate": 2,  "interval": 5, "clients": 10},
}
DURATION = 75  # segundos de emulação por cenário

def set_config(scen):
    text = CFG_PATH.read_text()
    lines = []
    for l in text.splitlines():
        if l.strip().startswith("NUM_ATTACKERS ="):
            lines.append(f"NUM_ATTACKERS = {scen['num_atk']}")
        elif l.strip().startswith("NUM_CLIENTS ="):
            lines.append(f"NUM_CLIENTS = {scen['clients']}")
        elif l.strip().startswith("SLOW_CONNS ="):
            lines.append(f"SLOW_CONNS = {scen['conns']}")
        elif l.strip().startswith("SLOW_RATE ="):
            lines.append(f"SLOW_RATE = {scen['rate']}")
        elif l.strip().startswith("SLOW_INTERVAL ="):
            lines.append(f"SLOW_INTERVAL = {scen['interval']}")
        elif l.strip().startswith("EXPERIMENT_DURATION ="):
            lines.append(f"EXPERIMENT_DURATION = {DURATION}")
        elif l.strip().startswith("SCALE_UP_CONN ="):
            lines.append("SCALE_UP_CONN = 35")
        else:
            lines.append(l)
    CFG_PATH.write_text("\n".join(lines) + "\n")
    print(f"[STEALTH-EXP] Configurado: {scen}")

def cleanup_docker():
    subprocess.run("sudo ovs-vsctl --if-exists del-br br0", shell=True, check=False)
    subprocess.run("sudo docker rm -f $(sudo docker ps -aq) 2>/dev/null || true", shell=True, check=False)

def run_scenario(name, scen):
    print(f"\n=======================================================")
    print(f"[STEALTH-EXP] Executando Cenario: {name}")
    print(f"=======================================================")
    cleanup_docker()
    set_config(scen)
    
    run_dir = OUT_BASE / name
    run_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = f"cd {BASE_DIR} && sudo python3 edos.py"
    t0 = time.time()
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"[STEALTH-EXP] Concluido em {time.time()-t0:.1f}s. Return code: {res.returncode}")
    
    flows_dir = BASE_DIR / "flows"
    for item in flows_dir.glob("*"):
        if item.is_file():
            shutil.copy(item, run_dir / item.name)
            
    cost_file = BASE_DIR / "edos_cost.csv"
    if cost_file.exists():
        shutil.copy(cost_file, run_dir / "edos_cost.csv")
    print(f"[STEALTH-EXP] Dados salvos em: {run_dir}")

def main():
    print("[STEALTH-EXP] Iniciando bateria comparativa no LST 2.0 (com Baseline 0 atacantes)...")
    for name, scen in SCENARIOS.items():
        run_scenario(name, scen)
        time.sleep(5)
    cleanup_docker()
    print("\n[STEALTH-EXP] Todos os cenarios foram executados com sucesso!")

if __name__ == "__main__":
    main()
