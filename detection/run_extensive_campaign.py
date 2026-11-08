#!/usr/bin/env python3
"""
Orquestrador da Campanha Experimental em Larga Escala:
Exploração do Trade-off de Evasão vs. Sobrecusto Econômico (EDoS).

Matriz Experimental (39 cenários no total):
- N_clients: [5, 10, 20]
- N_atk:     [1, 2, 4, 8]  (e 0 para os 3 baselines de controle)
- C_atk:     [5, 15, 30]

Executado diretamente no testbed LST 2.0 (VM fedbot).
Salva checkpoints parciais a cada rodada para segurança e tolerância a falhas.
"""
import os
import sys
import time
import json
import shutil
import subprocess
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent / "lst2.0" / "demonstration"
CFG_PATH = BASE_DIR / "edos_config.py"
OUT_BASE = Path(__file__).resolve().parent / "out" / "extensive_campaign"
OUT_BASE.mkdir(parents=True, exist_ok=True)
CHECKPOINT_FILE = OUT_BASE / "campaign_progress.csv"

# Grade de Parâmetros
CLIENTS_LIST = [5, 10, 20]
ATTACKERS_LIST = [1, 2, 4, 8]
CONNS_LIST = [5, 15, 30]

DURATION = 70  # segundos de emulação por cenário (suficiente para convergência do autoscaler)

def build_scenarios():
    scenarios = []
    # 1. Baselines puros de controle (0 atacantes) para cada contagem de clientes
    for n_cli in CLIENTS_LIST:
        name = f"base_c{n_cli}_a0_k0"
        scenarios.append({
            "name": name,
            "clients": n_cli,
            "num_atk": 0,
            "conns": 0,
            "rate": 0,
            "interval": 3,
            "is_baseline": True
        })
    # 2. Cenários de ataque (combinação completa)
    for n_cli in CLIENTS_LIST:
        for n_atk in ATTACKERS_LIST:
            for conns in CONNS_LIST:
                name = f"exp_c{n_cli}_a{n_atk}_k{conns}"
                scenarios.append({
                    "name": name,
                    "clients": n_cli,
                    "num_atk": n_atk,
                    "conns": conns,
                    "rate": 2,
                    "interval": 5,
                    "is_baseline": False
                })
    return scenarios

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
        else:
            lines.append(l)
    CFG_PATH.write_text("\n".join(lines) + "\n")

def cleanup_docker():
    subprocess.run("ovs-vsctl --if-exists del-br br0", shell=True, check=False)
    subprocess.run("docker rm -f $(docker ps -aq) 2>/dev/null || true", shell=True, check=False)

def run_scenario(scen, idx, total):
    print(f"\n=======================================================", flush=True)
    print(f"[{idx}/{total}] Executando Cenário: {scen['name']}", flush=True)
    print(f"       Clientes: {scen['clients']} | Atacantes: {scen['num_atk']} | Conns/Atk: {scen['conns']}", flush=True)
    print(f"=======================================================", flush=True)
    
    scen_out = OUT_BASE / scen["name"]
    # Se já existir e contiver final_report.csv válido, podemos pular (resumable)
    if (scen_out / "final_report.csv").exists() and (scen_out / "edos_cost.csv").exists():
        if (scen_out / "final_report.csv").stat().st_size > 5000:
            print(f"[SKIP] Cenário {scen['name']} já concluído anteriormente com sucesso.", flush=True)
            return True

    scen_out.mkdir(parents=True, exist_ok=True)
    cleanup_docker()
    set_config(scen)
    
    t0 = time.time()
    # Executa edos.py
    cmd = f"cd {BASE_DIR} && python3 edos.py"
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    elapsed = time.time() - t0
    print(f"[STATUS] Finalizado em {elapsed:.1f}s. Return code: {res.returncode}", flush=True)
    
    # Coleta artefatos
    flows_src = BASE_DIR / "flows"
    report_copied = False
    for f in flows_src.glob("*.csv"):
        if "final_report" in f.name:
            shutil.copy(f, scen_out / "final_report.csv")
            report_copied = True
    
    # edos_cost.csv fica em flows/edos_cost.csv ou em demonstration/edos_cost.csv
    cost_file_flows = flows_src / "edos_cost.csv"
    cost_file_base = BASE_DIR / "edos_cost.csv"
    if cost_file_flows.exists():
        shutil.copy(cost_file_flows, scen_out / "edos_cost.csv")
    elif cost_file_base.exists():
        shutil.copy(cost_file_base, scen_out / "edos_cost.csv")
        
    cleanup_docker()
    
    # Registra progresso no arquivo de checkpoint
    row = {
        "scenario": scen["name"],
        "clients": scen["clients"],
        "num_atk": scen["num_atk"],
        "conns": scen["conns"],
        "duration_s": elapsed,
        "return_code": res.returncode,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    df_row = pd.DataFrame([row])
    if not CHECKPOINT_FILE.exists():
        df_row.to_csv(CHECKPOINT_FILE, index=False)
    else:
        df_row.to_csv(CHECKPOINT_FILE, mode="a", header=False, index=False)
        
    return res.returncode == 0

def main():
    scenarios = build_scenarios()
    total = len(scenarios)
    print(f"[CAMPANHA] Iniciando bateria extensiva com {total} cenários no LST 2.0...", flush=True)
    
    t_start = time.time()
    for idx, scen in enumerate(scenarios, 1):
        success = run_scenario(scen, idx, total)
        if not success:
            print(f"[AVISO] Tentando reexecutar cenário {scen['name']} devido a erro...", flush=True)
            time.sleep(3)
            run_scenario(scen, idx, total)
            
    total_elapsed = time.time() - t_start
    print(f"\n=======================================================", flush=True)
    print(f"[CAMPANHA] Todos os {total} cenários foram processados em {total_elapsed/60:.1f} minutos!", flush=True)
    print(f"Dados arquivados em: {OUT_BASE}", flush=True)
    print(f"=======================================================", flush=True)

if __name__ == "__main__":
    main()
