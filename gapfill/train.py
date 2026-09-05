import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from gapfill.core import prepare, features


def masks(df, seed, mode):
    rng = np.random.default_rng(seed)
    indices = []
    for _, group in df[df.primary_ndvi.notna()].groupby(['anon_polygon_id','year']):
        group = group.sort_values('date')
        if len(group)<5:
            continue
        if mode == 'random':
            indices.extend(rng.choice(group.index, max(1,round(len(group)*.2)), replace=False))
        else:
            lo, hi = group.doy.min(), group.doy.max()
            start = rng.uniform(lo, max(lo+1,hi-29))
            indices.extend(group.index[(group.doy>=start)&(group.doy<start+30)])
    return np.array(sorted(indices),dtype=int)


def samples(df, seeds):
    xs, ys, modes = [], [], []
    for seed, mode in seeds:
        indices = masks(df,seed,mode)
        xs.append(features(df,indices))
        ys.append(df.loc[indices,'primary_ndvi'].to_numpy())
        modes.extend([mode]*len(indices))
    return pd.concat(xs,ignore_index=True),np.concatenate(ys),np.array(modes)


def metric(y,pred):
    rmse=float(np.sqrt(np.mean((y-pred)**2)))
    return {'rmse':rmse,'gapscore':round(30*max(0,1-rmse/.1),2),'n':len(y)}


def train(input_file, output_dir, iterations=650, seed=42):
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    df=prepare(pd.read_csv(input_file))
    fields=np.array(sorted(df.anon_polygon_id.unique()))
    if len(fields)<10:
        raise ValueError('At least 10 fields are needed for independent train/tuning/holdout groups')
    np.random.default_rng(seed).shuffle(fields)
    n=max(2,round(len(fields)*.2))
    groups={'train':fields[2*n:], 'tuning':fields[n:2*n], 'holdout':fields[:n]}
    datasets={k:df[df.anon_polygon_id.isin(v)].reset_index(drop=True) for k,v in groups.items()}
    print('Preparing training masks and leakage-safe features...',flush=True)
    x,y,_=samples(datasets['train'],[(seed,'random'),(seed+1,'random'),(seed+2,'block')])
    xv,yv,mv=samples(datasets['tuning'],[(seed+100,'random'),(seed+101,'block')])
    params=dict(iterations=iterations,depth=6,learning_rate=.04,loss_function='RMSE',
                random_seed=seed,thread_count=4,l2_leaf_reg=8,verbose=False,allow_writing_files=False)
    model=CatBoostRegressor(**params)
    print(f'Training residual model: {len(x)} samples, {len(x.columns)} features',flush=True)
    model.fit(x,y-x.baseline.to_numpy(),cat_features=['crop_type'],
              eval_set=(xv,yv-xv.baseline.to_numpy()),early_stopping_rounds=70)
    mlv=xv.baseline.to_numpy()+model.predict(xv)
    choices=[]
    methods=['baseline','neighbor_mean','pchip','seasonal','smooth_12']
    for method in methods:
        for alpha in [0,.25,.5,.75,1.0]:
            prediction=(1-alpha)*xv[method].to_numpy()+alpha*mlv
            choices.append({'method':method,'alpha':alpha,**metric(yv,prediction)})
    choice=min(choices,key=lambda r:r['rmse'])
    print('Selected using tuning fields only:',choice,flush=True)
    # The holdout is evaluated only after model/ensemble selection.
    xt,yt,mt=samples(datasets['holdout'],[(seed+200,'random'),(seed+201,'block')])
    ml=xt.baseline.to_numpy()+model.predict(xt)
    predictions={method:xt[method].to_numpy() for method in methods}
    predictions['catboost']=ml
    predictions['selected']=(1-choice['alpha'])*xt[choice['method']].to_numpy()+choice['alpha']*ml
    scores={name:{'all':metric(yt,p),**{mode:metric(yt[mt==mode],p[mt==mode]) for mode in ['random','block']}} for name,p in predictions.items()}
    segments={}
    for name,mask in {'edge':xt.edge.to_numpy()==1,'interior':xt.edge.to_numpy()==0,
                      'gap_over_20_days':xt.gap_days.to_numpy()>20,
                      'missing_weather':xt.era5_temp_c_linear.isna().to_numpy()}.items():
        if mask.any(): segments[name]=metric(yt[mask],predictions['selected'][mask])
    used_iterations=max(1,model.tree_count_)
    print('Holdout:',json.dumps(scores['selected']),flush=True)
    print('Refitting final model on all fields...',flush=True)
    xf,yf,_=samples(df,[(seed,'random'),(seed+1,'random'),(seed+2,'block')])
    params['iterations']=used_iterations
    final=CatBoostRegressor(**params)
    final.fit(xf,yf-xf.baseline.to_numpy(),cat_features=['crop_type'])
    final.save_model(str(out/'model.cbm'))
    config={'version':1,'method':choice['method'],'alpha':choice['alpha'],'features':list(x.columns),
            'seed':seed,'iterations':used_iterations,'context':'retrospective; both past and future visible observations'}
    (out/'config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    report={'input_sha256':hashlib.sha256(Path(input_file).read_bytes()).hexdigest(),
            'rows':len(df),'known_targets':int(df.primary_ndvi.notna().sum()),
            'split':{k:list(v) for k,v in groups.items()},'mask_seed':seed,
            'tuning_candidates':choices,'selected':choice,'holdout':scores,'holdout_segments':segments,
            'parameters':params,'features':list(x.columns),
            'protocol':'20% random masks and 30-calendar-day blocks; disjoint fields; all query dynamic columns hidden before features; supplied climatology/status/zscore unused. Holdout metrics belong to pre-refit model.'}
    (out/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved model and metrics to',out,flush=True)
    return report
