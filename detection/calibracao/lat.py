import os
import numpy as np, pandas as pd
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"calib.py")).read().split("ben={k:load")[0]; exec(src)
ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
C={1:100,2:25,3:50,4:75,5:150}
print("latência = do 1º fluxo do atacante no pulso até o FIM da 1ª janela em alarme (s) | scale-up = 1º tick com réplica 'warming' após o início do pulso")
for j in (5,10):
    det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
    thr=np.percentile(np.concatenate([det.score(windows(ben[k],j,330))["1d"] for k in range(6,11)]),99)
    print(f"\n=== j={j}s (detector 1D)")
    for k,cn in sorted(C.items(),key=lambda x:x[1]):
        df=load(f"{BASE}/calibracao/final_report{k}.csv"); c=pd.read_csv(f"{BASE}/calibracao/edos_cost{k}.csv")
        ct=(c.timestamp-c.timestamp.iloc[0]).values
        out=[]
        for a,b in [(0,120),(120,240)]:
            at=df.t[df.atk&(df.t>=a)&(df.t<b)]
            if at.empty: continue
            p0=at.min()
            # janelas deslizantes alinhadas a partir de 'a', passo 5s
            st=np.arange(a,b-j+1e-9,5); X=windows(df[(df.t>=a)&(df.t<b)].assign(t=lambda x:x.t-a),j,b-a)
            hit=det.score(X)["1d"]>thr
            lat=(st[hit][0]+j-p0) if hit.any() else np.nan
            w=np.where((ct>=p0)&(c.warming.values>0))[0]
            su=ct[w[0]]-p0 if len(w) and ct[w[0]]<b else np.nan
            out.append(f"pulso@{p0:5.0f}s: detecta em {lat:4.0f}s | scale-up em {'—' if np.isnan(su) else f'{su:.0f}s'}")
        print(f"  {cn:3d} conns  "+"   ".join(out))
