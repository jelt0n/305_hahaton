import numpy as np
import pandas as pd


def temperature_signals(rows, history):
    for row in rows:
        doy=pd.Timestamp(row['date']).dayofyear
        past=history[(history.doy-doy).abs()<=15].dropna(subset=['era5_temp_c'])
        years=past.year.nunique()
        normal=float(past.era5_temp_c.mean()) if years>=2 and len(past)>=10 else None
        std=float(past.era5_temp_c.std()) if normal is not None else None
        temperature=row['temperature']
        z=(temperature-normal)/max(std,2.0) if temperature is not None and normal is not None else None
        kind='heat' if z is not None and z>=2 else 'cold' if z is not None and z<=-2 else None
        row.update(temperature_normal=normal,temperature_std=std,temperature_zscore=z,
                   temperature_reference_years=int(years),temperature_anomaly=kind,
                   temperature_status=('Нет данных температуры' if temperature is None else
                                       'Нет температурной нормы' if z is None else
                                       'Необычно высокая температура' if kind=='heat' else
                                       'Необычно низкая температура' if kind=='cold' else 'Температура в пределах нормы'))
        vegetation=row['zscore'] is not None and row['zscore'] < -1
        row['anomaly_types']=(['vegetation'] if vegetation else [])+([kind] if kind else [])
        row['combined_anomaly']=bool(vegetation and kind)


def anomaly_episodes(rows):
    episodes=[]
    for kind in ['vegetation','heat','cold']:
        active=[]
        def finish():
            if len(active)<3: return
            observed=sum(p['observed'] is not None for p in active)
            overlap=sum(p['combined_anomaly'] for p in active)
            if kind=='vegetation':
                title='Угнетение растительности'
                explanation='NDVI ниже сезонной нормы не менее трёх дней подряд.'
                if overlap:
                    title+=' с температурным отклонением'
                    explanation+=f' В {overlap} днях одновременно отмечено температурное отклонение. Совпадение не доказывает причинность.'
                if not observed:
                    explanation+=' Сигнал NDVI основан только на восстановлении и требует проверки по снимкам.'
                rain=[p['precipitation'] for p in active if p['precipitation'] is not None]
                if len(rain)==len(active) and len(rain)>=7 and sum(rain)<2:
                    explanation+=' За период почти не было осадков; возможен дефицит влаги.'
            else:
                title='Необычно высокая температура' if kind=='heat' else 'Необычно низкая температура'
                direction='выше' if kind=='heat' else 'ниже'
                explanation=f'Среднесуточная температура {direction} сезонной нормы минимум на два стандартных отклонения не менее трёх дней подряд (минимальный масштаб разброса 2 °C).'
                if overlap:
                    explanation+=f' В {overlap} днях это совпало со снижением NDVI. Возможный температурный стресс требует проверки.'
                else:
                    explanation+=' Совпадающее снижение NDVI не установлено; это температурный сигнал, а не подтверждённое повреждение растений.'
            zs=[p['zscore'] for p in active if p['zscore'] is not None]
            tzs=[p['temperature_zscore'] for p in active if p['temperature_zscore'] is not None]
            episodes.append({'type':kind,'title':title,'start':active[0]['date'],'end':active[-1]['date'],
                             'days':len(active),'observed_points':observed,'combined_days':overlap,
                             'min_zscore':min(zs) if zs else None,
                             'temperature_zscore_extreme':max(tzs,key=abs) if tzs else None,'explanation':explanation})
        for row in rows:
            if active and (pd.Timestamp(row['date'])-pd.Timestamp(active[-1]['date'])).days!=1:
                finish(); active=[]
            if kind in row['anomaly_types']:
                active.append(row)
            else:
                finish(); active=[]
        finish()
    return sorted(episodes,key=lambda p:(p['start'],p['type']))
