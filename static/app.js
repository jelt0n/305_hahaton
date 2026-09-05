/* Real OSM boundaries and Earth Engine results only. */
const $ = id => document.getElementById(id);
if (!window.L) {
  $('map-status').textContent = 'Не удалось загрузить Leaflet. Проверьте интернет и обновите страницу.';
  throw new Error('Leaflet unavailable');
}
const map = L.map('map', {zoomControl: false}).setView([45.05, 38.90], 13);
map.attributionControl.setPrefix(false);
L.control.zoom({position: 'topright'}).addTo(map);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map).on('tileerror', () => { $('map-status').textContent = 'Подложка OSM недоступна. Проверьте соединение.'; });
map.createPane('ndvi'); map.getPane('ndvi').style.zIndex = 450; map.getPane('ndvi').style.pointerEvents = 'none';
map.createPane('selection'); map.getPane('selection').style.zIndex = 460; map.getPane('selection').style.pointerEvents = 'none';
let fieldLayer, selected, selectionLayer, ndviLayer, result, analysisVersion = 0, analysisController;
const fieldStyle = {color:'#71864a', weight:1.5, fillColor:'#a8bc69', fillOpacity:.15};
const fmt = (n, digits = 2) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits:digits, minimumFractionDigits:digits}).format(n);
function status(message, error = false) { $('analysis-status').textContent = message; $('analysis-status').classList.toggle('error', error); }
function clearResult() {
  window.dispatchEvent(new Event('analysis-reset'));
  analysisVersion++; analysisController?.abort();
  if (ndviLayer) {map.removeLayer(ndviLayer); ndviLayer = null;}
  result = null; $('results').hidden = true; $('calculate').disabled = !selected;
  $('calculate').innerHTML = 'Рассчитать NDVI <span>↗</span>';
}
const displayDate = iso => iso.split('-').reverse().join('.');
const localDate = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
function parseDate(value) {
  const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value.trim());
  if (!match) return null;
  const [, day, month, year] = match;
  const iso = `${year}-${month}-${day}`;
  const date = new Date(`${iso}T12:00:00`);
  return !Number.isNaN(date.getTime()) && localDate(date) === iso ? iso : null;
}
function validateDate(id) {
  const iso = parseDate($(id).value);
  $(id).setCustomValidity(!iso ? 'Введите существующую дату в формате ДД.ММ.ГГГГ.' :
    iso < '2017-03-28' || iso > localDate(new Date()) ? 'Дата должна быть между 28.03.2017 и сегодняшним днём.' : '');
  $(id + '-calendar').value = iso || '';
  return iso;
}
function setPeriod(days) {
  const end = new Date(), start = new Date(); start.setDate(end.getDate() - days + 1);
  $('end').value = displayDate(localDate(end)); $('start').value = displayDate(localDate(start));
  $('start-calendar').max = $('end-calendar').max = localDate(end);
  validateDate('start'); validateDate('end');
}
setPeriod(30);
function changedParameters() {clearResult(); status(selected ? 'Параметры изменены. Выполните расчёт.' : 'Сначала выберите участок на карте.');}
document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => {
  setPeriod(Number(button.dataset.days)); document.querySelectorAll('[data-days]').forEach(b => b.classList.toggle('active', b === button)); changedParameters();
}));
['start','end'].forEach(id => {
  const changed = () => {validateDate(id); document.querySelectorAll('[data-days]').forEach(b => b.classList.remove('active')); changedParameters();};
  $(id).addEventListener('input', changed);
  $(id + '-calendar').addEventListener('click', event => {event.currentTarget.showPicker?.();});
  $(id + '-calendar').addEventListener('change', event => {$(id).value = displayDate(event.target.value); changed();});
});
$('cloud').addEventListener('input', () => { $('cloud-value').textContent = `до ${$('cloud').value}%`; changedParameters(); });
async function api(url, options = {}) {
  const response = await fetch(url, options); let data;
  try {data = await response.json();} catch {throw new Error('Сервер вернул некорректный ответ.');}
  if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(x => x.msg.replace('Value error, ', '')).join(' ') : data.detail || 'Ошибка запроса.');
  return data;
}
async function loadFields() {
  const b = map.getBounds();
  if (b.getNorth()-b.getSouth() > .15 || b.getEast()-b.getWest() > .25) { $('map-status').textContent = 'Приблизьте карту ещё на один-два уровня и повторите поиск.'; return; }
  $('load-fields').disabled = true; $('map-status').textContent = 'Загружаем контуры полей из OpenStreetMap…';
  const bounds = [b.getSouth(),b.getWest(),b.getNorth(),b.getEast()].map(x => x.toFixed(6)).join(',');
  try {
    const data = await api(`/api/fields?bbox=${encodeURIComponent(bounds)}`);
    if (fieldLayer) map.removeLayer(fieldLayer);
    fieldLayer = L.geoJSON(data, {style:fieldStyle, onEachFeature: (feature, layer) => {
      const label = document.createElement('span'); label.textContent = feature.properties.name;
      layer.bindTooltip(label);
      layer.on('mouseover', () => layer.setStyle({fillOpacity:.3,weight:2}));
      layer.on('mouseout', () => layer.setStyle(fieldStyle));
      layer.on('click', () => {if (!window.drawingField) selectField(feature);});
    }}).addTo(map);
    $('map-status').textContent = data.features.length ? `Найдено участков: ${data.features.length}. Нажмите на поле.` : 'Полей с тегом landuse=farmland здесь нет. Переместите карту.';
  } catch (error) { $('map-status').textContent = error.message; }
  finally { $('load-fields').disabled = false; }
}
function selectField(feature) {
  clearResult(); selected = feature;
  if (selectionLayer) map.removeLayer(selectionLayer);
  selectionLayer = L.geoJSON(feature, {pane:'selection', interactive:false, style:{color:'#244f3d',weight:3,fill:false}}).addTo(map);
  $('field-name').textContent = feature.properties.name;
  $('field-description').textContent = `Участок ${feature.properties.osm_id} · landuse=farmland`;
  $('osm-link').href = `https://www.openstreetmap.org/${feature.properties.osm_id}`; $('osm-link').hidden = false;
  if (feature.properties.custom) {
    $('field-description').textContent = 'Контур, заданный пользователем'; $('osm-link').hidden = true;
  }
  $('calculate').disabled = false; status('Участок выбран. Задайте период и рассчитайте NDVI.');
  window.dispatchEvent(new Event('field-selected'));
}
$('load-fields').addEventListener('click', loadFields);
map.on('moveend', () => { const c = map.getCenter(); $('coordinates').textContent = `${Math.abs(c.lat).toFixed(4)}° ${c.lat >= 0 ? 'N' : 'S'} · ${Math.abs(c.lng).toFixed(4)}° ${c.lng >= 0 ? 'E' : 'W'}`; });
$('locate').addEventListener('click', () => {
  if (!navigator.geolocation) { $('map-status').textContent = 'Браузер не поддерживает геолокацию.'; return; }
  $('map-status').textContent = 'Определяем местоположение…';
  navigator.geolocation.getCurrentPosition(pos => {map.setView([pos.coords.latitude,pos.coords.longitude],14); loadFields();}, () => {$('map-status').textContent = 'Местоположение недоступно. Найдите участок вручную.';}, {timeout:10000});
});
$('calculate').addEventListener('click', async () => {
  if (!selected) return;
  const start = validateDate('start'), end = validateDate('end');
  if (!$('start').reportValidity() || !$('end').reportValidity()) return;
  clearResult(); const version = analysisVersion;
  const geometry = selected.geometry, cloud = Number($('cloud').value);
  analysisController = new AbortController();
  $('calculate').disabled = true; $('calculate').textContent = 'Рассчитываем…';
  status('Earth Engine обрабатывает спутниковые снимки. Обычно это занимает до минуты.');
  try {
    const data = await api('/api/ndvi', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry,start,end,cloud}), signal:analysisController.signal});
    if (version !== analysisVersion) return;
    result = {...data, start, end, cloud};
    ndviLayer = L.tileLayer(data.tile_url, {pane:'ndvi',opacity:Number($('opacity').value), maxZoom:19, attribution:'NDVI: Google Earth Engine · Copernicus Sentinel-2'});
    ndviLayer.on('tileerror', () => status('Не удалось загрузить слой NDVI. Повторите расчёт, чтобы обновить ссылку на тайлы.',true));
    $('show-ndvi').checked = true; ndviLayer.addTo(map);
    $('mean').textContent = fmt(data.mean); $('minimum').textContent = fmt(data.min); $('maximum').textContent = fmt(data.max); $('coverage').textContent = `${fmt(data.coverage,1)}%`;
    $('scene-info').textContent = `${data.scene_count} сцен · ${data.dates.length} дат съёмки · разрешение ${data.scale} м. Съёмки: ${data.dates.map(displayDate).join(', ')}.`;
    $('result-period').textContent = `Период: ${displayDate(start)} — ${displayDate(end)}, включительно. Облачность сцены ≤ ${cloud}%.`;
    $('results').hidden = false;
    status(data.coverage < 80 ? 'Расчёт готов. Неполное покрытие: статистика относится только к доступной части поля.' : 'Расчёт готов. Слой NDVI показан в границах поля.');
  } catch (error) {if (version === analysisVersion && error.name !== 'AbortError') status(error.message,true);}
  finally {if (version === analysisVersion) {$('calculate').disabled = false; $('calculate').innerHTML = 'Рассчитать NDVI <span>↗</span>';}}
});
$('show-ndvi').addEventListener('change', () => { if (ndviLayer) $('show-ndvi').checked ? ndviLayer.addTo(map) : map.removeLayer(ndviLayer); });
$('opacity').addEventListener('input', () => ndviLayer?.setOpacity(Number($('opacity').value)));
$('download').addEventListener('click', () => {
  if (!result || !selected) return;
  const {tile_url, ...statistics} = result;
  const blob = new Blob([JSON.stringify({...selected, properties:{...selected.properties, ndvi:statistics, source:'COPERNICUS/S2_SR_HARMONIZED'}},null,2)],{type:'application/geo+json'});
  const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href=url; a.download=`ndvi-${selected.properties.osm_id.replace('/','-')}.geojson`; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
api('/api/health').then(data => {
  $('connection').textContent = data.earth_engine ? '● Earth Engine подключён' : '○ Earth Engine: нужна настройка';
  $('connection').classList.toggle('ready',data.earth_engine); $('connection').title = data.message;
  if (!data.earth_engine) status(data.message);
}).catch(() => { $('connection').textContent = '○ Сервер недоступен'; });
loadFields();
