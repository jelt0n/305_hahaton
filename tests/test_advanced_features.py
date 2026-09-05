import numpy as np
import pandas as pd
from gapfill.advanced import features_v2
from gapfill.peers import peer_features


def fixture():
    rows=[]
    for field in ['a','b']:
        for day in range(1,20):
            value=.2+day*.02
            rows.append({'anon_polygon_id':field,'date':f'2024-06-{day:02d}',
                         'primary_ndvi':value,'s2_ndvi':value,'landsat_ndvi':value*.9})
    return pd.DataFrame(rows)


def test_v2_hides_all_target_values_before_smoothing_and_peer_features():
    a=fixture();b=a.copy()
    ids=[4,5,23,24]
    b.loc[ids,['primary_ndvi','s2_ndvi','landsat_ndvi']]=.99
    b['ndvi_climatology_mean']=999
    pd.testing.assert_frame_equal(features_v2(a,ids),features_v2(b,ids))


def test_v2_temporal_features_are_finite_where_observed_context_exists():
    x=features_v2(fixture(),[4,5])
    assert np.isfinite(x['v2_primary_ndvi_local_7']).all()
    assert x['v2_primary_ndvi_local_7'].tolist()==[.3,.32]
    assert x['v2_peer_s2_ndvi_presence'].tolist()==[1.,1.]


def test_peer_values_do_not_use_hidden_targets():
    a=fixture();b=a.copy();ids=[4,5,23,24]
    b.loc[ids,['primary_ndvi','s2_ndvi','landsat_ndvi']]=.99
    pd.testing.assert_frame_equal(peer_features(a,ids),peer_features(b,ids))
