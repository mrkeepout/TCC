import os
import numpy as np, pandas as pd
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib.py")).read().split("ben={k:load")[0]; exec(src)
ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
C={1:100,2:25,3:50,4:75,5:150}
c=pd.read_csv(f"{BASE}/calibracao/edos_cost1.csv")
for j in (10,30):
    det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
    thr={m:np.percentile(np.concatenate([det.score(windows(ben[k],j,330))[m] for k in range(6,11)]),99) for m in ("maha","1d")}
    print(f"\n=== j={j}s: % de janelas acima do limiar — PULSO (janela inteira dentro de ON) vs OCIOSO (janela inteira dentro de OFF)")
    for k,cn in sorted(C.items(),key=lambda x:x[1]):
        df=load(f"{BASE}/calibracao/final_report{k}.csv"); X=windows(df,j,230); sc=det.score(X)
        st=np.arange(0,230-j+1e-9,5)
        # ON/OFF a partir do atacante: janela 'ON' se tem fluxo do atacante, 'OFF' se nenhuma e longe (>10s) de qualquer fluxo dele
        at=np.sort(df.t[df.atk].values)
        def near(s,e,pad): return ((at>=s-pad)&(at<e+pad)).any()
        on=np.array([near(s,s+j,0) for s in st]); off=np.array([not near(s,s+j,10) for s in st])
        out=[]
        for m in ("1d","maha"):
            h=sc[m]>thr[m]
            out.append(f"{m}: pulso {h[on].mean():4.0%} | ocioso {h[off].mean():4.0%} (n={off.sum()})")
        print(f"  {cn:3d} conns  "+"   ".join(out))
