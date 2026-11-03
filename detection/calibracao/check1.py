import os
import numpy as np, sys
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib.py")).read().split("ben={k:load")[0]; exec(src)
ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
j=10; det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
val=[det.score(windows(ben[k],j,360)) for k in range(6,11)]
thr={m:np.percentile(np.concatenate([v[m] for v in val]),99) for m in ("maha","1d")}
print("Benignas de VALIDAÇÃO (6–10), alarme por segmento de 60s:")
for s0 in range(0,360,60):
    h={"maha":[],"1d":[]}
    for k in range(6,11):
        seg=ben[k][(ben[k].t>=s0)&(ben[k].t<s0+60)].assign(t=lambda x:x.t-s0)
        sc=det.score(windows(seg,j,60))
        for m in h: h[m].append(sc[m].max()>thr[m])
    print(f"   t={s0:3d}–{s0+60:3d}s   1D={np.mean(h['1d']):.0%}   Mahalanobis={np.mean(h['maha']):.0%}")
# benignos da rodada de ataque, por janela: onde dispara? (durante o pulso ou fora?)
print("\n1 atacante, atacante removido: janelas que disparam (início da janela, s) — Mahalanobis / 1D")
for k in range(1,11):
    df=load(f"{BASE}/01/final_report{k}.csv",tmax=60); sub=df[~df.atk]
    sc=det.score(windows(sub,j,60)); st=np.arange(0,60-j+1e-9,5)
    print(f"   rodada {k:2d}: maha {list(st[sc['maha']>thr['maha']].astype(int))}   1d {list(st[sc['1d']>thr['1d']].astype(int))}")
