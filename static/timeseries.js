let seriesData = null, seriesVersion = 0, seriesController;
let dailyMapLayer = null, previousCompositeVisible = false;
map.createPane('daily-ndvi'); map.getPane('daily-ndvi').style.zIndex = 470;
const dailyPalette = ['9e3450','d88452','e8ce79','b5cf76','66a867','28724e','124631'];
function dailyNdviColor(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '#a2aaa1';
  const position = (Math.max(-1,Math.min(1,value))+1)/2*(dailyPalette.length-1);
  const index = Math.min(dailyPalette.length-2,Math.floor(position)), fraction = position-index;
  const rgb = hex => [0,2,4].map(offset => parseInt(hex.slice(offset,offset+2),16));
  const a=rgb(dailyPalette[index]), b=rgb(dailyPalette[index+1]);
  return '#'+a.map((v,i)=>Math.round(v+(b[i]-v)*fraction).toString(16).padStart(2,'0')).join('');
}
function removeDailyColor(restoreComposite=true) {
  if (dailyMapLayer) {map.removeLayer(dailyMapLayer);dailyMapLayer=null;}
  if (restoreComposite && previousCompositeVisible && ndviLayer) {ndviLayer.addTo(map);$('show-ndvi').checked=true;}
  previousCompositeVisible=false;
}
function colorSelectedDay(point) {
  if (!selected || !$('color-by-day').checked) return;
  if (!dailyMapLayer) {
    previousCompositeVisible=!!ndviLayer && map.hasLayer(ndviLayer);
    dailyMapLayer=L.geoJSON(selected,{pane:'daily-ndvi',style:{weight:2,color:'#244f3d'}}).addTo(map);
  }
  if (ndviLayer) map.removeLayer(ndviLayer);
  $('show-ndvi').checked=false;
  const available=typeof point.restored==='number' && Number.isFinite(point.restored);
  dailyMapLayer.setStyle({fill:true,fillColor:dailyNdviColor(point.restored),fillOpacity:available?1:.55,
    dashArray:point.is_reconstructed?'5 4':available?null:'2 5'});
  const label=document.createElement('span');label.textContent=`${displayDate(point.date)} · ${available ? `NDVI ${fmt(point.restored)} · ${point.is_reconstructed?'восстановлено':point.source}`:'Нет данных NDVI'}`;
  dailyMapLayer.eachLayer(layer=>{layer.unbindTooltip();layer.bindTooltip(label);});
}
function clearSeries() {
  removeDailyColor(); $('map-day-control').hidden=true;
  seriesVersion++; seriesController?.abort(); seriesData = null;
  $('series-panel').hidden = true; document.querySelector('.map-panel').classList.remove('with-series');
  $('load-series').disabled = !selected; $('load-series').textContent = 'Восстановить ряд и найти аномалии';
  $('series-status').textContent = '';
}
window.addEventListener('analysis-reset', clearSeries);
window.addEventListener('field-selected', () => {$('load-series').disabled = false;});
$('series-source').addEventListener('change', clearSeries);
$('close-series').addEventListener('click', () => {$('series-panel').hidden = true; document.querySelector('.map-panel').classList.remove('with-series');});
$('load-series').addEventListener('click', async () => {
  if (!selected) return;
  const start = validateDate('start'), end = validateDate('end');
  if (!$('start').reportValidity() || !$('end').reportValidity()) return;
  if (new Date(end) - new Date(start) > 120*86400000 || start > end) {$('series-status').textContent = 'Выберите корректный период не больше 120 дней.'; return;}
  clearSeries(); const version = seriesVersion;
  seriesController = new AbortController(); $('load-series').disabled = true;
  $('load-series').textContent = 'Получаем временной ряд…';
  $('series-status').textContent = 'Собираем снимки и погоду, рассчитываем норму и восстанавливаем пропуски…';
  try {
    const data = await api('/api/timeseries',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({geometry:selected.geometry,start,end,cloud:Number($('cloud').value),source:$('series-source').value}),signal:seriesController.signal});
    if (version !== seriesVersion) return;
    seriesData = data; $('series-panel').hidden = false; document.querySelector('.map-panel').classList.add('with-series');
    $('series-summary').textContent = `${data.observed_count} наблюдений · ${data.reconstructed_count} восстановленных дней · ${displayDate(start)} — ${displayDate(end)}`;
    $('series-day').max = Math.max(0,data.points.length-1); $('series-day').value = 0;
    $('map-day-slider').max = $('series-day').max; $('map-day-slider').value = 0;
    $('map-day-control').hidden=false; $('color-by-day').checked=true;
    $('map-day-start').textContent=displayDate(data.points[0].date);
    $('map-day-end').textContent=displayDate(data.points[data.points.length-1].date);
    $('series-note').textContent = data.note;
    $('series-status').textContent = data.warnings.length ? data.warnings.join(' ') : 'Временной ряд готов.';
    const episodes = $('series-episodes'); episodes.replaceChildren();
    if (!data.episodes.length) {
      const p = document.createElement('p'); p.className = 'micro';
      p.textContent = data.points.some(p => p.zscore !== null || p.temperature_zscore !== null) ? 'Устойчивых аномалий NDVI или температуры длительностью от трёх дней не найдено.' : 'Недостаточно исторических наблюдений для оценки аномалий.';
      episodes.append(p);
    }
    data.episodes.forEach(episode => {
      const card = document.createElement('div'); card.className = 'episode';
      const title = document.createElement('b'); title.textContent = `${episode.title} · ${displayDate(episode.start)} — ${displayDate(episode.end)} · ${episode.days} дней`;
      card.classList.add(`episode-${episode.type}`);
      const p = document.createElement('p'); p.textContent = episode.explanation;
      card.append(title,p); episodes.append(card);
    });
    drawSeries(); showSeriesDay();
  } catch(error) {if (version === seriesVersion && error.name !== 'AbortError') $('series-status').textContent = error.message;}
  finally {if (version === seriesVersion) {$('load-series').disabled = false; $('load-series').textContent = 'Восстановить ряд и найти аномалии';}}
});
function drawSeries() {
  if (!seriesData) return;
  const points = seriesData.points, W = 780, H = 210, left = 45, right = 15, top = 15, bottom = 30;
  const x = i => left + i*(W-left-right)/Math.max(1,points.length-1);
  const values = points.flatMap(p => [p.observed,p.restored,p.normal === null ? null : p.normal+(p.normal_std||0),p.normal === null ? null : p.normal-(p.normal_std||0)]).filter(v => v !== null);
  const min = Math.min(-.1,...values)-.03, max = Math.max(.85,...values)+.03;
  const y = value => top+(max-value)/(max-min)*(H-top-bottom);
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('role','img'); svg.setAttribute('aria-label','NDVI: исходные наблюдения, восстановление и историческая норма');
  function element(tag,attrs,text) {const node = document.createElementNS(ns,tag); for(const [k,v] of Object.entries(attrs)) node.setAttribute(k,String(v)); if(text!==undefined) node.textContent=text; svg.append(node); return node;}
  for(let tick=0;tick<=4;tick++) {const value=min+(max-min)*tick/4; element('line',{x1:left,y1:y(value),x2:W-right,y2:y(value),stroke:'#e1e7da'}); element('text',{x:left-7,y:y(value)+4,'text-anchor':'end',fill:'#76816d','font-size':10},fmt(value,2));}
  const dateIndices = [...new Set([0,Math.floor((points.length-1)/2),points.length-1])];
  dateIndices.forEach(i => element('text',{x:x(i),y:H-7,'text-anchor':i===0?'start':i===points.length-1?'end':'middle',fill:'#76816d','font-size':10},displayDate(points[i].date)));
  seriesData.episodes.forEach(episode => {const a=points.findIndex(p=>p.date===episode.start),b=points.findIndex(p=>p.date===episode.end); element('rect',{x:x(a),y:top,width:Math.max(3,x(b)-x(a)),height:H-top-bottom,fill:episode.type==='cold'?'#5b98cb':episode.type==='heat'?'#da6851':'#cba842',opacity:.12});});
  function line(key,color,dashed=false) {let path='', previous=false; points.forEach((p,i)=>{if(p[key]===null){previous=false;return;} path+=`${previous?'L':'M'}${x(i)},${y(p[key])} `;previous=true;}); element('path',{d:path,fill:'none',stroke:color,'stroke-width':2,...(dashed?{'stroke-dasharray':'4 3'}:{})});}
  if ($('chart-normal').checked) {
    for(let i=1;i<points.length;i++) {const a=points[i-1],b=points[i]; if(a.normal===null||b.normal===null) continue; element('polygon',{points:`${x(i-1)},${y(a.normal+(a.normal_std||0))} ${x(i)},${y(b.normal+(b.normal_std||0))} ${x(i)},${y(b.normal-(b.normal_std||0))} ${x(i-1)},${y(a.normal-(a.normal_std||0))}`,fill:'#9aac7c',opacity:.2});}
    line('normal','#8b9a71');
  }
  if ($('chart-restored').checked) line('restored','#d79b3b',true);
  if ($('chart-observed').checked) points.forEach((p,i)=>{if(p.observed===null)return; const dot=element('circle',{cx:x(i),cy:y(p.observed),r:3.5,fill:'#2d6244',tabindex:0}); const title=document.createElementNS(ns,'title');title.textContent=`${displayDate(p.date)} · ${p.source} · NDVI ${fmt(p.observed)}`;dot.append(title);dot.addEventListener('click',()=>{$('series-day').value=i;showSeriesDay();});dot.addEventListener('focus',()=>{$('series-day').value=i;showSeriesDay();});});
  $('series-chart').replaceChildren(svg);
  drawTemperature();
}
function drawTemperature() {
  const points=seriesData.points, values=points.flatMap(p=>[p.temperature,p.temperature_normal]).filter(v=>v!==null);
  const container=$('temperature-chart');container.replaceChildren();
  if(!values.length){container.textContent='Температурные данные недоступны.';return;}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 780 120');svg.setAttribute('role','img');svg.setAttribute('aria-label','Среднесуточная температура и её историческая норма');
  const low=Math.min(...values)-2,high=Math.max(...values)+2,x=i=>45+i*720/Math.max(1,points.length-1),y=v=>10+(high-v)/(high-low)*80;
  const add=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e;};
  [low,(low+high)/2,high].forEach(v=>{add('line',{x1:45,x2:765,y1:y(v),y2:y(v),stroke:'#e1e7da'});add('text',{x:40,y:y(v)+4,'text-anchor':'end',fill:'#76816d','font-size':10},`${fmt(v,0)} °C`);});
  for(const key of ['temperature_normal','temperature']){let d='',previous=false;points.forEach((p,i)=>{if(p[key]===null){previous=false;return;}d+=`${previous?'L':'M'}${x(i)},${y(p[key])} `;previous=true;});add('path',{d,fill:'none',stroke:key==='temperature'?'#c7644f':'#8b9a71','stroke-width':2,'stroke-dasharray':key==='temperature'?'none':'4 3'});}
  points.forEach((p,i)=>{if(p.temperature===null)return;const dot=add('circle',{cx:x(i),cy:y(p.temperature),r:3,fill:'#c7644f'});const title=document.createElementNS(ns,'title');title.textContent=`${displayDate(p.date)} · ${fmt(p.temperature,1)} °C · ${p.temperature_status}`;dot.append(title);dot.addEventListener('click',()=>{$('series-day').value=i;showSeriesDay();});});
  container.append(svg);
}
function showSeriesDay() {
  if (!seriesData) return;
  const p = seriesData.points[Number($('series-day').value)];
  $('map-day-slider').value=$('series-day').value;
  $('map-day-date').textContent=displayDate(p.date);
  const available=typeof p.restored==='number' && Number.isFinite(p.restored);
  const description=available?`Средний NDVI: ${fmt(p.restored,3)} · ${p.is_reconstructed?'Восстановленное значение':p.source}`:'Нет данных NDVI на этот день';
  $('map-day-value').textContent=description;
  $('map-day-slider').setAttribute('aria-valuetext',`${displayDate(p.date)}. ${description}`);
  $('day-previous').disabled=Number($('series-day').value)===0;
  $('day-next').disabled=Number($('series-day').value)===seriesData.points.length-1;
  colorSelectedDay(p);
  $('series-detail').textContent = `${displayDate(p.date)} · ${p.observed !== null ? p.source : p.is_reconstructed ? 'Восстановлено моделью' : 'Нет данных'} · NDVI ${p.restored === null ? '—' : fmt(p.restored)} · ${p.status}${p.temperature === null ? '' : ` · ${fmt(p.temperature,1)} °C`}${p.precipitation === null ? '' : ` · осадки ${fmt(p.precipitation,1)} мм`}`;
  $('series-detail').textContent += ` · ${p.temperature_status}${p.temperature_normal === null ? '' : ` (норма ${fmt(p.temperature_normal,1)} °C; z=${p.temperature_zscore === null ? '—' : fmt(p.temperature_zscore)})`}${p.combined_anomaly ? ' · Совпадение NDVI и температурного отклонения' : ''}`;
}
['chart-observed','chart-restored','chart-normal'].forEach(id => $(id).addEventListener('change',drawSeries));
$('series-day').addEventListener('input',showSeriesDay);
$('map-day-slider').addEventListener('input',()=>{$('series-day').value=$('map-day-slider').value;showSeriesDay();});
for (const [id,step] of [['day-previous',-1],['day-next',1]]) $(id).addEventListener('click',()=>{
  if(!seriesData)return;
  $('series-day').value=Math.max(0,Math.min(seriesData.points.length-1,Number($('series-day').value)+step));showSeriesDay();
});
$('color-by-day').addEventListener('change',()=>{if($('color-by-day').checked)showSeriesDay();else removeDailyColor();});
$('show-ndvi').addEventListener('change',()=>{if($('show-ndvi').checked){$('color-by-day').checked=false;removeDailyColor(false);}});
$('download-series').addEventListener('click',()=>{
  if (!seriesData) return;
  const columns=['date','observed','restored','is_reconstructed','source','normal','normal_std','reference_years','zscore','status','temperature','temperature_normal','temperature_std','temperature_zscore','temperature_reference_years','temperature_status','combined_anomaly','precipitation'];
  const quote = value => value===null ? '' : `"${String(value).replaceAll('"','""')}"`;
  const csv=[columns.join(','),...seriesData.points.map(p=>columns.map(c=>quote(p[c])).join(','))].join('\n');
  const url=URL.createObjectURL(new Blob(['\uFEFF',csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='ndvi-timeseries.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
let drawnPoints=[], drawingLine;
window.drawingField=false;
function stopDrawing() {window.drawingField=false; if(drawingLine){map.removeLayer(drawingLine);drawingLine=null;}drawnPoints=[];$('finish-field').hidden=true;$('cancel-field').hidden=true;$('draw-field').hidden=false;map.getContainer().style.cursor='';}
$('draw-field').addEventListener('click',()=>{window.drawingField=true;drawnPoints=[];$('draw-field').hidden=true;$('finish-field').hidden=false;$('cancel-field').hidden=false;$('map-status').textContent='Нажимайте на карту, отмечая вершины участка. Затем завершите контур.';map.getContainer().style.cursor='crosshair';});
$('cancel-field').addEventListener('click',stopDrawing);
map.on('click',event=>{if(!window.drawingField)return;drawnPoints.push(event.latlng);if(drawingLine)drawingLine.setLatLngs(drawnPoints);else drawingLine=L.polyline(drawnPoints,{color:'#244f3d',weight:3,interactive:false}).addTo(map);});
$('finish-field').addEventListener('click',()=>{if(drawnPoints.length<3){$('map-status').textContent='Нужны как минимум три вершины.';return;}const coords=drawnPoints.map(p=>[p.lng,p.lat]);coords.push([...coords[0]]);const feature={type:'Feature',geometry:{type:'Polygon',coordinates:[coords]},properties:{name:'Мой участок',osm_id:'custom',custom:true}};stopDrawing();selectField(feature);$('map-status').textContent='Собственный контур выбран. Можно выполнить анализ.';});
