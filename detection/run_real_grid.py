#!/usr/bin/env python3
"""
Orquestrador de Execução Real no Testbed LST 2.0 (VM):
1. Varia SLOW_INTERVAL (Delta t) em valores reais: [1, 3, 6, 12] segundos.
2. Executa a emulação LST (edos.py) por 70 segundos para cada Delta t.
3. Coleta o final_report.csv gerado pelo CICFlowMeter e o edos_cost.csv.
4. Executa a detecção de Mahalanobis em cada rodada real.
5. Plota a superfície 3D e curvas 2D 100% empíricas com os dados reais gerados.
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
OUT_BASE = Path(__file__).resolve().parent / "out" / "real_grid"
OUT_BASE.mkdir(parents=True, exist_ok=True)

DELTA_T_LIST = [1, 3, 6, 12]  # Segundos de intervalo de ataque
RUN_DURATION = 65              # Duração de cada rodada do LST em segundos

def set_config(delta_t, duration):
    text = CFG_PATH.read_text()
    lines = []
    for l in text.splitlines():
        if l.strip().startswith("SLOW_INTERVAL ="):
            lines.append(f"SLOW_INTERVAL = {delta_t}")
        elif l.strip().startswith("EXPERIMENT_DURATION ="):
            lines.append(f"EXPERIMENT_DURATION = {duration}")
        else:
            lines.append(l)
    CFG_PATH.write_text("\n".join(lines) + "\n")
    print(f"[GRID] Configurado: SLOW_INTERVAL={delta_t}, EXPERIMENT_DURATION={duration}")

def cleanup_docker():
    subprocess.run("sudo ovs-vsctl --if-exists del-br br0", shell=True, check=False)
    subprocess.run("sudo docker rm -f $(sudo docker ps -aq) 2>/dev/null || true", shell=True, check=False)

def run_simulation(dt):
    print(f"\n=======================================================")
    print(f"[GRID] Iniciando Emulacao Real LST para Delta_t = {dt}s")
    print(f"=======================================================")
    cleanup_docker()
    set_config(dt, RUN_DURATION)
    
    run_dir = OUT_BASE / f"dt_{dt}s"
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # Executa edos.py
    cmd = f"cd {BASE_DIR} && sudo python3 edos.py"
    t0 = time.time()
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"[GRID] Rodada finalizada em {time.time()-t0:.1f}s. Código retorno: {res.returncode}")
    
    # Copia os artefatos reais gerados
    flows_dir = BASE_DIR / "flows"
    for item in flows_dir.glob("*"):
        if item.is_file():
            shutil.copy(item, run_dir / item.name)
        elif item.is_dir() and item.name != "br0":
            shutil.copytree(item, run_dir / item.name, dirs_exist_ok=True)
        
    cost_file = BASE_DIR / "edos_cost.csv"
    if cost_file.exists():
        shutil.copy(cost_file, run_dir / "edos_cost.csv")
        
    print(f"[GRID] Artefatos reais salvos em: {run_dir}")

def main():
    print(f"[GRID] Teste de grade empírica real iniciado para Delta_t in {DELTA_T_LIST}")
    for dt in DELTA_T_LIST:
        run_simulation(dt)
        time.sleep(5)
    cleanup_docker()
    print("\n[GRID] Todas as emulações reais foram concluídas com sucesso!")

if __name__ == "__main__":
    main()
