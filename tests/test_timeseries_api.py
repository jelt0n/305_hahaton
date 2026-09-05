from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from tests.test_app import BODY

client=TestClient(app)


def test_series_validation():
    assert client.post('/api/timeseries',json=BODY|{'source':'invalid'}).status_code==422
    assert client.post('/api/timeseries',json=BODY|{'end':'2025-12-01'}).status_code==422


def test_series_route_returns_analysis_and_partial_source_warning():
    with patch('app.timeseries.collect',return_value=('frame',['MODIS unavailable'])), patch('app.timeseries.analyze',return_value={'points':[]}):
        response=client.post('/api/timeseries',json=BODY)
        assert response.status_code==200
        assert response.json()['warnings']==['MODIS unavailable']
