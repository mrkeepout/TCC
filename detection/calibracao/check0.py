import os
import numpy as np, pandas as pd, sys
sys.argv=["x"]; src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib.py")).read().split("ben={k:load")[0]; exec(src)
ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
j=10; det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
val=[det.score(windows(ben[k],j,360)) for k in range(6,11)]
thr={m:np.percentile(np.concatenate([v[m] for v in val]),99) for m in ("maha","1d")}
print("Benignas 1–5 (treino), janela 10s: n_req e n_lentas médios por janela:",
      np.round(np.expm1(np.vstack([windows(ben[k],j,360) for k in range(1,6)])[:,:2]).mean(0),1))
for n in (1,4):
    hits={"maha":[],"1d":[]}; feats=[]
    for k in range(1,11):
        df=load(f"{BASE}/0{n}/final_report{k}.csv",tmax=60)
        sub=df[~df.atk]                       # remove 100% do atacante
        X=windows(sub,j,60); sc=det.score(X); feats.append(X)
        for m in hits: hits[m].append(sc[m].max()>thr[m])
    F=np.vstack(feats)
    print(f"\n{n} atacante(s), SÓ tráfego benigno da rodada de ataque (atacante removido 100%):")
    print(f"   P_d 1D={np.mean(hits['1d']):.0%}   P_d Mahalanobis={np.mean(hits['maha']):.0%}")
    print(f"   benignos nessa rodada: n_req/janela={np.expm1(F[:,0]).mean():.1f}  n_lentas/janela={np.expm1(F[:,1]).mean():.1f}  frac_lentas={F[:,2].mean():.2f}  max n_lentas={np.expm1(F[:,1]).max():.0f}")
    a=load(f"{BASE}/0{n}/final_report1.csv",tmax=60); a=a[a.atk]
    print(f"   atacante (rodada 1): {len(a)} fluxos, {a.conn.nunique()} conexões distintas, t de {a.t.min():.0f}s a {a.t.max():.0f}s")
