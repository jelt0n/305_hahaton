"""Convert Overpass geometries, including fragmented multipolygon rings."""
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import polygonize, unary_union


def _coords(points):
    return [(p['lon'], p['lat']) for p in points]


def _rings(members, role):
    lines = [LineString(_coords(m['geometry'])) for m in members
             if m.get('type') == 'way' and m.get('role', '') in role
             and len(m.get('geometry', [])) >= 2]
    return list(polygonize(unary_union(lines))) if lines else []


def farmland_geojson(data):
    elements = data.get('elements', [])
    features, represented = [], set()
    # Relations first: suppress member ways only after a valid relation is built.
    for item in sorted(elements, key=lambda e: e['type'] != 'relation'):
        if item.get('tags', {}).get('landuse') != 'farmland':
            continue
        if item['type'] == 'way' and item['id'] in represented:
            continue
        if item['type'] == 'relation':
            members = item.get('members', [])
            outer = _rings(members, {'outer', ''})
            if not outer:
                continue
            geom = unary_union(outer)
            inner = _rings(members, {'inner'})
            if inner:
                geom = geom.difference(unary_union(inner))
        elif item['type'] == 'way':
            coords = _coords(item.get('geometry', []))
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            geom = Polygon(coords)
        else:
            continue
        if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
            continue
        if item['type'] == 'relation':
            represented.update(m['ref'] for m in item.get('members', []) if m.get('type') == 'way')
        osm_id = f"{item['type']}/{item['id']}"
        features.append({'type': 'Feature', 'id': osm_id, 'geometry': mapping(geom),
                         'properties': {'osm_id': osm_id, 'name': item['tags'].get('name', 'Поле без названия'),
                                        'landuse': 'farmland'}})
    return {'type': 'FeatureCollection', 'features': features}
