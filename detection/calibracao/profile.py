import os
import pandas as pd, numpy as np, glob, os, re
base=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","data")
rows=[]
for d in sorted(os.listdir(base)):
    for fr in sorted(glob.glob(f"{base}/{d}/final_report*.csv")):
        k=re.findall(r"final_report(\d*)\.csv",fr)[0]
        df=pd.read_csv(fr,low_memory=False,usecols=["Src IP","Dst IP","Dst Port","Timestamp","Label"])
        ts=pd.to_datetime(df["Timestamp"],format="%d/%m/%Y %I:%M:%S %p",errors="coerce")
        lab=df["Label"].value_counts().to_dict()
        atk_ips=sorted(df.loc[df.Label=="attack","Src IP"].astype(str).unique())
        cf=f"{base}/{d}/edos_cost{k}.csv"
        if os.path.exists(cf):
            c=pd.read_csv(cf); maxr=c.active_replicas.max(); cost=c.cumulative_cost.iloc[-1]; cdur=c.timestamp.iloc[-1]-c.timestamp.iloc[0]
        else: maxr=cost=cdur=np.nan
        rows.append(dict(cen=d,run=k or "x",dur_flows=(ts.max()-ts.min()).total_seconds(),n=len(df),
            atk=lab.get("attack",0),ben=lab.get("benign",0),unk=lab.get("unknown",0),
            atk_ips=",".join(i.split(".")[-1] for i in atk_ips),maxrep=maxr,cost=round(cost,5),cost_dur=cdur))
r=pd.DataFrame(rows); pd.set_option("display.width",250); pd.set_option("display.max_rows",200)
print(r.to_string(index=False))
