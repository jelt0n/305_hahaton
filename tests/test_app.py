from datetime import date
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app, _cache
from app.osm import farmland_geojson

client = TestClient(app)
GEOMETRY = {'type': 'Polygon', 'coordinates': [[[38.9,45],[38.91,45],[38.91,45.01],[38.9,45.01],[38.9,45]]]}
BODY = {'geometry': GEOMETRY, 'start': '2025-06-01', 'end': '2025-06-30', 'cloud': 60}


def points(coords):
    return [{'lon': x, 'lat': y} for x, y in coords]


def test_osm_relation_with_fragmented_outer_and_hole():
    members = [{'type':'way','ref':1,'role':'outer','geometry':points([(0,0),(4,0),(4,4)])},
               {'type':'way','ref':2,'role':'outer','geometry':points([(4,4),(0,4),(0,0)])},
               {'type':'way','ref':3,'role':'inner','geometry':points([(1,1),(2,1),(2,2),(1,2),(1,1)])}]
    data = {'elements':[{'type':'way','id':1,'tags':{'landuse':'farmland'},'geometry':points([(0,0),(4,0),(4,4),(0,0)])},
                        {'type':'relation','id':42,'tags':{'landuse':'farmland'},'members':members}]}
    result = farmland_geojson(data)
    assert len(result['features']) == 1
    assert result['features'][0]['id'] == 'relation/42'
    assert len(result['features'][0]['geometry']['coordinates']) == 2


def test_open_way_is_not_fabricated_into_polygon():
    assert not farmland_geojson({'elements':[{'type':'way','id':1,'tags':{'landuse':'farmland'},
                                              'geometry':points([(0,0),(1,0),(1,1),(0,1)])}]})['features']


def test_home_and_bad_bbox():
    assert client.get('/').status_code == 200
    for bbox in ['bad', 'nan,0,1,1', '0,0,10,10', '45,39,44,38']:
        assert client.get('/api/fields', params={'bbox':bbox}).status_code == 422


def test_overpass_failure_is_visible():
    import httpx
    _cache.clear()
    with patch('app.main.httpx.post', side_effect=httpx.ConnectError('offline')):
        assert client.get('/api/fields?bbox=45,38,45.01,38.01').status_code == 502


def test_invalid_dates_and_geometry():
    for change in [{'start':'2025-07-01'}, {'start':'2016-01-01'}, {'end':'2099-01-01'},
                   {'cloud':101}, {'geometry':{'type':'Point','coordinates':[38,45]}},
                   {'geometry':{'type':'Polygon','coordinates':[[[0,0],[1,1],[1,0],[0,1],[0,0]]]}}]:
        assert client.post('/api/ndvi', json=BODY | change).status_code == 422


def test_ndvi_inclusive_end_date():
    expected = {'mean':0.6,'min':0.2,'max':0.8,'tile_url':'https://example.test/{z}/{x}/{y}'}
    with patch('app.main.earth_engine.calculate', return_value=expected) as calculate:
        response = client.post('/api/ndvi',json=BODY)
        assert response.status_code == 200
        assert response.json() == expected
        calculate.assert_called_once_with(GEOMETRY, '2025-06-01', '2025-07-01', 60)


def test_no_pixels_and_missing_credentials():
    from app.earth_engine import EarthEngineUnavailable
    with patch('app.main.earth_engine.calculate',return_value=None):
        assert client.post('/api/ndvi',json=BODY).status_code == 404
    with patch('app.main.earth_engine.calculate',side_effect=EarthEngineUnavailable('Configure EE')):
        assert client.post('/api/ndvi',json=BODY).status_code == 503


def test_unexpected_ee_error_does_not_expose_credentials():
    with patch('app.main.earth_engine.calculate',side_effect=RuntimeError('secret-token')):
        response = client.post('/api/ndvi',json=BODY)
        assert response.status_code == 502
        assert 'secret-token' not in response.text
