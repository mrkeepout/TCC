#!/usr/bin/env python3
# Copyright (C) 2024
# Interface gráfica web para o EDoS Testbed no LST 2.0.

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)

# Configurações do ambiente
BASE_DIR = Path(__file__).resolve().parent
EDOS_SCRIPT = BASE_DIR / 'edos.py'
PLOT_SCRIPT = BASE_DIR / 'plot_edos.py'
METRICS_SCRIPT = BASE_DIR / 'edos_metrics.py'
BENCHMARK_SCRIPT = BASE_DIR / 'edos_benchmark.py'
BENCHMARK_JSON = BASE_DIR / 'flows' / 'benchmark.json'
OVERRIDE_FILE = BASE_DIR / '.edos_overrides.py'
COST_CSV = BASE_DIR / 'flows' / 'edos_cost.csv'
COST_PLOT = BASE_DIR / 'flows' / 'edos_plot.png'
METRICS_JSON = BASE_DIR / 'flows' / 'edos_metrics.json'
# Config the run was started with; travels with the CSV into the history backup
RUN_CONFIG = COST_CSV.with_suffix('.json')

# Estado global da execução
experiment_process = None
# True from /api/start until run_experiment finishes; experiment_process alone is
# still None while the override file is being written, which made /api/logs
# report "done" right away
experiment_active = False
log_queue = queue.Queue()
experiment_lock = threading.Lock()

def enqueue_output(out, q):
    """Lê a saída do subprocesso linha a linha e coloca na fila."""
    for line in iter(out.readline, b''):
        q.put(line.decode('utf-8', 'replace'))
    out.close()

def run_experiment(config_data):
    """Gera o arquivo de override, executa o experimento e gera os gráficos ao final."""
    global experiment_process, experiment_active
    
    # 1. Gerar o arquivo de override com os valores da GUI. repr() cobre str,
    # número, bool, lista (FLEET) e dict (PRICING) como Python válido.
    override_content = ""
    for key, value in config_data.items():
        override_content += f"{key} = {repr(value)}\n"

    with open(OVERRIDE_FILE, 'w') as f:
        f.write(override_content)
        
    # Limpar CSV e PNG anteriores se existirem (histórico gerenciado pela GUI seria copiar antes)
    # Obs: Como solicitado, salvar o histórico para não sobrescrever: 
    # Podemos salvar o flows/ anterior anexando timestamp, mas para simplificar o plot
    # vamos manter o flows/edos_cost.csv atual, e o frontend lida com os dados visualmente,
    # ou fazemos o backup dos resultados antigos.
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    if COST_CSV.exists():
        backup_csv = BASE_DIR / 'flows' / f'edos_cost_{timestamp_str}.csv'
        COST_CSV.rename(backup_csv)
    if COST_PLOT.exists():
        backup_plot = BASE_DIR / 'flows' / f'edos_plot_{timestamp_str}.png'
        COST_PLOT.rename(backup_plot)
    if RUN_CONFIG.exists():
        RUN_CONFIG.rename(BASE_DIR / 'flows' / f'edos_cost_{timestamp_str}.json')
    # Back up the previous run's derived metrics so it stays paired with its CSV.
    if METRICS_JSON.exists():
        METRICS_JSON.rename(BASE_DIR / 'flows' / f'edos_metrics_{timestamp_str}.json')
    try:
        with open(RUN_CONFIG, 'w') as f:
            json.dump(config_data, f)
    except OSError as e:
        log_queue.put(f">>> [GUI] Aviso: não foi possível salvar a config da rodada: {e}\n")

    # 2. Executar edos.py
    log_queue.put(">>> [GUI] Iniciando experimento EDoS...\n")
    try:
        # Nota: Assume que o Flask foi iniciado com sudo, ou que o usuário tem sudo sem senha.
        # Já como root, roda o edos.py direto: com sudo no meio, o SIGINT do botão
        # "parar" vai para o sudo, que não o repassa ao edos.py (ele roda num pty próprio).
        cmd = ["python3", "-u", str(EDOS_SCRIPT)]
        if os.geteuid() != 0:
            cmd = ["sudo"] + cmd
            log_queue.put(">>> [GUI] Aviso: GUI sem root; o botão de parar pode não funcionar. "
                          "Inicie com 'sudo python3 edos_gui.py'.\n")
        experiment_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(BASE_DIR)
        )
        
        # Iniciar thread para ler os logs
        t = threading.Thread(target=enqueue_output, args=(experiment_process.stdout, log_queue))
        t.daemon = True
        t.start()
        
        # Esperar terminar
        experiment_process.wait()
        
    except Exception as e:
        log_queue.put(f">>> [GUI] Erro ao iniciar experimento: {e}\n")
    finally:
        log_queue.put(">>> [GUI] Experimento finalizado.\n")
        # Remover arquivo de override
        if OVERRIDE_FILE.exists():
            OVERRIDE_FILE.unlink()
        
        # 3. Gerar os gráficos e as métricas derivadas se o CSV foi criado
        if COST_CSV.exists():
            log_queue.put(">>> [GUI] Gerando gráficos com plot_edos.py...\n")
            subprocess.run(["python3", str(PLOT_SCRIPT)], cwd=str(BASE_DIR), capture_output=True)
            log_queue.put(">>> [GUI] Calculando métricas derivadas (NCO / EAF / W_yoyo / QoS)...\n")
            m = subprocess.run(["python3", str(METRICS_SCRIPT)], cwd=str(BASE_DIR),
                               capture_output=True, text=True)
            for line in (m.stdout or '').splitlines():
                log_queue.put(line + "\n")
            log_queue.put(">>> [GUI] Resultados prontos para visualização.\n")
        
        with experiment_lock:
            experiment_process = None
            experiment_active = False

def run_benchmark(config_data):
    """Run the capacity benchmark as a subprocess, streaming its logs. Shares the
    experiment's busy state so the two can't run at once (same Docker names)."""
    global experiment_process, experiment_active
    # Apply the current form values (e.g. TARGET_PORT) as an override, so the
    # benchmark measures the same environment the experiment will run in.
    override = ''.join(f"{k} = {repr(v)}\n" for k, v in (config_data or {}).items())
    with open(OVERRIDE_FILE, 'w') as f:
        f.write(override)
    log_queue.put(">>> [GUI] Iniciando benchmark de capacidade da réplica...\n")
    try:
        cmd = ["python3", "-u", str(BENCHMARK_SCRIPT)]
        if os.geteuid() != 0:
            cmd = ["sudo"] + cmd
            log_queue.put(">>> [GUI] Aviso: GUI sem root; inicie com 'sudo python3 edos_gui.py'.\n")
        experiment_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(BASE_DIR))
        t = threading.Thread(target=enqueue_output, args=(experiment_process.stdout, log_queue))
        t.daemon = True
        t.start()
        experiment_process.wait()
    except Exception as e:
        log_queue.put(f">>> [GUI] Erro no benchmark: {e}\n")
    finally:
        log_queue.put(">>> [GUI] Benchmark finalizado.\n")
        if OVERRIDE_FILE.exists():
            OVERRIDE_FILE.unlink()
        with experiment_lock:
            experiment_process = None
            experiment_active = False


@app.route('/api/benchmark/start', methods=['POST'])
def benchmark_start():
    global experiment_active
    with experiment_lock:
        if experiment_active:
            return jsonify({'error': 'Já existe uma execução em andamento.'}), 400
        config_data = request.json or {}
        while not log_queue.empty():
            log_queue.get_nowait()
        experiment_active = True
        threading.Thread(target=run_benchmark, args=(config_data,), daemon=True).start()
        return jsonify({'status': 'started'})


@app.route('/api/benchmark/result', methods=['GET'])
def benchmark_result():
    if BENCHMARK_JSON.exists():
        try:
            with open(BENCHMARK_JSON) as f:
                return jsonify(json.load(f))
        except (OSError, ValueError):
            pass
    return jsonify({'error': 'Nenhum resultado de benchmark disponível.'}), 404


@app.route('/')
def index():
    # Retorna o arquivo HTML puro para evitar conflito entre Jinja2 e Vue.js
    with open(BASE_DIR / 'templates' / 'index.html', 'r', encoding='utf-8') as f:
        return f.read()

@app.route('/api/defaults', methods=['GET'])
def get_defaults():
    """Lê os padrões diretamente importando o edos_config.py (sem overrides aplicados)."""
    # Remove overrides antigos se ficaram para trás por algum crash
    if OVERRIDE_FILE.exists():
        OVERRIDE_FILE.unlink()
        
    import importlib
    import edos_config
    importlib.reload(edos_config)
    
    defaults = {
        # Benign fleet is now declarative (how many + which profile).
        'FLEET': getattr(edos_config, 'FLEET',
                         [{'profile': 'mixed', 'count': 2}]),
        'SEED': getattr(edos_config, 'SEED', 1337),
        'NUM_ATTACKERS': getattr(edos_config, 'NUM_ATTACKERS', 2),
        'MIN_REPLICAS': getattr(edos_config, 'MIN_REPLICAS', 1),
        'MAX_REPLICAS': getattr(edos_config, 'MAX_REPLICAS', 3),
        'SCALE_UP_CONN': getattr(edos_config, 'SCALE_UP_CONN', 50),
        'SCALE_DOWN_CONN': getattr(edos_config, 'SCALE_DOWN_CONN', 10),
        'TICK_SECONDS': getattr(edos_config, 'TICK_SECONDS', 5),
        'COOLDOWN_SECONDS': getattr(edos_config, 'COOLDOWN_SECONDS', 10),
        # Phase 2 realism.
        'WARMUP_SECONDS': getattr(edos_config, 'WARMUP_SECONDS', 30),
        'DRAIN_TIMEOUT_SECONDS': getattr(edos_config, 'DRAIN_TIMEOUT_SECONDS', 30),
        'PRICING': getattr(edos_config, 'PRICING', {}),
        'PRICING_CLOUD': getattr(edos_config, 'PRICING_CLOUD', 'aws'),
        'PRICING_PROFILES': getattr(edos_config, 'PRICING_PROFILES', {}),
        'ATTACK_MODE': getattr(edos_config, 'ATTACK_MODE', 'yoyo'),
        'SLOW_TEST': getattr(edos_config, 'SLOW_TEST', 'slowloris'),
        'SLOW_CONNS': getattr(edos_config, 'SLOW_CONNS', 200),
        'SLOW_RATE': getattr(edos_config, 'SLOW_RATE', 50),
        'SLOW_INTERVAL': getattr(edos_config, 'SLOW_INTERVAL', 10),
        'PULSE_ON': getattr(edos_config, 'PULSE_ON', 20),
        'PULSE_OFF': getattr(edos_config, 'PULSE_OFF', 20),
        'EXPERIMENT_DURATION': getattr(edos_config, 'EXPERIMENT_DURATION', 0),
        'subnet': getattr(edos_config, 'subnet', '192.168.100.'),
        'mask': getattr(edos_config, 'mask', 24),
        'TARGET_PORT': getattr(edos_config, 'TARGET_PORT', 80)
    }
    return jsonify(defaults)

@app.route('/api/start', methods=['POST'])
def start_experiment():
    global experiment_active
    with experiment_lock:
        if experiment_active:
            return jsonify({'error': 'Já existe um experimento em execução.'}), 400
            
        config_data = request.json
        
        # Limpar fila de logs
        while not log_queue.empty():
            log_queue.get_nowait()
            
        experiment_active = True
        thread = threading.Thread(target=run_experiment, args=(config_data,))
        thread.daemon = True
        thread.start()
        
        return jsonify({'status': 'started'})

@app.route('/api/stop', methods=['POST'])
def stop_experiment():
    global experiment_process
    with experiment_lock:
        if experiment_process is None:
            return jsonify({'error': 'Nenhum experimento em execução.'}), 400
            
        log_queue.put(">>> [GUI] Solicitando parada do experimento (SIGINT / CTRL+C)...\n")
        try:
            experiment_process.send_signal(signal.SIGINT)
        except Exception as e:
            log_queue.put(f">>> [GUI] Erro ao parar experimento: {e}\n")
            
        return jsonify({'status': 'stopping'})

@app.route('/api/status', methods=['GET'])
def get_status():
    with experiment_lock:
        if experiment_active:
            return jsonify({'status': 'running'})
        return jsonify({'status': 'idle'})

@app.route('/api/logs')
def stream_logs():
    def generate():
        while True:
            # Se o processo terminou e a fila está vazia, espera um pouco para garantir
            # que a UI leia tudo antes de parar de atualizar
            if not experiment_active and log_queue.empty():
                yield "event: done\ndata: {}\n\n"
                break
                
            try:
                line = log_queue.get(timeout=1.0)
                yield f"data: {json.dumps({'line': line})}\n\n"
            except queue.Empty:
                # Keep-alive
                yield ":\n\n"
    return Response(generate(), mimetype='text/event-stream')

FLOWS_DIR = BASE_DIR / 'flows'
# Backups of past runs, written by run_experiment() before each new run.
HISTORY_GLOB = 'edos_cost_*.csv'


def _resolve_cost_csv(name):
    """Map a client-supplied history name to a safe path inside flows/.

    Returns the current-run CSV when name is falsy, or a validated backup path.
    Raises ValueError if the name tries to escape flows/ or does not exist.
    """
    if not name:
        return COST_CSV
    # Only accept a bare filename that matches our own backup pattern; never a
    # path, so a crafted `name` can't read arbitrary files off the host.
    if '/' in name or '\\' in name or not name.startswith('edos_cost_') \
            or not name.endswith('.csv'):
        raise ValueError('Nome de histórico inválido.')
    path = (FLOWS_DIR / name).resolve()
    if path.parent != FLOWS_DIR.resolve() or not path.exists():
        raise ValueError('Histórico não encontrado.')
    return path


# Config keys the results view needs to explain a run.
_RUN_KEYS = {
    'MIN_REPLICAS': 1, 'MAX_REPLICAS': 3, 'SCALE_UP_CONN': 50, 'SCALE_DOWN_CONN': 10,
    'COOLDOWN_SECONDS': 10, 'TICK_SECONDS': 5, 'COST_PER_REPLICA_SEC': 0.0001,
    'ATTACK_MODE': 'yoyo', 'PULSE_ON': 20, 'PULSE_OFF': 20,
}


def _run_config(csv_path):
    """Config a run used: its saved JSON if present, else the current edos_config.

    Runs recorded before the JSON existed fall back to the current defaults, so
    the thresholds shown for them may not match what was actually used.
    """
    cfg_path = csv_path.with_suffix('.json')
    source, values = 'current', {}
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                values = json.load(f)
            source = 'run'
        except (OSError, ValueError):
            values = {}
    if source == 'current':
        import importlib
        import edos_config
        importlib.reload(edos_config)
        values = {k: getattr(edos_config, k, d) for k, d in _RUN_KEYS.items()}
    out = {}
    for k, d in _RUN_KEYS.items():
        v = values.get(k, d)
        try:
            out[k] = type(d)(v)
        except (TypeError, ValueError):
            out[k] = d
    out['source'] = source
    return out


def _attack_windows(t, conns, threshold):
    """Intervals where the LB holds more than `threshold` connections.

    The attack phase isn't logged, so it is inferred from the load: benign
    clients keep very few connections open, the slow-HTTP attack holds hundreds.
    Edges sit halfway between samples, since the change happened in between.
    """
    windows, start = [], None
    for i, c in enumerate(conns):
        if c > threshold and start is None:
            start = (t[i - 1] + t[i]) / 2 if i > 0 else t[i]
        elif c <= threshold and start is not None:
            windows.append([start, (t[i - 1] + t[i]) / 2])
            start = None
    if start is not None:
        windows.append([start, t[-1]])
    return windows


def _read_cost_csv(path):
    """Parse a cost CSV into time-normalized series plus a summary block."""
    import csv
    timestamps, replicas, conns, costs = [], [], [], []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            timestamps.append(float(row['timestamp']))
            replicas.append(int(row['active_replicas']))
            conns.append(int(row['active_conns']))
            costs.append(float(row['cumulative_cost']))

    if not timestamps:
        raise ValueError('CSV vazio.')

    t0 = timestamps[0]
    t = [x - t0 for x in timestamps]
    duration = t[-1]

    # replica-seconds: the EDoS meter — integrate active replicas over time.
    replica_seconds = 0.0
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        replica_seconds += replicas[i - 1] * dt

    cfg = _run_config(path)
    min_rep = cfg['MIN_REPLICAS']

    # "Sem ataque" baseline: the autoscaler bills replicas * tick * price per row,
    # so re-bill every row at MIN_REPLICAS using that row's own increment.
    baseline, extra_in, extra_out = [], 0.0, 0.0
    windows = _attack_windows(t, conns, cfg['SCALE_DOWN_CONN'])
    in_attack = lambda x: any(a <= x <= b for a, b in windows)
    prev_cost, base = 0.0, 0.0
    for i in range(len(t)):
        step = costs[i] - prev_cost
        prev_cost = costs[i]
        base_step = step * min_rep / replicas[i] if replicas[i] > 0 else 0.0
        base += base_step
        baseline.append(base)
        # Split the extra spend by whether it was billed while under attack
        if in_attack(t[i]):
            extra_in += step - base_step
        else:
            extra_out += step - base_step

    events = []
    for i in range(1, len(t)):
        if replicas[i] != replicas[i - 1]:
            events.append({'t': t[i], 'from': replicas[i - 1], 'to': replicas[i],
                           'dir': 'up' if replicas[i] > replicas[i - 1] else 'down'})

    baseline_cost = baseline[-1]
    extra = costs[-1] - baseline_cost
    summary = {
        'final_cost': costs[-1],
        'peak_replicas': max(replicas),
        'peak_conns': max(conns),
        'replica_seconds': replica_seconds,
        'duration': duration,
        'baseline_cost': baseline_cost,
        'extra_cost': extra,
        'extra_pct': (extra / baseline_cost * 100) if baseline_cost > 0 else 0.0,
        # Share of the extra spend billed while no attack traffic was present:
        # replicas the attacker no longer needs to keep busy
        'extra_after_attack_pct': (extra_out / extra * 100) if extra > 0 else 0.0,
        'attack_seconds': sum(b - a for a, b in windows),
        'pulses': len(windows),
        'scale_ups': sum(1 for e in events if e['dir'] == 'up'),
        'scale_downs': sum(1 for e in events if e['dir'] == 'down'),
        'attack_mode': cfg['ATTACK_MODE'],
    }
    return {'time': t, 'replicas': replicas, 'conns': conns, 'costs': costs,
            'baseline': baseline,
            # Scale-up fires once conns exceed replicas * SCALE_UP_CONN
            'scale_up_line': [r * cfg['SCALE_UP_CONN'] for r in replicas],
            'attack_windows': windows, 'events': events,
            'config': cfg, 'summary': summary}


def _load_metrics(csv_path):
    """Find the derived-metrics JSON paired with a cost CSV (current run or a
    timestamped backup) and return it, or None if absent."""
    if csv_path == COST_CSV:
        mpath = METRICS_JSON
    else:
        # edos_cost_<ts>.csv -> edos_metrics_<ts>.json
        name = csv_path.name.replace('edos_cost_', 'edos_metrics_')
        mpath = csv_path.with_name(name).with_suffix('.json')
    if mpath.exists():
        try:
            with open(mpath) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


@app.route('/api/results', methods=['GET'])
def get_results():
    """Return one run's series. ?file=<backup name> selects a past run."""
    try:
        path = _resolve_cost_csv(request.args.get('file'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    if not path.exists():
        return jsonify({'error': 'Resultados não encontrados.'}), 404
    try:
        payload = _read_cost_csv(path)
        payload['metrics'] = _load_metrics(path)
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """List past runs (backup CSVs) with lightweight summary metadata."""
    runs = []
    for path in sorted(FLOWS_DIR.glob(HISTORY_GLOB), reverse=True):
        # Filename shape: edos_cost_YYYYMMDD_HHMMSS.csv
        stamp = path.stem[len('edos_cost_'):]
        label = stamp
        try:
            dt = time.strptime(stamp, '%Y%m%d_%H%M%S')
            label = time.strftime('%d/%m/%Y %H:%M:%S', dt)
        except ValueError:
            pass
        entry = {'file': path.name, 'label': label}
        try:
            entry['summary'] = _read_cost_csv(path)['summary']
        except Exception:
            entry['summary'] = None
        runs.append(entry)
    return jsonify({'runs': runs})

if __name__ == '__main__':
    # Cria diretório de templates se não existir
    (BASE_DIR / 'templates').mkdir(exist_ok=True)
    # Rodar o servidor na porta 5000, escutando em todas as interfaces
    # No reloader: it watches every .py/.pyc here, so writing .edos_overrides.py (or
    # edos.py compiling its modules) restarted the server mid-run and orphaned edos.py.
    # No debug either: the Werkzeug debugger on 0.0.0.0 as root is a remote shell.
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False, use_reloader=False)
