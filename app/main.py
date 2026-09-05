from datetime import date, timedelta
from pathlib import Path
import logging
import math
import time
from threading import BoundedSemaphore, Lock

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field, model_validator
from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from app.osm import farmland_geojson
from app import earth_engine

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
app = FastAPI(title='Поле · NDVI', version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
log = logging.getLogger(__name__)
_cache = {}
_cache_lock = Lock()
_ee_slots = BoundedSemaphore(2)


@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')


@app.get('/api/health')
def health():
    try:
        earth_engine.initialize()
        return {'earth_engine': True, 'message': 'Earth Engine подключён'}
    except earth_engine.EarthEngineUnavailable as exc:
        return {'earth_engine': False, 'message': str(exc)}


@app.get('/api/fields')
def fields(bbox: str = Query(max_length=120)):
    try:
        south, west, north, east = map(float, bbox.split(','))
        if not all(math.isfinite(v) for v in (south, west, north, east)):
            raise ValueError()
        if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, 'Некорректные границы карты.')
    if north - south > .15 or east - west > .25:
        raise HTTPException(422, 'Приблизьте карту: область поиска слишком большая.')
    key = (south, west, north, east)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    query = f'[out:json][timeout:25];nwr["landuse"="farmland"]({south},{west},{north},{east});out geom;'
    for endpoint in ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']:
        try:
            response = httpx.post(endpoint, data={'data': query}, timeout=35,
                                  headers={'User-Agent': 'FarmlandNDVI/1.0'})
            response.raise_for_status()
            payload = response.json()
            if payload.get('remark'):
                raise ValueError('Incomplete Overpass response')
            result = farmland_geojson(payload)
            with _cache_lock:
                if len(_cache) >= 64:
                    _cache.pop(next(iter(_cache)))
                _cache[key] = (time.monotonic(), result)
            return result
        except (httpx.HTTPError, ValueError):
            continue
    raise HTTPException(502, 'OpenStreetMap временно не отвечает. Повторите поиск через минуту.')


class NDVIRequest(BaseModel):
    geometry: dict
    start: date
    end: date
    cloud: int = Field(default=60, ge=0, le=100)

    @model_validator(mode='after')
    def validate_request(self):
        if not date(2017, 3, 28) <= self.start <= self.end <= date.today():
            raise ValueError('Период должен быть между 28.03.2017 и сегодняшним днём.')
        if (self.end - self.start).days > 366:
            raise ValueError('Выберите период не больше года.')
        try:
            geom = shape(self.geometry)
            if geom.geom_type not in ('Polygon', 'MultiPolygon') or geom.is_empty or not geom.is_valid or geom.has_z:
                raise ValueError()
            west, south, east, north = geom.bounds
            if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
                raise ValueError()
            if east - west > 2 or north - south > 2:
                raise ValueError()
            polygons = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
            if sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polygons) > 20000:
                raise ValueError()
            area = sum(abs(Geod(ellps='WGS84').geometry_area_perimeter(orient(p))[0]) for p in polygons)
            if not 100 <= area <= 100000000:
                raise ValueError()
        except Exception as exc:
            raise ValueError('Нужен корректный полигон площадью от 0,01 до 10 000 га (до 20 000 вершин).') from exc
        return self


class SeriesRequest(NDVIRequest):
    source: str = Field(default='all', pattern='^(all|s2)$')
    history_years: int = Field(default=3, ge=2, le=5)


@app.post('/api/timeseries')
def timeseries(body: SeriesRequest):
    if (body.end-body.start).days>120:
        raise HTTPException(422, 'Для временного ряда выберите период не больше 120 дней.')
    if not _ee_slots.acquire(blocking=False):
        raise HTTPException(429, 'Сервис уже выполняет расчёты. Повторите через минуту.')
    try:
        from app.timeseries import collect, analyze
        frame,warnings=collect(body.geometry,body.start.isoformat(),body.end.isoformat(),body.cloud,body.source,body.history_years)
        result=analyze(frame,body.start.isoformat(),body.end.isoformat(),ROOT/'models/ndvi')
        result['warnings']=warnings
        return result
    except earth_engine.EarthEngineUnavailable as exc:
        raise HTTPException(503,str(exc))
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    except Exception:
        log.exception('Time series failed')
        raise HTTPException(502,'Не удалось получить временной ряд. Попробуйте меньший период. Подробности в журнале сервера.')
    finally:
        _ee_slots.release()


@app.post('/api/ndvi')
def ndvi(body: NDVIRequest):
    if not _ee_slots.acquire(blocking=False):
        raise HTTPException(429, 'Сервис уже выполняет расчёты. Повторите через минуту.')
    try:
        result = earth_engine.calculate(body.geometry, body.start.isoformat(),
                                        (body.end + timedelta(days=1)).isoformat(), body.cloud)
        if result is None:
            raise HTTPException(404, 'Нет безоблачных пикселей за этот период. Расширьте период или увеличьте порог облачности.')
        return result
    except earth_engine.EarthEngineUnavailable as exc:
        raise HTTPException(503, str(exc))
    except HTTPException:
        raise
    except Exception:
        log.exception('Earth Engine calculation failed')
        raise HTTPException(502, 'Ошибка расчёта Earth Engine. Повторите запрос или выберите меньший период. Подробности в журнале сервера.')
    finally:
        _ee_slots.release()
