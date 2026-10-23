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

# Brief: Plots the EDoS cost timeline produced by the autoscaler
# (flows/edos_cost.csv) into a figure showing, over time: the number of active
# replicas, the active connections at the load balancer, and the cumulative
# cost. Makes the "economic" effect of the attack visible for the report.
#
# Usage:
#   pip install matplotlib
#   python3 plot_edos.py [--csv flows/edos_cost.csv] [--out flows/edos_plot.png]

import argparse
import csv
import sys


def load(path):
    t, replicas, conns, cost = [], [], [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            t.append(float(row['timestamp']))
            replicas.append(int(row['active_replicas']))
            conns.append(int(row['active_conns']))
            cost.append(float(row['cumulative_cost']))
    if not t:
        raise SystemExit(f"[plot] {path} has no data rows yet.")
    t0 = t[0]
    t = [x - t0 for x in t]  # seconds since experiment start
    return t, replicas, conns, cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default='flows/edos_cost.csv')
    ap.add_argument('--out', default='flows/edos_plot.png')
    ap.add_argument('--show', action='store_true', help='open an interactive window')
    args = ap.parse_args()

    try:
        import matplotlib
        if not args.show:
            matplotlib.use('Agg')  # headless: just write the PNG
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("[plot] matplotlib not installed. Run: pip install matplotlib")

    t, replicas, conns, cost = load(args.csv)

    fig, ax_left = plt.subplots(figsize=(11, 5))

    # Left axis: counts (replicas as a step line, connections as a thin line).
    ax_left.step(t, replicas, where='post', color='#d62728', linewidth=2.2,
                 label='Réplicas ativas')
    ax_left.plot(t, conns, color='#7f7f7f', linewidth=1.0, alpha=0.8,
                 label='Conexões no LB')
    ax_left.set_xlabel('Tempo (s desde o início)')
    ax_left.set_ylabel('Réplicas / Conexões')
    ax_left.set_ylim(bottom=0)
    ax_left.grid(True, alpha=0.3)

    # Right axis: cumulative cost.
    ax_right = ax_left.twinx()
    ax_right.plot(t, cost, color='#1f77b4', linewidth=2.2, label='Custo acumulado')
    ax_right.set_ylabel('Custo acumulado (réplica-s)')
    ax_right.set_ylim(bottom=0)

    # Merge legends from both axes.
    lines = ax_left.get_lines() + ax_right.get_lines()
    ax_left.legend(lines, [l.get_label() for l in lines], loc='upper left')

    plt.title('EDoS — elasticidade e custo ao longo do ataque')
    fig.tight_layout()

    fig.savefig(args.out, dpi=130)
    print(f"[plot] saved {args.out}")
    print(f"[plot] replicas: min={min(replicas)} max={max(replicas)} | "
          f"final cost={cost[-1]:.4f} replica-s over {t[-1]:.0f}s")
    if args.show:
        plt.show()


if __name__ == '__main__':
    main()
