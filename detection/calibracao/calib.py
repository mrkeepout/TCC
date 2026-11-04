"""Calibração offline do detector por janela (nível 2 generalizado).
Pergunta: com que intensidade de ataque UM pulso deixa de ser detectado?
- benignas 00/1..5 = treino (mu, Sigma); 00/6..10 = limiar(p99 das janelas) e FPR.
- ataque: só o 1º pulso (fluxos que começam em t<60s), limpo dos bugs.
- afinamento: mantém uma fração p das CONEXÕES (Src IP+Src Port) do atacante.
"""
import os, json, numpy as np, pandas as pd
from sklearn.covariance import LedoitWolf
BASE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","data")
VIP="192.168.100.10"; ATK={f"192.168.100.{i}" for i in range(103,111)}
JS=[5,10,20,30,60]; STEP=5; SEG=60; SLOW=1000
COLS=["Src IP","Src Port","Dst IP","Dst Port","Timestamp","Flow Duration","Flow Bytes/s"]
rng=np.random.default_rng(1337)

def load(path, tmax=None):
    df=pd.read_csv(path,usecols=COLS,low_memory=False)
    df["Dst Port"]=pd.to_numeric(df["Dst Port"],errors="coerce")
    df=df[(df["Dst IP"].astype(str)==VIP)&(df["Dst Port"]==80)].copy()
    ts=pd.to_datetime(df["Timestamp"],format="%d/%m/%Y %I:%M:%S %p",errors="coerce")
    df["t"]=(ts-ts.min()).dt.total_seconds()
    d=pd.to_numeric(df["Flow Duration"],errors="coerce")/1e6
    b=pd.to_numeric(df["Flow Bytes/s"],errors="coerce")
    df["valid"]=(d>=0)&np.isfinite(b)
    df["slow"]=df["valid"]&(b<SLOW)
    df["logdur"]=np.where(df["valid"],np.log1p(d.clip(lower=0)),np.nan)
    df["atk"]=df["Src IP"].astype(str).isin(ATK)
    df["conn"]=df["Src IP"].astype(str)+":"+df["Src Port"].astype(str)
    if tmax is not None: df=df[df.t<tmax]
    return df[["t","slow","logdur","atk","conn","Src IP"]]

def windows(df,j,t_end):
    """features por janela deslizante [s, s+j), s = 0,5,10,... até t_end-j"""
    t=df.t.values; out=[]
    for s in np.arange(0,t_end-j+1e-9,STEP):
        m=(t>=s)&(t<s+j); w=df[m]
        n=len(w); ns=int(w.slow.sum())
        ld=w.logdur.dropna()
        out.append([np.log1p(n),np.log1p(ns),ns/n if n else 0.0,
                    ld.median() if len(ld) else 0.0,w["Src IP"].nunique()])
    return np.array(out)       # cols: log n_req, log n_slow, frac_slow, med logdur, n_src

class Det:
    def __init__(s,train,j):
        X=np.vstack(train); s.mu=X.mean(0)
        s.VI=np.linalg.inv(LedoitWolf().fit(X-s.mu).covariance_)
        s.m1,s.s1=X[:,1].mean(),X[:,1].std()          # 1D: log n_slow (z-score)
    def score(s,X):
        d=X-s.mu
        return {"maha":np.einsum("ij,jk,ik->i",d,s.VI,d),
                "1d":((X[:,1]-s.m1)/s.s1)**2}         # 1D Mahalanobis = z^2

ben={k:load(f"{BASE}/00/final_report{k}.csv") for k in range(1,11)}
atk={(n,k):load(f"{BASE}/0{n}/final_report{k}.csv",tmax=SEG) for n in range(1,5) for k in range(1,11)}
res={"thr":{},"fpr":{},"rows":[]}
P=[1,.75,.5,.35,.25,.15,.10,.05,.025]; DRAWS=5
for j in JS:
    det=Det([windows(ben[k],j,360) for k in range(1,6)],j)
    val={k:det.score(windows(ben[k],j,360)) for k in range(6,11)}
    thr={m:float(np.percentile(np.concatenate([v[m] for v in val.values()]),99)) for m in ("maha","1d")}
    res["thr"][j]=thr
    # FPR por segmento de 60s nas benignas de validação (mesma regra de alarme do ataque)
    fpr={}
    for m in ("maha","1d"):
        hits=[]
        for k in range(6,11):
            for s0 in range(0,360,SEG):
                seg=ben[k][(ben[k].t>=s0)&(ben[k].t<s0+SEG)].assign(t=lambda x:x.t-s0)
                hits.append(det.score(windows(seg,j,SEG))[m].max()>thr[m])
        fpr[m]=float(np.mean(hits))
    res["fpr"][j]=fpr
    for (n,k),df in atk.items():
        conns=df.loc[df.atk,"conn"].unique()
        for p in P:
            for r in range(DRAWS if p<1 else 1):
                keep=set(rng.choice(conns,size=max(0,int(round(p*len(conns)))),replace=False))
                sub=df[(~df.atk)|df.conn.isin(keep)]
                sc=det.score(windows(sub,j,SEG))
                res["rows"].append(dict(j=j,n_atk=n,run=k,p=p,draw=r,eff_conns=200*n*p,
                    maha=bool(sc["maha"].max()>thr["maha"]),uni=bool(sc["1d"].max()>thr["1d"])))
    print(f"j={j:>2}s  limiar maha={thr['maha']:.1f}  1d={thr['1d']:.1f} | FPR/seg maha={fpr['maha']:.0%} 1d={fpr['1d']:.0%}",flush=True)
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","out","calibracao"),exist_ok=True)
pd.DataFrame(res["rows"]).to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","out","calibracao","calib_rows.csv"),index=False)
json.dump({"thr":res["thr"],"fpr":res["fpr"]},open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","out","calibracao","calib_thr_fpr.json"),"w"),indent=2)
print("ok")
