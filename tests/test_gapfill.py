import numpy as np
import pandas as pd
import pytest
from gapfill.core import features, prepare, Reconstructor, submission, control_mask
from gapfill.train import masks
from app.timeseries import analyze


def sample():
    return pd.DataFrame({'anon_polygon_id':['a']*5,'date':['2024-06-01','2024-06-03','2024-06-05','2024-06-07','2024-06-09'],
                         'primary_ndvi':[.2,.3,.4,.5,.6],'crop_type':['wheat']*5})


def test_target_and_derived_columns_cannot_leak():
    a=sample(); a['s2_ndvi']=a.primary_ndvi
    b=a.copy()
    b.loc[2,'primary_ndvi']=.95; b.loc[2,'s2_ndvi']=.95
    b['ndvi_zscore']=999; b['status']='hidden answer'
    b['ndvi_climatology_mean']=999
    pd.testing.assert_frame_equal(features(a,[2]),features(b,[2]))
    assert features(a,[2]).baseline.iloc[0] == pytest.approx(.4)


def test_entire_query_block_is_hidden_together():
    a=sample(); b=a.copy(); b.loc[[1,2,3],'primary_ndvi']=[9,8,7]
    pd.testing.assert_frame_equal(features(a,[1,2,3]),features(b,[1,2,3]))


def test_no_interpolation_across_winter_and_no_data_fallback():
    a=sample(); a['date']=['2023-10-01','2023-10-03','2024-04-05','2024-04-07','2024-04-09']
    x=features(a,[2]); assert np.isnan(x.ndvi_left0.iloc[0])
    assert x.edge.iloc[0]==1
    empty=sample(); empty['primary_ndvi']=np.nan
    assert np.isfinite(Reconstructor().predict(empty,[2])).all()


def test_submission_exact_keys_and_input_unchanged():
    a=sample(); a['is_synthetic_gap']=['False','True','False','1','0']
    before=a.copy(deep=True)
    result=submission(a,Reconstructor())
    assert list(result.columns)==['anon_polygon_id','date','primary_ndvi_pred']
    assert result.date.tolist()==['2024-06-03','2024-06-07']
    assert result.primary_ndvi_pred.tolist()==pytest.approx([.3,.5])
    pd.testing.assert_frame_equal(a,before)
    with pytest.raises(ValueError): control_mask(a.assign(is_synthetic_gap='maybe'))
    with pytest.raises(ValueError): prepare(pd.concat([a,a]))


def test_empty_submission():
    result=submission(sample().assign(is_synthetic_gap=False),Reconstructor())
    assert result.empty and len(result.columns)==3


def test_masks_only_select_observed_values():
    df=prepare(sample()); df.loc[0,'primary_ndvi']=np.nan
    for mode in ['random','block']:
        ids=masks(df,42,mode)
        assert df.loc[ids,'primary_ndvi'].notna().all()


def test_web_preserves_observations_and_marks_restoration():
    a=sample(); a.loc[2,'primary_ndvi']=np.nan
    result=analyze(a,'2024-06-01','2024-06-09','nonexistent-model-directory')
    assert result['observed_count']==4
    assert result['reconstructed_count']==1
    assert result['points'][2]['restored']==pytest.approx(.4)
    assert result['points'][0]['restored']==.2
    assert result['points'][2]['is_reconstructed']
    assert all(p['zscore'] is None for p in result['points'])


def test_trained_model_loads_and_predicts():
    from pathlib import Path
    if not Path('models/ndvi/config.json').exists(): pytest.skip('Train artifacts not installed')
    result=submission(sample().assign(is_synthetic_gap=[False,True,False,False,False]),Reconstructor('models/ndvi'))
    assert len(result)==1 and np.isfinite(result.primary_ndvi_pred.iloc[0])
