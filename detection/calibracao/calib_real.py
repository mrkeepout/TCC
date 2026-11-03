import os
import numpy as np, pandas as pd
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib.py")).read().split("ben={k:load")[0]; exec(src)
ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
C={1:100,2:25,3:50,4:75,5:150}
runs={C[k]:load(f"{BASE}/calibracao/final_report{k}.csv") for k in C}
rows=[]
for j in JS:
    det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
    val=[det.score(windows(ben[k],j,330)) for k in range(6,11)]
    thr={m:np.percentile(np.concatenate([v[m] for v in val]),99) for m in ("maha","1d")}
    for c,df in sorted(runs.items()):
        for p,(a,b) in enumerate([(0,100),(120,220)],1):          # pulso p (sem os 20s finais)
            seg=df[(df.t>=a)&(df.t<b)].assign(t=lambda x:x.t-a)
            sc=det.score(windows(seg,j,b-a)); st=np.arange(0,b-a-j+1e-9,5)
            for m in ("1d","maha"):
                hit=sc[m]>thr[m]
                rows.append(dict(j=j,conns=c,pulso=p,met=m,det=bool(hit.any()),
                                 lat=float(st[hit][0]+j) if hit.any() else np.nan,
                                 razao=float(sc[m].max()/thr[m])))
r=pd.DataFrame(rows)
for m,lab in [("1d","1D (contagem de lentas)"),("maha","Mahalanobis (5 features)")]:
    s=r[r.met==m]
    print(f"\n=== {lab}: pulsos detectados (de 2) — linhas: conexões, colunas: j (s)")
    print(s.groupby(["conns","j"]).det.sum().unstack().astype(int).to_string())
    print(f"--- quanto o pior caso passa do limiar (máx score / limiar, menor dos 2 pulsos)")
    print(s.groupby(["conns","j"]).razao.min().unstack().round(1).to_string())
r.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","out","calibracao","calib_real_rows.csv"),index=False)
