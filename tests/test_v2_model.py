from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from gapfill.core import Reconstructor,submission


def test_v2_saved_ensemble_inference():
    if not Path('models/ndvi_v2/config.json').exists():pytest.skip('V2 artifacts not installed')
    df=pd.DataFrame({'anon_polygon_id':['a']*9,'date':pd.date_range('2024-06-01',periods=9).strftime('%Y-%m-%d'),
                     'primary_ndvi':[.2,.22,.24,np.nan,.28,.30,.32,.34,.36],
                     'is_synthetic_gap':[False,False,False,True,False,False,False,False,False]})
    predictor=Reconstructor('models/ndvi_v2')
    assert len(predictor.components)==2
    result=submission(df,predictor)
    assert result.shape==(1,3)
    assert np.isfinite(result.primary_ndvi_pred).all()
