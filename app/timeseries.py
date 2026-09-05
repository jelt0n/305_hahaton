from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
import ee
from app.earth_engine import initialize, _ndvi
from gapfill.core import prepare, Reconstructor, features
from app.anomalies import temperature_signals, anomaly_episodes


def _landsat(image):
    qa=image.select('QA_PIXEL')
    mask=qa.bitwiseAnd(63).eq(0).And(image.select('QA_RADSAT').eq(0))
    red=image.select('SR_B4').multiply(.0000275).add(-.2)
    nir=image.select('SR_B5').multiply(.0000275).add(-.2)
    ndvi=nir.subtract(red).divide(nir.add(red)).rename('NDVI')
    return ndvi.updateMask(mask.And(nir.add(red).gt(0)).And(ndvi.gte(-1)).And(ndvi.lte(1))).copyProperties(image,['system:time_start'])


def _modis(image):
    return image.select('NDVI').multiply(.0001).updateMask(image.select('SummaryQA').eq(0)).copyProperties(image,['system:time_start'])


def _extract(collection, region, scale, names):
    def reduce(item):
        image=ee.Image(item)
        stats=image.reduceRegion(reducer=ee.Reducer.mean(),geometry=region,scale=scale,
                                 maxPixels=2000000,tileScale=4)
        return ee.Feature(None,stats).set('date',image.date().format('YYYY-MM-dd'))
    info=ee.FeatureCollection(collection.toList(collection.size()).map(reduce)).getInfo()
    rows=[]
    for feature in info['features']:
        p=feature['properties']
        rows.append({'date':p['date'],**{target:p.get(source) for source,target in names.items()}})
    return rows


def collect(geometry, start, end, cloud=60, source='all', history_years=3):
    initialize()
    region=ee.Geometry(geometry)
    first=pd.Timestamp(start); last=pd.Timestamp(end)
    windows=[]
    for years in range(history_years+1):
        a=first-pd.DateOffset(years=years)-pd.Timedelta(days=20)
        b=last-pd.DateOffset(years=years)+pd.Timedelta(days=21)
        windows.append(ee.Filter.date(a.strftime('%Y-%m-%d'),b.strftime('%Y-%m-%d')))
    period_filter=ee.Filter.Or(*windows)
    s2=(ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(region).filter(period_filter)
        .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE',cloud)).map(_ndvi))
    jobs={'s2':(s2,10,{'NDVI':'s2_ndvi'})}
    if source=='all':
        landsat=(ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
                 .filterBounds(region).filter(period_filter).filter(ee.Filter.lte('CLOUD_COVER',cloud)).map(_landsat))
        modis=ee.ImageCollection('MODIS/061/MOD13Q1').filterBounds(region).filter(period_filter).map(_modis)
        jobs.update(landsat=(landsat,30,{'NDVI':'landsat_ndvi'}),modis=(modis,250,{'NDVI':'modis_ndvi'}))
    def weather_image(image):
        return (image.select('temperature_2m').subtract(273.15).rename('temp')
                .addBands(image.select('total_precipitation_sum').multiply(1000).max(0).rename('rain'))
                .copyProperties(image,['system:time_start']))
    weather=(ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_AGGR')
             .filter(period_filter)
             .map(weather_image))
    jobs['weather']=(weather,11132,{'temp':'era5_temp_c','rain':'era5_precip_mm'})
    frames=[]; warnings=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={name:pool.submit(_extract,col,region,scale,names) for name,(col,scale,names) in jobs.items()}
        for name,future in futures.items():
            try:
                rows=future.result()
                if rows:
                    frames.append(pd.DataFrame(rows).groupby('date').mean(numeric_only=False))
            except Exception:
                warnings.append(f'Источник {name} недоступен; его данные не использованы.')
    if not frames:
        raise ValueError('Источники временного ряда недоступны. Повторите запрос позже.')
    df=pd.concat(frames,axis=1)
    df.index=pd.to_datetime(df.index)
    df=df.reindex(df.index.union(pd.date_range(first,last))).sort_index()
    for col in ['s2_ndvi','landsat_ndvi','modis_ndvi']:
        if col not in df: df[col]=np.nan
    df['primary_ndvi']=df.s2_ndvi.combine_first(df.landsat_ndvi).combine_first(df.modis_ndvi)
    df['source']=np.select([df.s2_ndvi.notna(),df.landsat_ndvi.notna(),df.modis_ndvi.notna()],['Sentinel-2','Landsat','MODIS'],default='')
    df['anon_polygon_id']='selected-field'
    df['crop_type']='unknown'
    df.index.name='date'
    if not df.loc[first:last,'primary_ndvi'].notna().any():
        if 'era5_temp_c' not in df or not df.loc[first:last,'era5_temp_c'].notna().any():
            raise ValueError('За период нет пригодных спутниковых и температурных данных. Расширьте период.')
        warnings.append('Нет спутниковых наблюдений за период: доступны температурные сигналы; состояние растительности не подтверждено.')
    return df.reset_index(),warnings


def analyze(frame, start, end, model_dir):
    df=prepare(frame)
    mask=df.date.between(pd.Timestamp(start),pd.Timestamp(end))
    ids=df.index[mask].to_numpy()
    gaps=df.index[mask & df.primary_ndvi.isna()].to_numpy()
    model_path=Path(model_dir)
    reconstructor=Reconstructor(model_path if (model_path/'config.json').exists() else None)
    restored=df.primary_ndvi.copy()
    support={}
    if len(gaps):
        x=reconstructor.build_features(df,gaps)
        prediction=reconstructor.predict_features(x)
        distances=x[['ndvi_left0_days','ndvi_right0_days']].min(axis=1)
        reliable=distances.le(30)
        restored.loc[gaps]=np.where(reliable,prediction,np.nan)
        support={int(i):bool(v) for i,v in zip(gaps,reliable)}
    history=df[df.date < pd.Timestamp(start).replace(month=1,day=1)]
    rows=[]
    for i in ids:
        row=df.loc[i]; doy=row.doy
        historical=history[(history.doy-doy).abs()<=15].dropna(subset=['primary_ndvi'])
        historical=historical[historical.primary_ndvi.between(-1,1)]
        n_years=historical.year.nunique()
        normal=float(historical.primary_ndvi.mean()) if n_years>=2 and len(historical)>=5 else None
        std=float(historical.primary_ndvi.std()) if normal is not None else None
        value=restored.loc[i]
        observed=pd.notna(row.primary_ndvi)
        z=(float(value)-normal)/max(std,.05) if pd.notna(value) and normal is not None and std is not None and np.isfinite(std) else None
        status='Нет исторической нормы' if z is None else ('Критическая аномалия' if z < -2 else 'Угнетение биомассы' if z < -1 else 'Штатное развитие')
        if pd.isna(value): status='Недостаточно наблюдений'
        def number(v): return float(v) if pd.notna(v) and np.isfinite(v) else None
        rows.append({'date':row.date.strftime('%Y-%m-%d'),'observed':number(row.primary_ndvi),
                     'restored':number(value),'is_reconstructed':not observed and pd.notna(value),
                     'source':frame.loc[i,'source'] if 'source' in frame else '',
                     'normal':normal,'normal_std':std,'reference_years':int(n_years),'zscore':z,'status':status,
                     'temperature':number(row.era5_temp_c),'precipitation':number(row.era5_precip_mm)})
    temperature_signals(rows,history)
    episodes=anomaly_episodes(rows)
    return {'points':rows,'episodes':episodes,'model':reconstructor.config['method'],
            'ml_weight':reconstructor.config['alpha'],'observed_count':sum(p['observed'] is not None for p in rows),
            'reconstructed_count':sum(p['is_reconstructed'] for p in rows),
            'note':'Сигналы: NDVI z < −1; температура z ≥ 2 или ≤ −2; длительность от трёх дней. Температурная норма: предыдущие годы, ±15 дней, минимум 2 года и 10 наблюдений; масштаб разброса не меньше 2 °C. Температурное отклонение не доказывает повреждение растений. ERA5-Land — региональный реанализ. Восстановление NDVI ретроспективное. MODIS: 16-дневный композит 250 м. Приоритет: Sentinel-2 → Landsat → MODIS.'}
