from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

SENSORS = ['s2_ndvi', 'landsat_ndvi', 'modis_ndvi']
AUX = ['s2_evi', 's2_ndwi', 'landsat_evi', 'landsat_ndwi', 'modis_evi', 'era5_temp_c', 'era5_precip_mm']
DYNAMIC = ['primary_ndvi', *SENSORS, *AUX, 'ndvi_climatology_mean', 'ndvi_climatology_std', 'ndvi_zscore', 'status', 'n_reference_years']


def prepare(frame):
    df = frame.copy().reset_index(drop=True)
    required = {'anon_polygon_id', 'date', 'primary_ndvi'}
    if not required.issubset(df):
        raise ValueError(f'Missing columns: {sorted(required - set(df))}')
    if df['anon_polygon_id'].isna().any() or df['date'].isna().any():
        raise ValueError('Field IDs and dates must not be empty')
    df['date'] = pd.to_datetime(df['date'], format='%Y-%m-%d', errors='raise')
    if df.duplicated(['anon_polygon_id', 'date']).any():
        raise ValueError('Duplicate field/date keys')
    df['anon_polygon_id'] = df.anon_polygon_id.astype(str)
    df['crop_type'] = df.get('crop_type', pd.Series('unknown', index=df.index)).fillna('unknown').astype(str)
    df['year'] = df.date.dt.year
    df['doy'] = df.date.dt.dayofyear
    for col in ['primary_ndvi', *SENSORS, *AUX]:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    return df


def control_mask(frame):
    if 'is_synthetic_gap' not in frame:
        raise ValueError('Missing is_synthetic_gap column')
    values = frame.is_synthetic_gap.astype(str).str.strip().str.lower()
    if not values.isin(['true', 'false', '1', '0']).all():
        raise ValueError('is_synthetic_gap must contain True/False or 1/0')
    return values.isin(['true', '1']).to_numpy()


def conceal(df, indices):
    context = df.copy()
    for col in DYNAMIC:
        if col in context:
            if col == 'status':
                context[col] = context[col].astype(object)
            context.loc[indices, col] = np.nan
    return context


def clean_context(df):
    context = df.copy()
    for col in ['primary_ndvi', *SENSORS]:
        context.loc[~context[col].between(-1, 1), col] = np.nan
    for col in AUX:
        if col.endswith('evi') or col.endswith('ndwi'):
            context.loc[context[col].abs() > 3, col] = np.nan
    context['era5_precip_mm'] = context.era5_precip_mm.clip(lower=0)
    return context


def _neighbors(t, values, target_t, prefix, count=2):
    finite = np.isfinite(values)
    t, values = t[finite], values[finite]
    output = {}
    for side in ['left', 'right']:
        for k in range(count):
            output[f'{prefix}_{side}{k}'] = np.full(len(target_t), np.nan)
            output[f'{prefix}_{side}{k}_days'] = np.full(len(target_t), np.nan)
    output[f'{prefix}_linear'] = np.full(len(target_t), np.nan)
    if not len(t):
        return output
    insertion = np.searchsorted(t, target_t)
    for side in ['left', 'right']:
        for k in range(count):
            index = insertion - 1 - k if side == 'left' else insertion + k
            valid = (index >= 0) & (index < len(t))
            output[f'{prefix}_{side}{k}'][valid] = values[index[valid]]
            output[f'{prefix}_{side}{k}_days'][valid] = np.abs(t[index[valid]] - target_t[valid])
    output[f'{prefix}_linear'] = np.interp(target_t, t, values)
    return output


def features(frame, indices):
    """Hide ALL query values first. No provided climatology/status/zscore is read."""
    df = prepare(frame)
    indices = np.asarray(indices, dtype=int)
    if len(np.unique(indices)) != len(indices):
        raise ValueError('Duplicate query indices')
    context = clean_context(conceal(df, indices))
    rows = []
    for (field, year), target in df.loc[indices].groupby(['anon_polygon_id', 'year'], sort=False):
        all_field = context[context.anon_polygon_id == field]
        season = all_field[all_field.year == year].sort_values('date')
        tt = target.doy.to_numpy(dtype=float)
        x = pd.DataFrame(index=target.index)
        x['crop_type'] = target.crop_type
        x['doy'] = tt
        x['year'] = year
        x['doy_sin'] = np.sin(2*np.pi*tt/365.25)
        x['doy_cos'] = np.cos(2*np.pi*tt/365.25)
        times = season.doy.to_numpy(dtype=float)
        values = season.primary_ndvi.to_numpy(dtype=float)
        for key, value in _neighbors(times, values, tt, 'ndvi', 3).items():
            x[key] = value
        finite = np.isfinite(values)
        t, y = times[finite], values[finite]
        x['season_count'] = len(y)
        x['season_median'] = np.median(y) if len(y) else np.nan
        x['season_std'] = np.std(y) if len(y) else np.nan
        for width in [5, 12, 25]:
            weights = np.exp(-.5*((tt[:, None]-t[None, :])/width)**2)
            denom = weights.sum(axis=1)
            x[f'smooth_{width}'] = np.divide(weights @ y, denom, out=np.full(len(tt),np.nan), where=denom>1e-8)
        x['pchip'] = PchipInterpolator(t, y, extrapolate=False)(tt) if len(y)>1 else np.nan
        x['neighbor_mean'] = x[['ndvi_left0','ndvi_right0']].mean(axis=1)
        x['gap_days'] = x.ndvi_left0_days + x.ndvi_right0_days
        x['edge'] = (x.ndvi_left0.isna() | x.ndvi_right0.isna()).astype(int)
        for col in SENSORS + AUX:
            for key, value in _neighbors(times, season[col].to_numpy(dtype=float), tt, col, 1).items():
                x[key] = value
        history = all_field[(all_field.year != year) & all_field.primary_ndvi.notna()]
        ht = history.doy.to_numpy(dtype=float)
        hy = history.primary_ndvi.to_numpy(dtype=float)
        weights = np.exp(-.5*((tt[:, None]-ht[None, :])/12)**2)
        denom = weights.sum(axis=1)
        mean = np.divide(weights @ hy, denom, out=np.full(len(tt),np.nan), where=denom>1e-8)
        variance = np.divide(weights @ (hy**2), denom, out=np.full(len(tt),np.nan), where=denom>1e-8)-mean**2
        x['historical_mean'] = mean
        x['historical_std'] = np.sqrt(np.maximum(variance,0))
        x['historical_years'] = history.year.nunique()
        x['baseline'] = x.ndvi_linear.fillna(x.historical_mean).fillna(.35)
        x['pchip'] = x.pchip.fillna(x.baseline)
        x['neighbor_mean'] = x.neighbor_mean.fillna(x.baseline)
        x['seasonal'] = x.historical_mean.fillna(x.baseline)
        x['smooth_12'] = x.smooth_12.fillna(x.baseline)
        rows.append(x)
    if not rows:
        return pd.DataFrame(index=indices)
    return pd.concat(rows).loc[indices].replace([np.inf,-np.inf],np.nan)


class Reconstructor:
    def __init__(self, model_dir=None):
        self.model = None
        self.components = []
        self.config = {'method':'baseline', 'alpha':0.0}
        if model_dir is not None:
            model_dir = Path(model_dir)
            self.config = json.loads((model_dir/'config.json').read_text(encoding='utf-8'))
            if self.config.get('components'):
                from catboost import CatBoostClassifier, CatBoostRegressor
                for spec in self.config['components']:
                    if spec['kind']=='extra':
                        import joblib
                        self.components.append((spec,joblib.load(model_dir/spec['file'])))
                    elif spec['kind']=='regressor':
                        estimator=CatBoostRegressor(); estimator.load_model(str(model_dir/spec['file']))
                        self.components.append((spec,estimator))
                    else:
                        classifier=CatBoostClassifier();classifier.load_model(str(model_dir/spec['classifier']))
                        experts=[]
                        for file in spec['experts']:
                            estimator=CatBoostRegressor();estimator.load_model(str(model_dir/file));experts.append(estimator)
                        self.components.append((spec,(classifier,experts)))
            elif self.config['alpha']:
                from catboost import CatBoostRegressor
                self.model = CatBoostRegressor()
                self.model.load_model(str(model_dir/'model.cbm'))

    def build_features(self,frame,indices):
        feature_set=self.config.get('feature_set','v1')
        if feature_set=='v1':return features(frame,indices)
        from gapfill.advanced import features_v2
        x=features_v2(frame,indices)
        if feature_set=='peers':
            from gapfill.peers import peer_features
            x=pd.concat([x,peer_features(frame,indices)],axis=1)
        return x

    def predict_features(self, x):
        if not len(x):
            return np.array([])
        if self.components:
            result=np.zeros(len(x))
            for spec,estimator in self.components:
                subset=x[spec['features']]
                if spec['kind'] in ('regressor','extra'):
                    prediction=estimator.predict(subset.fillna(-999) if spec['kind']=='extra' else subset)
                    if spec['residual']:prediction=prediction+x.baseline.to_numpy()
                else:
                    classifier,experts=estimator
                    probabilities=classifier.predict_proba(subset)
                    predictions=[]
                    for i,col in enumerate(SENSORS):
                        baseline=x[f'{col}_linear'].fillna(x.baseline).to_numpy()
                        predictions.append(experts[i].predict(subset)+baseline)
                    prediction=(np.array(predictions).T*probabilities).sum(axis=1)
                result+=spec['weight']*prediction
            return result
        baseline = x[self.config['method']].to_numpy(dtype=float)
        if self.model is None:
            return baseline
        ml = x.baseline.to_numpy() + self.model.predict(x[self.config['features']])
        return (1-self.config['alpha'])*baseline + self.config['alpha']*ml

    def predict(self, frame, indices):
        x = self.build_features(frame, indices)
        return self.predict_features(x)


def submission(frame, reconstructor):
    mask = control_mask(frame)
    df = prepare(frame)
    indices = np.flatnonzero(mask)
    result = df.loc[indices, ['anon_polygon_id','date']].copy()
    result['date'] = result.date.dt.strftime('%Y-%m-%d')
    result['primary_ndvi_pred'] = reconstructor.predict(df, indices)
    if not np.isfinite(result.primary_ndvi_pred.to_numpy()).all():
        raise ValueError('Nonfinite submission prediction')
    if len(result) != int(mask.sum()) or result.duplicated(['anon_polygon_id','date']).any():
        raise ValueError('Submission keys do not match control rows')
    return result
