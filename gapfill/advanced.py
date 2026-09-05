import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator, UnivariateSpline
from gapfill.core import prepare, conceal, clean_context, features, _neighbors, SENSORS


def features_v2(frame, indices):
    base=features(frame,indices)
    df=prepare(frame)
    context=clean_context(conceal(df,indices))
    result=[]
    for (field,year),target in df.loc[indices].groupby(['anon_polygon_id','year'],sort=False):
        field_data=context[context.anon_polygon_id==field]
        season=field_data[field_data.year==year].sort_values('date')
        tt=target.doy.to_numpy(dtype=float)
        times=season.doy.to_numpy(dtype=float)
        out={}
        for col in ['primary_ndvi',*SENSORS]:
            vals=season[col].to_numpy(dtype=float)
            finite=np.isfinite(vals); t=times[finite]; y=vals[finite]
            prefix='v2_'+col
            out.update(_neighbors(times,vals,tt,prefix,3))
            for q in [10,50,90]:out[f'{prefix}_q{q}']=np.full(len(tt),np.percentile(y,q) if len(y) else np.nan)
            delta=t[None,:]-tt[:,None]
            for width in [3,7,15,30]:
                w=np.exp(-.5*(delta/width)**2)
                s0=w.sum(axis=1);s1=(w*delta).sum(axis=1);s2=(w*delta**2).sum(axis=1)
                sy=w@y;sxy=(w*delta)@y
                det=s0*s2-s1*s1
                mean=np.divide(sy,s0,out=np.full(len(tt),np.nan),where=s0>1e-6)
                intercept=np.divide(s2*sy-s1*sxy,det,out=mean.copy(),where=det>1e-6)
                slope=np.divide(s0*sxy-s1*sy,det,out=np.zeros(len(tt)),where=det>1e-6)
                out[f'{prefix}_local_{width}']=np.clip(intercept,-1,1)
                out[f'{prefix}_mean_{width}']=mean
                out[f'{prefix}_slope_{width}']=slope
                out[f'{prefix}_support_{width}']=s0
            if len(t)>1:
                out[f'{prefix}_pchip']=PchipInterpolator(t,y,extrapolate=False)(tt)
            else:out[f'{prefix}_pchip']=np.full(len(tt),np.nan)
            for noise in [.03,.07]:
                prediction=np.full(len(tt),np.nan)
                if len(t)>=6:
                    curve=UnivariateSpline(t,y,k=3,s=len(t)*noise**2,ext=3)
                    prediction=curve(tt)
                out[f'{prefix}_spline_{noise}']=np.clip(prediction,-1,1)
            left=out[f'{prefix}_left0'];right=out[f'{prefix}_right0']
            ld=out[f'{prefix}_left0_days'];rd=out[f'{prefix}_right0_days']
            out[f'{prefix}_slope_gap']=(right-left)/np.maximum(ld+rd,1)
            out[f'{prefix}_jump']=right-left
            for cadence in [5,8,16]:
                distance=np.minimum(np.nan_to_num(ld,nan=10000),np.nan_to_num(rd,nan=10000))
                phase=distance%cadence
                out[f'{prefix}_phase_{cadence}']=np.minimum(phase,cadence-phase)
        source=np.select([season.s2_ndvi.notna(),season.landsat_ndvi.notna(),season.modis_ndvi.notna()],[0.,1.,2.],default=np.nan)
        out.update(_neighbors(times,source,tt,'v2_source',2))
        for offset in [-2,-1,1,2]:
            past=field_data[(field_data.year==year+offset)&field_data.primary_ndvi.notna()]
            ht=past.doy.to_numpy(dtype=float);hy=past.primary_ndvi.to_numpy(dtype=float)
            w=np.exp(-.5*((tt[:,None]-ht[None,:])/10)**2);denom=w.sum(axis=1)
            out[f'v2_year_offset_{offset}']=np.divide(w@hy,denom,out=np.full(len(tt),np.nan),where=denom>1e-6)
        peers=context[(context.anon_polygon_id!=field)&(context.year==year)]
        for col in SENSORS:
            counts=peers.groupby('date')[col].count()
            out[f'v2_peer_{col}_presence']=target.date.map(counts).fillna(0).to_numpy()/max(1,peers.anon_polygon_id.nunique())
        result.append(pd.DataFrame(out,index=target.index))
    if not len(base):return base
    return pd.concat([base,pd.concat(result).loc[indices]],axis=1).replace([np.inf,-np.inf],np.nan)
