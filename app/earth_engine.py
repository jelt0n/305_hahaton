import os
from threading import Lock
import ee

PALETTE = ['9e3450', 'd88452', 'e8ce79', 'b5cf76', '66a867', '28724e', '124631']
_lock = Lock()
_initialized = False


class EarthEngineUnavailable(Exception):
    pass


def initialize():
    global _initialized
    with _lock:
        if _initialized:
            return
        project = os.getenv('EE_PROJECT')
        if not project:
            raise EarthEngineUnavailable('Укажите EE_PROJECT в .env и выполните earthengine authenticate. Затем перезапустите сервер.')
        try:
            key = os.getenv('EE_SERVICE_ACCOUNT_FILE')
            if key:
                credentials = ee.ServiceAccountCredentials(email=None, key_file=key)
                ee.Initialize(credentials=credentials, project=project)
            else:
                # Omitting credentials loads the saved OAuth login. None disables it.
                ee.Initialize(project=project)
            ee.data.setDeadline(90000)
            _initialized = True
        except Exception as exc:
            detail = str(exc).lower()
            if 'not found or deleted' in detail:
                raise EarthEngineUnavailable('Google Cloud проект из EE_PROJECT не найден или удалён. Укажите реальный Project ID в .env: my-farmland-project из инструкции — только пример. Затем перезапустите сервер.') from exc
            if 'not registered' in detail:
                raise EarthEngineUnavailable('Проект EE_PROJECT не зарегистрирован для Earth Engine. Зарегистрируйте его в Google Cloud Console → Earth Engine.') from exc
            raise EarthEngineUnavailable('Не удалось подключиться к Earth Engine. Проверьте авторизацию, EE_PROJECT и доступ проекта к Earth Engine.') from exc


def _ndvi(image):
    scl = image.select('SCL')
    mask = scl.neq(0).And(scl.neq(1)).And(scl.neq(3)).And(scl.neq(7)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    return image.updateMask(mask).normalizedDifference(['B8', 'B4']).rename('NDVI').copyProperties(image, ['system:time_start'])


def calculate(geometry, start, end_exclusive, cloud):
    initialize()
    region = ee.Geometry(geometry)
    collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                  .filterBounds(region).filterDate(start, end_exclusive)
                  .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', cloud)))
    count = collection.size().getInfo()
    if not count:
        return None
    images = collection.map(_ndvi)
    composite = images.median().clip(region)
    # Fixed 10 m sampling; use the first Sentinel scene's native CRS for all statistics.
    crs = ee.Image(collection.first()).select('B4').projection()
    reducer = ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True)
    stats = composite.reduceRegion(reducer=reducer, geometry=region, scale=10, crs=crs,
                                   maxPixels=20000000, tileScale=4)
    valid_area = ee.Image.pixelArea().updateMask(composite.mask()).rename('area').reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=10, crs=crs, maxPixels=20000000, tileScale=4).get('area')
    dates = ee.List(collection.aggregate_array('system:time_start')).map(
        lambda timestamp: ee.Date(timestamp).format('YYYY-MM-dd')).distinct().sort()
    info = ee.Dictionary({'stats': stats, 'valid_area': valid_area, 'area': region.area(1), 'dates': dates}).getInfo()
    if info['stats'].get('NDVI_mean') is None:
        return None
    tile = composite.getMapId({'min': -1, 'max': 1, 'palette': PALETTE})
    return {'mean': info['stats']['NDVI_mean'], 'min': info['stats']['NDVI_min'],
            'max': info['stats']['NDVI_max'], 'coverage': min(100, 100 * (info.get('valid_area') or 0) / info['area']),
            'scene_count': count, 'dates': info['dates'], 'tile_url': tile['tile_fetcher'].url_format,
            'scale': 10, 'composite': 'median', 'palette': PALETTE}
