import pandas as pd
import numpy as np
from app.timeseries import analyze
from app.anomalies import anomaly_episodes


def fixture(temperature=30,ndvi=.6):
    rows=[]
    for year in [2022,2023]:
        for day in range(1,11):
            rows.append(dict(anon_polygon_id='a',date=f'{year}-06-{day:02d}',primary_ndvi=.6,
                             era5_temp_c=20+(day%2),era5_precip_mm=1))
    for day in range(1,5):
        rows.append(dict(anon_polygon_id='a',date=f'2024-06-{day:02d}',primary_ndvi=ndvi,
                         era5_temp_c=temperature,era5_precip_mm=0))
    return pd.DataFrame(rows)


def run(frame):
    return analyze(frame,'2024-06-01','2024-06-04','nonexistent-model-directory')


def test_heat_detected_with_normal_ndvi():
    r=run(fixture())
    assert [e['type'] for e in r['episodes']]==['heat']
    assert r['points'][0]['temperature_normal']==20.5
    assert r['points'][0]['temperature_zscore']==4.75
    assert not r['points'][0]['combined_anomaly']


def test_cold_and_combined_vegetation_anomaly():
    r=run(fixture(temperature=5,ndvi=.3))
    assert {e['type'] for e in r['episodes']}=={'cold','vegetation'}
    assert all(e['combined_days']==4 for e in r['episodes'])
    assert all(p['combined_anomaly'] for p in r['points'])


def test_no_temperature_normal_means_no_temperature_diagnosis():
    frame=fixture(); frame.loc[frame.date.str.startswith('202'),'era5_temp_c']=np.nan
    r=run(frame)
    assert not r['episodes']
    assert all(p['temperature_zscore'] is None for p in r['points'])


def test_missing_day_breaks_temperature_episode():
    frame=fixture();frame.loc[frame.date=='2024-06-02','era5_temp_c']=np.nan
    assert not run(frame)['episodes']


def test_temperature_detection_without_satellite_data():
    frame=fixture(); frame['primary_ndvi']=np.nan
    r=run(frame)
    assert r['episodes'][0]['type']=='heat'
    assert r['observed_count']==0
    assert all(p['restored'] is None for p in r['points'])


def test_nonconsecutive_dates_do_not_count_as_three_consecutive_days():
    rows=run(fixture())['points'][::2]
    assert not anomaly_episodes(rows)


def test_hot_current_days_do_not_change_historical_normal():
    a=run(fixture(temperature=25)); b=run(fixture(temperature=40))
    assert a['points'][0]['temperature_normal']==b['points'][0]['temperature_normal']
