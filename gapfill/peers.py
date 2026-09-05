"""Cross-field acquisition context; all query labels remain masked."""
import numpy as np
import pandas as pd
from gapfill.core import prepare, conceal, clean_context, SENSORS


def peer_features(frame,indices):
    df=prepare(frame);ctx=clean_context(conceal(df,indices))
    # Residual of each observed peer relative to that peer's OTHER dates.
    residuals={}
    for col in SENSORS:
        residual=pd.Series(np.nan,index=ctx.index)
        for _,g in ctx[ctx[col].notna()].groupby(['anon_polygon_id','year']):
            g=g.sort_values('date');t=g.doy.to_numpy(float);y=g[col].to_numpy(float)
            if len(y)<3:continue
            estimated=(y[:-2]*(t[2:]-t[1:-1])+y[2:]*(t[1:-1]-t[:-2]))/(t[2:]-t[:-2])
            residual.loc[g.index[1:-1]]=y[1:-1]-estimated
        residuals[col]=residual
    out=[]
    for (field,year),target in df.loc[indices].groupby(['anon_polygon_id','year'],sort=False):
        peers=ctx[(ctx.anon_polygon_id!=field)&(ctx.year==year)].copy()
        own=ctx[(ctx.anon_polygon_id==field)&(ctx.year==year)]
        x={}
        for col in SENSORS:
            peers['residual']=residuals[col].loc[peers.index]
            daily=peers.groupby('date').agg(mean=(col,'mean'),median=(col,'median'),std=(col,'std'),
                                            residual_mean=('residual','mean'),residual_median=('residual','median'),residual_std=('residual','std'))
            for key in daily:
                x[f'peer_{col}_{key}']=target.date.map(daily[key]).to_numpy()
            # Estimate field-to-peer offsets from visible, coincident dates only.
            own_values=own.set_index('date')[col].dropna()
            candidates=[]
            for peer_id,peer in peers[peers[col].notna()].groupby('anon_polygon_id'):
                peer_values=peer.set_index('date')[col]
                common=own_values.index.intersection(peer_values.index)
                if len(common)<5:continue
                a=own_values.loc[common].to_numpy();b=peer_values.loc[common].to_numpy()
                offset=np.median(a-b)
                error=np.sqrt(np.mean((a-b-offset)**2))
                candidates.append((error,offset,peer_values))
            candidates.sort(key=lambda item:item[0])
            candidate_values=[];weights=[]
            for error,offset,values in candidates[:5]:
                candidate_values.append(target.date.map(values).to_numpy()+offset)
                weights.append(1/max(error,.03)**2)
            if candidate_values:
                values=np.array(candidate_values);w=np.array(weights)[:,None]*np.isfinite(values)
                denom=w.sum(axis=0)
                estimate=np.divide((w*np.nan_to_num(values)).sum(axis=0),denom,out=np.full(len(target),np.nan),where=denom>0)
                x[f'peer_{col}_calibrated']=estimate
                x[f'peer_{col}_calibration_rmse']=candidates[0][0]
            else:
                x[f'peer_{col}_calibrated']=np.full(len(target),np.nan)
                x[f'peer_{col}_calibration_rmse']=np.nan
        out.append(pd.DataFrame(x,index=target.index))
    return pd.concat(out).loc[indices] if out else pd.DataFrame(index=indices)
