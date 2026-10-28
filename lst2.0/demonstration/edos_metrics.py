#!/usr/bin/env python3
# Copyright (C) 2022 Alexandre Mitsuru Kaihara
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Brief: Post-processing of an EDoS run into the derived metrics that sustain the
# "low-and-slow costs more per byte" argument. Reads only the CSVs/JSON a run
# already produces (edos_cost.csv, run_manifest.json, qos_l*.csv, final_report.csv)
# -- pure analysis, no infrastructure -- and emits a report + edos_metrics.json.
#
#   NCO   Normalized Cost Overhead: how far the attack drove the bill above the
#         baseline of running MIN_REPLICAS (compute) for the whole run.
#           baseline = MIN_REPLICAS * p_vm_per_sec * duration
#           NCO      = (total_cost - baseline) / baseline
#   EAF   Economic Amplification Factor: damage per attacker byte.
#           EAF = (total_cost - baseline) / attacker_bytes
#   W_yoyo (yoyo only) Fraction of billed replica-seconds spent in the attack's
#         OFF phases -- capacity paid for while the attacker was idle.
#
# Every assumption is printed and stored, so the definitions are auditable and
# easy to adjust for the paper.
#
# Usage: python3 edos_metrics.py [--flows-dir flows] [--out flows/edos_metrics.json]

import argparse
import csv
import glob
import json
import os
import sys


# --- small helpers --------------------------------------------------------
def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q / 100.0 * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * frac


def _norm(name):
    return ''.join(c for c in name.lower() if c.isalnum())


def load_manifest(flows_dir):
    path = os.path.join(flows_dir, 'run_manifest.json')
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def resolve_config(manifest):
    """Pull the knobs the metrics need from the manifest, falling back to
    edos_config, then to hard defaults -- so the script runs even on a bare
    CSV from another machine."""
    cfgm = manifest.get('config', {})
    defaults = {'TICK_SECONDS': 5, 'MIN_REPLICAS': 1, 'ATTACK_MODE': 'sustained',
                'PULSE_ON': 20, 'PULSE_OFF': 20}
    pricing_default = {'p_vm_per_sec': 0.0416 / 3600}
    if not cfgm:
        try:
            import edos_config as c
            cfgm = {'TICK_SECONDS': c.TICK_SECONDS, 'MIN_REPLICAS': c.MIN_REPLICAS,
                    'ATTACK_MODE': c.ATTACK_MODE, 'PULSE_ON': c.PULSE_ON,
                    'PULSE_OFF': c.PULSE_OFF, 'PRICING': getattr(c, 'PRICING', pricing_default)}
        except Exception:
            cfgm = {}
    out = dict(defaults)
    out.update({k: cfgm[k] for k in defaults if k in cfgm})
    out['PRICING'] = cfgm.get('PRICING', pricing_default)
    return out


# --- cost timeline --------------------------------------------------------
def load_cost(flows_dir, tick):
    path = os.path.join(flows_dir, 'edos_cost.csv')
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    if not rows:
        raise SystemExit(f"[metrics] {path} has no data rows.")
    t = [float(r['timestamp']) for r in rows]
    billed = [int(r['active_replicas']) for r in rows]  # alive (billed) replicas
    total_cost = float(rows[-1]['cumulative_cost'])
    duration = (t[-1] - t[0]) + tick  # +1 tick so a single row isn't zero-length
    replica_seconds = sum(b * tick for b in billed)
    # Per-component finals (Phase 2 columns), if present.
    def last(col):
        return float(rows[-1][col]) if col in rows[-1] else None
    return {
        'rows': rows, 't': t, 'billed': billed,
        'total_cost': total_cost, 'duration': duration,
        'replica_seconds': replica_seconds,
        'vm_cost': last('vm_cost'), 'lcu_cost': last('lcu_cost'),
        'bw_cost': last('bw_cost'),
        't0': t[0],
    }


# --- attacker bytes (for EAF) --------------------------------------------
def attacker_bytes(flows_dir):
    path = os.path.join(flows_dir, 'final_report.csv')
    if not os.path.isfile(path):
        return None, 'final_report.csv not found'
    with open(path) as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None, 'final_report.csv empty'
        hn = [_norm(h) for h in header]
        # Locate Label and the forward/backward byte-total columns by fuzzy name.
        def find(pred):
            for i, h in enumerate(hn):
                if pred(h):
                    return i
            return None
        i_label = find(lambda h: h == 'label')
        i_fwd = find(lambda h: 'totlen' in h and 'fwd' in h) \
            or find(lambda h: 'fwd' in h and 'byte' in h)
        i_bwd = find(lambda h: 'totlen' in h and 'bwd' in h) \
            or find(lambda h: 'bwd' in h and 'byte' in h)
        if i_label is None or i_fwd is None:
            return None, 'label/byte columns not found in final_report.csv'
        total = 0.0
        n = 0
        for cols in reader:
            if len(cols) <= max(i_label, i_fwd, i_bwd or 0):
                continue
            if cols[i_label].strip().lower() != 'attack':
                continue
            try:
                total += float(cols[i_fwd])
                if i_bwd is not None:
                    total += float(cols[i_bwd])
            except ValueError:
                continue
            n += 1
        if n == 0:
            return None, 'no attack-labelled flows in final_report.csv'
        return total, f'{n} attack flows (fwd+bwd bytes at the edge)'


# --- QoS ------------------------------------------------------------------
def phase_of(ts, t0, pulse_on, pulse_off):
    period = pulse_on + pulse_off
    if period <= 0:
        return 'on'
    off = (ts - t0) % period
    return 'off' if off >= pulse_on else 'on'


def load_qos(flows_dir, cfg, t0):
    files = sorted(glob.glob(os.path.join(flows_dir, 'qos_*.csv')))
    yoyo = cfg['ATTACK_MODE'] == 'yoyo'
    agg = {'files': len(files), 'requests': 0, 'ok': 0,
           'latencies_ok': [], 'by_profile': {}, 'on': {'req': 0, 'ok': 0},
           'off': {'req': 0, 'ok': 0}}
    for path in files:
        try:
            with open(path) as f:
                for r in csv.DictReader(f):
                    agg['requests'] += 1
                    ok = r.get('ok', '0') == '1'
                    prof = r.get('profile', '?')
                    p = agg['by_profile'].setdefault(prof, {'req': 0, 'ok': 0})
                    p['req'] += 1
                    if ok:
                        agg['ok'] += 1
                        p['ok'] += 1
                        try:
                            agg['latencies_ok'].append(float(r['latency_ms']))
                        except (KeyError, ValueError):
                            pass
                    if yoyo:
                        try:
                            ph = phase_of(float(r['timestamp']), t0,
                                          cfg['PULSE_ON'], cfg['PULSE_OFF'])
                            agg[ph]['req'] += 1
                            agg[ph]['ok'] += 1 if ok else 0
                        except (KeyError, ValueError):
                            pass
        except OSError:
            continue
    return agg, yoyo


# --- W_yoyo ---------------------------------------------------------------
def w_yoyo(cost, cfg):
    if cfg['ATTACK_MODE'] != 'yoyo':
        return None, 'not a yoyo run'
    tick = cfg['TICK_SECONDS']
    t0 = cost['t0']
    wasted = 0.0
    for t, billed in zip(cost['t'], cost['billed']):
        if phase_of(t, t0, cfg['PULSE_ON'], cfg['PULSE_OFF']) == 'off':
            wasted += billed * tick
    total = cost['replica_seconds']
    if total <= 0:
        return None, 'no billed replica-seconds'
    return wasted / total, f'{wasted:.0f}/{total:.0f} replica-s in OFF phases'


# --- main -----------------------------------------------------------------
def rate(ok, req):
    return (ok / req) if req else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--flows-dir', default='flows')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    out_path = args.out or os.path.join(args.flows_dir, 'edos_metrics.json')

    manifest = load_manifest(args.flows_dir)
    cfg = resolve_config(manifest)
    tick = cfg['TICK_SECONDS']
    p_vm = cfg['PRICING'].get('p_vm_per_sec', 0.0416 / 3600)

    cost = load_cost(args.flows_dir, tick)

    # NCO + EAF share the baseline and the extra cost above it.
    baseline = cfg['MIN_REPLICAS'] * p_vm * cost['duration']
    extra = cost['total_cost'] - baseline
    nco = (extra / baseline) if baseline > 0 else None

    abytes, abytes_note = attacker_bytes(args.flows_dir)
    eaf = (extra / abytes) if (abytes and abytes > 0) else None

    wy, wy_note = w_yoyo(cost, cfg)
    qos, yoyo = load_qos(args.flows_dir, cfg, cost['t0'])

    lat = sorted(qos['latencies_ok'])
    result = {
        'run': {
            'git_sha': manifest.get('git_sha'),
            'seed': manifest.get('seed'),
            'attack_mode': cfg['ATTACK_MODE'],
            'duration_s': round(cost['duration'], 1),
        },
        'cost': {
            'total': cost['total_cost'], 'baseline_min_pool': baseline,
            'extra_above_baseline': extra, 'replica_seconds': cost['replica_seconds'],
            'vm_cost': cost['vm_cost'], 'lcu_cost': cost['lcu_cost'],
            'bw_cost': cost['bw_cost'],
        },
        'NCO': nco,
        'EAF': eaf,
        'EAF_cost_per_MB': (eaf * 1e6) if eaf is not None else None,
        'attacker_bytes': abytes,
        'attacker_bytes_note': abytes_note,
        'W_yoyo': wy,
        'W_yoyo_note': wy_note,
        'qos': {
            'files': qos['files'], 'requests': qos['requests'],
            'success_rate': rate(qos['ok'], qos['requests']),
            'latency_ms': {
                'mean': (sum(lat) / len(lat)) if lat else None,
                'p50': _percentile(lat, 50), 'p95': _percentile(lat, 95),
                'p99': _percentile(lat, 99),
            },
            'by_profile': {k: {'requests': v['req'],
                               'success_rate': rate(v['ok'], v['req'])}
                           for k, v in qos['by_profile'].items()},
            'success_rate_attack_on': rate(qos['on']['ok'], qos['on']['req']) if yoyo else None,
            'success_rate_attack_off': rate(qos['off']['ok'], qos['off']['req']) if yoyo else None,
        },
        'assumptions': {
            'baseline': 'MIN_REPLICAS * p_vm_per_sec * duration (compute floor, no scaling)',
            'EAF': 'extra_cost / attacker_bytes; ' + abytes_note,
            'W_yoyo': 'billed replica-seconds in OFF phases / total; pulses aligned to '
                      'first cost timestamp (approximate)',
            'pricing_p_vm_per_sec': p_vm,
        },
    }

    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2)

    _print_report(result)
    print(f"\n[metrics] wrote {out_path}")


def _fmt(x, nd=4):
    return 'n/a' if x is None else f'{x:.{nd}f}'


def _print_report(r):
    print("\n[LST2.0][EDoS] ===== Derived metrics =====")
    print(f"  Run            : mode={r['run']['attack_mode']} "
          f"seed={r['run']['seed']} duration={r['run']['duration_s']}s")
    c = r['cost']
    print(f"  Total cost     : {_fmt(c['total'])}  "
          f"(baseline {_fmt(c['baseline_min_pool'])}, "
          f"extra {_fmt(c['extra_above_baseline'])})")
    if c['vm_cost'] is not None:
        print(f"  Cost breakdown : vm={_fmt(c['vm_cost'])} "
              f"lcu={_fmt(c['lcu_cost'])} bw={_fmt(c['bw_cost'])}")
    print(f"  NCO            : {_fmt(r['NCO'])}  (cost overhead above baseline)")
    print(f"  EAF            : {_fmt(r['EAF'], 10)} per byte  "
          f"({_fmt(r['EAF_cost_per_MB'])} per MB) | {r['attacker_bytes_note']}")
    print(f"  W_yoyo         : {_fmt(r['W_yoyo'], 3)}  ({r['W_yoyo_note']})")
    q = r['qos']
    print(f"  QoS (benign)   : {q['requests']} reqs from {q['files']} clients, "
          f"success={_fmt(q['success_rate'], 3)}")
    lm = q['latency_ms']
    print(f"    latency ms   : mean={_fmt(lm['mean'],1)} p50={_fmt(lm['p50'],1)} "
          f"p95={_fmt(lm['p95'],1)} p99={_fmt(lm['p99'],1)}")
    if q['success_rate_attack_on'] is not None:
        print(f"    success ON/OFF: {_fmt(q['success_rate_attack_on'],3)} "
              f"/ {_fmt(q['success_rate_attack_off'],3)} (attack pulse)")
    for prof, v in q['by_profile'].items():
        print(f"    {prof:<12}: {v['requests']} reqs, success={_fmt(v['success_rate'],3)}")


if __name__ == '__main__':
    main()
