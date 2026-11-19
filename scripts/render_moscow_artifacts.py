#!/usr/bin/env python3
"""Create maps from already verified source-snapshot rows, not synthetic POIs."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'/'demo'/'moscow'
ART=ROOT/'artifacts'
REPORT=ROOT/'reports'
pois=json.loads((DATA/'pois_real_master.json').read_text(encoding='utf-8'))['pois']
stat=json.loads((REPORT/'MOSCOW_DATA_STATISTICS.json').read_text(encoding='utf-8'))
CATS=[('museums','Музеи и галереи','#a78bfa'),('history','История','#fbbf24'),('nature','Парки и природа','#34d399'),('cafes','Кафе и рестораны','#fb923c'),('entertainment','Театры и кино','#fb7185'),('shopping','Магазины','#60a5fa'),('viewpoints','Смотровые','#22d3ee'),('unusual','Необычное','#c084fc')]

# Embed source data. All external libraries are optional, and the static-SVG
# fallback works offline, including clustering and filtering.

def create_html(filename:str, center_only:bool=False):
    subset= [p for p in pois if p['quality_level']=='B' and p['city_area']=='central_administrative_okrug'] if center_only else pois
    mapped=[{'id':p['id'],'name':p['name_ru'],'lat':p['lat'],'lon':p['lon'],'category':p['primary_category'],
             'kind':p['source_type'],'quality':p['quality_level'],'district':p['district'] or 'Не определён',
             'coordinate_method':p['coordinate_method'],'issues':p['quality_reasons'],
             'duplicates':p['potential_duplicates'],'url':p['sources'][0]['source_code_url'],
             'source_id':p['source_ids']['derived_map_feature_id']} for p in subset]
    assert all(p['id'] and p['name'] and isinstance(p['lat'],float) and isinstance(p['lon'],float) for p in mapped)
    jsdata=json.dumps(mapped,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    catdata=json.dumps(CATS,ensure_ascii=False,separators=(',',':'))
    title='Центр Москвы · культурные POI уровня B' if center_only else 'Москва · реальные культурные POI'
    disclaimer='Только выбранные записи уровня B в пределах ЦАО. Независимая проверка A не проведена.' if center_only else 'Частичная выборка из одной OSM-производной карты. ТиНАО, рестораны, магазины и многие достопримечательности не собраны.'
    html='''<!doctype html><html lang="ru"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ — TripWise</title>

<style>
:root{color-scheme:dark;--bg:#090b1c;--panel:#12172e;--line:#303751;--muted:#a9b5d3;--txt:#f6f7ff;--violet:#a78bfa}*{box-sizing:border-box}
html,body{width:100%;height:100%;padding:0;margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--txt)}
main{display:grid;grid-template-columns:350px minmax(0,1fr);height:100vh;min-height:550px}
aside{background:linear-gradient(160deg,#151733,#0c1023);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow:auto;padding:22px;gap:16px;z-index:3}
.brand{font-size:12px;font-weight:800;color:#a78bfa;letter-spacing:2px}.title{font-size:24px;font-weight:800;letter-spacing:-.6px;line-height:1.2;margin:5px 0 4px}
.muted{color:var(--muted)}.info{padding:13px 15px;border:1px solid #584f87;border-radius:14px;color:#d7d0ff;font-size:12px;background:#211f3a}
.numrow{display:grid;grid-template-columns:1fr 1fr;gap:10px}.numbox{background:#1b2140;border:1px solid #353c61;border-radius:13px;padding:13px}.num{font-size:26px;font-weight:800;letter-spacing:-1px;line-height:1.1}.numlabel{font-size:11px;color:#aeb6d5}
.controls{display:grid;gap:12px}.field{display:grid;gap:6px}.field>label{font-size:12px;letter-spacing:.3px;color:#cbd5e1;font-weight:650}
input[type=text],select{background:#1d2340;border:1px solid #444b70;color:white;padding:10px 12px;border-radius:9px;width:100%;outline:none;font:inherit}
input:focus,select:focus{border-color:#a78bfa}select{max-width:100%}
.categories{display:grid;grid-template-columns:1fr 1fr;gap:7px}.cat{display:flex;align-items:center;gap:7px;border:1px solid #39405e;padding:9px 8px;border-radius:10px;cursor:pointer;font-size:12px;background:#1b2038}.cat input{accent-color:#a78bfa;flex:0 0 auto}
.dot{width:8px;height:8px;border-radius:100%;display:inline-block;flex:none}.cat em{font-style:normal;color:#98a5c7;margin-left:auto}
.actions{display:flex;flex-wrap:wrap;gap:6px}button{border:1px solid #465070;background:#252a4b;color:white;border-radius:10px;padding:9px 11px;cursor:pointer;font:inherit;font-size:12px}button:hover{background:#393a66}button.accent{background:#6759ae;border-color:#6759ae}
footer{color:#99a8c8;font-size:11px;line-height:1.5;margin-top:auto}
.mapwrap{position:relative;overflow:hidden;background:#0d1426}.map{width:100%;height:100%;z-index:1}
#offline{position:absolute;inset:0;display:none;z-index:1;background:radial-gradient(ellipse at 60% 40%,#1c2a47 0%,#101629 70%);overflow:hidden}
#plot{width:100%;height:100%;touch-action:none;cursor:grab}#plot:active{cursor:grabbing}
#offlineBanner{position:absolute;top:17px;left:17px;font-size:12px;color:#bdc6e2;background:#131c36ed;border:1px solid #344361;border-radius:10px;padding:8px 13px;pointer-events:none}
#detail{position:absolute;right:16px;top:16px;max-width:320px;max-height:75vh;overflow:auto;background:#141b36ef;backdrop-filter:blur(16px);border:1px solid #536184;border-radius:17px;box-shadow:0 18px 70px #0007;padding:17px;z-index:12;display:none}
#detail h3{margin:5px 30px 12px 0;font-size:18px;line-height:1.3}#detail p{margin:7px 0;color:#c3cce5}#detail a{color:#b7a9fc}#detail .close{position:absolute;right:10px;top:10px;border:0;background:transparent;padding:6px 9px;font-size:20px}
.statsstrip{position:absolute;bottom:13px;left:14px;right:14px;display:flex;gap:7px;flex-wrap:wrap;pointer-events:none;z-index:10}.pill{background:#11172aea;border:1px solid #3b4668;border-radius:20px;padding:7px 12px;font-size:12px;color:#dbe3f7}
.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#1b2340;color:#f5f5ff}.leaflet-popup-content{margin:12px}.leaflet-popup a{color:#ad9fff}.leaflet-container{background:#101a30}
.pmarker{height:13px;width:13px;border:2px solid #e7e8ff;border-radius:100%;box-shadow:0 1px 9px #0008}.marker-cluster-small,.marker-cluster-medium,.marker-cluster-large{background:rgba(129,97,208,.3)}.marker-cluster div{background:#6453b8;color:white;font-weight:800}
@media(max-width:800px){main{grid-template-columns:1fr;grid-template-rows:45vh 55vh}aside{padding:12px;gap:8px}.title{font-size:19px}.categories{grid-template-columns:repeat(4,1fr)}.cat{font-size:10px;gap:2px;padding:6px;justify-content:center}.cat em{display:none}#detail{top:7px;right:7px;max-width:75%;font-size:12px}footer{display:none}}
</style></head>
<body><main><aside><header><div class="brand">TRIPWISE / DATA ATLAS</div><div class="title">__TITLE__</div><div class="muted">Реальные записи в культурном слое · 19.11.2026</div></header>
<div class="info">__DISCLAIMER__</div>
<div class="numrow"><div class="numbox"><div class="num" id="nTotal">—</div><div class="numlabel">В исходной выборке</div></div><div class="numbox"><div class="num" id="nVisible">—</div><div class="numlabel">По фильтрам</div></div></div>
<div class="controls"><div class="field"><label for="search">Найти место</label><input id="search" type="text" placeholder="Музей, парк, театр..." autocomplete="off"/></div>
<div class="field"><label>Категории TripWise</label><div class="categories" id="categories"></div></div>
<div class="field"><label for="quality">Уровень качества</label><select id="quality"><option value="all">Все уровни (B + C)</option><option value="B">B — запись открытого источника</option><option value="C">C — требует проверки</option><option value="A">A — два независимых источника</option></select></div>
<div class="field"><label for="district">Район</label><select id="district"><option value="all">Все районы</option></select></div>
<div class="field"><label><input id="onlyIssues" type="checkbox"/> Только потенциальные дубли и аномалии</label></div>
<div class="actions"><button class="accent" id="reset">Сбросить фильтры</button><button id="onlyCenter">Показать ЦАО</button><button id="allcity">Весь охват</button><button id="streetview">Уличная карта (онлайн)</button></div></div>
<footer>Источники: OpenStreetMap (ODbL 1.0), культурная карта ArtemIlinn / HSE 2026. Действующий статус заведений не проверен. Точка парка — внутри полигона, не гарантированный вход.<p><a style="color:#b9aafb" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">Атрибуция OSM</a> · <a style="color:#b9aafb" href="https://github.com/ArtemIlinn/masters-urban-analytics" target="_blank" rel="noopener">Первичная карта</a></p></footer>
</aside>
<section class="mapwrap"><div id="leaflet" class="map"></div><div id="offline"><svg id="plot" viewBox="0 0 1100 800" preserveAspectRatio="xMidYMid meet"></svg><div id="offlineBanner">Офлайн-карта по реальным координатам · без уличной подложки · колёсико — масштаб, мышь — перемещение</div></div>
<div id="detail"><button class="close" id="close">×</button><div id="detailBody"></div></div><div class="statsstrip"><span class="pill" id="sourceLabel">OSM-производный культурный слой</span><span class="pill" id="qualityLabel">Без независимой проверки A</span><span class="pill" id="displayLabel">Загрузка карты…</span></div>
</section></main>
<script>const POINTS=__POINTS__; const CATINFO=__CATS__; const CATCOL=Object.fromEntries(CATINFO.map(c=>[c[0],c[2]]));
const $=s=>document.getElementById(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const filterCats=new Set(CATINFO.map(c=>c[0]));const centerNames=new Set(['Мещанский район','район Замоскворечье','район Хамовники','Красносельский район','район Арбат','Тверской район','Пресненский район','Таганский район','район Якиманка','Басманный район']);
let filtered=[],leafletMap=null,markerLayer=null,isOffline=false,svgScale=1,svgCenter=[37.63,55.77],svgDragging=false,svgPrev=null;
$('nTotal').textContent=POINTS.length.toLocaleString('ru-RU');const catCount={};CATINFO.forEach(c=>catCount[c[0]]=POINTS.filter(p=>p.category===c[0]).length);
CATINFO.forEach(([id,name,col])=>{const el=document.createElement('label');el.className='cat';el.innerHTML=`<input type="checkbox" checked value="${esc(id)}"><span class="dot" style="background:${col}"></span><span>${esc(name)}</span><em>${catCount[id]}</em>`;el.querySelector('input').addEventListener('change',e=>{if(e.target.checked)filterCats.add(id);else filterCats.delete(id);refresh()});$('categories').append(el)});
[...new Set(POINTS.map(p=>p.district))].sort((a,b)=>a.localeCompare(b,'ru')).forEach(n=>{const el=document.createElement('option');el.value=n;el.textContent=n;$('district').append(el)});
function showDetail(p){$('detail').style.display='block';const srcLink='https://github.com/ArtemIlinn/masters-urban-analytics/blob/main/index.html'; const osmLink=`https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=17/${p.lat}/${p.lon}`;
 $('detailBody').innerHTML=`<div style="color:${CATCOL[p.category]};font-size:11px;font-weight:800;letter-spacing:1px">${esc(CATINFO.find(c=>c[0]===p.category)?.[1])} · ${esc(p.quality)}</div><h3>${esc(p.name)}</h3><p>${esc(p.district)}</p><p>Координаты: <b>${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</b></p><p>Источник: OSM-производная карта · элемент ${esc(p.source_id)}</p><p>${p.coordinate_method==='polygon_interior_point_derived'?'Координата внутри исходного полигона парка. Вход и дорожный доступ НЕ проверены.':'Координата маркера исходной карты. Работа заведения не подтверждена.'}</p>${p.issues.length?`<p style="color:#ffbfcb">Проверить: ${esc(p.issues.join(', '))}</p>`:''}${p.duplicates.length?`<p>Возможные дубли: ${esc(p.duplicates.join(', '))}</p>`:''}<p><a href="${srcLink}" target="_blank" rel="noopener">Исходная карта и код</a> · <a href="${osmLink}" target="_blank" rel="noopener">Координаты в OSM</a></p>`;
}
$('close').onclick=()=>$('detail').style.display='none';
function applyFilters(){const q=$('search').value.toLocaleLowerCase('ru').trim(),qual=$('quality').value,dist=$('district').value,issues=$('onlyIssues').checked;
 filtered=POINTS.filter(p=>filterCats.has(p.category)&&(qual==='all'||p.quality===qual)&&(dist==='all'||p.district===dist)&&(!q||(p.name+' '+p.district).toLocaleLowerCase('ru').includes(q))&&(!issues||p.issues.length||p.duplicates.length));
 $('nVisible').textContent=filtered.length.toLocaleString('ru-RU');}
function refresh(){applyFilters();if(isOffline)drawOffline();else if(leafletMap)drawLeaflet()}
['search','quality','district','onlyIssues'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',refresh));
$('reset').onclick=()=>{$('search').value='';$('quality').value='all';$('district').value='all';$('onlyIssues').checked=false;filterCats.clear();CATINFO.forEach(c=>filterCats.add(c[0]));$('categories').querySelectorAll('input').forEach(x=>x.checked=true);refresh();if(leafletMap)leafletMap.setView([55.75,37.62],11);else{svgScale=1;svgCenter=[37.63,55.77];drawOffline()}};
$('onlyCenter').onclick=()=>{ $('district').value='all'; filterCats.size||CATINFO.forEach(c=>filterCats.add(c[0])); $('search').value='';filtered=POINTS.filter(p=>centerNames.has(p.district)&&filterCats.has(p.category)&&($('quality').value==='all'||p.quality===$('quality').value));$('nVisible').textContent=filtered.length.toLocaleString('ru-RU');if(leafletMap&&!isOffline){drawLeaflet();leafletMap.setView([55.751,37.618],12)}else{svgScale=4;svgCenter=[37.618,55.751];drawOffline()} };
$('allcity').onclick=()=>$('reset').click();
function popText(p){return `<b>${esc(p.name)}</b><br>${esc(p.district)} · ${esc(p.quality)}<br><a href="#" onclick="return false">Нажмите маркер для карточки</a>`}
function drawLeaflet(){if(!leafletMap)return;if(markerLayer){markerLayer.clearLayers()}else{markerLayer=L.markerClusterGroup({chunkedLoading:true,maxClusterRadius:48,spiderfyOnMaxZoom:true,showCoverageOnHover:false});leafletMap.addLayer(markerLayer)}
 filtered.forEach(p=>{const col=CATCOL[p.category]||'#9ca3af';const marker=L.marker([p.lat,p.lon],{icon:L.divIcon({className:'',iconSize:[13,13],iconAnchor:[7,7],html:`<div class="pmarker" style="background:${col}"></div>`})});marker.bindTooltip(esc(p.name));marker.on('click',()=>showDetail(p));markerLayer.addLayer(marker)});
 $('displayLabel').textContent='Кластеризация Leaflet + OSM';}
const plot=$('plot');const X=1100,Y=800;
const wlon=1.45, hlat=.64;
// Equirectangular coordinate plot with longitude compressed by cos(latitude)
function coords(lon,lat){let px=(lon-svgCenter[0])*(X/wlon)*svgScale+X/2;let py=(svgCenter[1]-lat)*(Y/hlat)*svgScale+Y/2;return [px,py]}
function inverse(x,y){return [svgCenter[0]+(x-X/2)*wlon/(X*svgScale),svgCenter[1]-(y-Y/2)*hlat/(Y*svgScale)]}
function svgElement(n,attrs={},str=''){let el=document.createElementNS('http://www.w3.org/2000/svg',n);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));if(str)el.textContent=str;return el}
function drawOffline(){isOffline=true;$('offline').style.display='block';$('leaflet').style.display='none';plot.replaceChildren();let bg=svgElement('rect',{x:0,y:0,width:X,height:Y,fill:'transparent'});plot.append(bg);
 let grid=svgElement('g',{'stroke':'#354260','stroke-width':1});for(let x=0;x<=X;x+=100){grid.append(svgElement('line',{x1:x,y1:0,x2:x,y2:Y,'stroke-opacity':.3}))}for(let y=0;y<=Y;y+=100){grid.append(svgElement('line',{x1:0,y1:y,x2:X,y2:y,'stroke-opacity':.3}))}plot.append(grid);
 const title=svgElement('text',{x:27,y:42,fill:'#c3b8ff','font-size':21,'font-weight':700},'МОСКВА · ГЕОГРАФИЯ POI');plot.append(title);
 let buckets=new Map(),cell=svgScale>5?14:svgScale>2?24:svgScale>1.3?36:49;
 filtered.forEach(p=>{let [x,y]=coords(p.lon,p.lat);if(x<-30||x>X+30||y<-30||y>Y+30)return;let k=Math.floor(x/cell)+','+Math.floor(y/cell),b=buckets.get(k);if(!b){b={x:0,y:0,items:[],cats:{}};buckets.set(k,b)}b.x+=x;b.y+=y;b.items.push(p);b.cats[p.category]=(b.cats[p.category]||0)+1});
 for(let b of buckets.values()){let len=b.items.length,x=b.x/len,y=b.y/len,dominant=Object.entries(b.cats).sort((a,b)=>b[1]-a[1])[0][0];if(len==1){const p=b.items[0];const circle=svgElement('circle',{cx:x,cy:y,r:5.2,fill:CATCOL[p.category],stroke:'#f5f3ff','stroke-width':.8,opacity:.88});circle.style.cursor='pointer';circle.addEventListener('click',()=>showDetail(p));circle.append(svgElement('title',{},p.name));plot.append(circle)}else{let r=Math.min(27,10+Math.sqrt(len)*1.6);let g=svgElement('g');const circ=svgElement('circle',{cx:x,cy:y,r,fill:'#6e5bb1',stroke:'#c4b5fd','stroke-width':2,opacity:.95});g.append(circ);g.append(svgElement('text',{x,y:y+4.2,'text-anchor':'middle',fill:'white','font-size':Math.min(15,Math.max(11,r*.78)),'font-weight':800},String(len)));g.style.cursor='zoom-in';g.addEventListener('click',()=>{svgCenter=inverse(x,y);svgScale=Math.min(svgScale*2.1,28);drawOffline()});g.append(svgElement('title',{},`${len} объектов, нажмите для приближения`));plot.append(g)}}
 $('displayLabel').textContent='Автономная координатная карта · кластеры';}
plot.addEventListener('wheel',e=>{e.preventDefault();const rect=plot.getBoundingClientRect(),x=(e.clientX-rect.left)/rect.width*X,y=(e.clientY-rect.top)/rect.height*Y,pt=inverse(x,y);const s=Math.max(.7,Math.min(36,svgScale*(e.deltaY>0?.82:1.22)));if(s===svgScale)return;svgScale=s;svgCenter=[pt[0]-(x-X/2)*wlon/(X*svgScale),pt[1]+(y-Y/2)*hlat/(Y*svgScale)];drawOffline()},{passive:false});
plot.addEventListener('pointerdown',e=>{svgDragging=true;svgPrev=[e.clientX,e.clientY];plot.setPointerCapture(e.pointerId)});plot.addEventListener('pointermove',e=>{if(!svgDragging||!svgPrev)return;let rect=plot.getBoundingClientRect(),dx=(e.clientX-svgPrev[0])*X/rect.width,dy=(e.clientY-svgPrev[1])*Y/rect.height;svgCenter[0]-=dx*wlon/(X*svgScale);svgCenter[1]+=dy*hlat/(Y*svgScale);svgPrev=[e.clientX,e.clientY];drawOffline()});plot.addEventListener('pointerup',()=>{svgDragging=false;svgPrev=null});plot.addEventListener('pointercancel',()=>{svgDragging=false;svgPrev=null});
function init(){applyFilters();if(typeof window.L!=='undefined'&&typeof L.markerClusterGroup==='function'){
 leafletMap=L.map('leaflet',{preferCanvas:true}).setView([55.751,37.618],11);
 L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,minZoom:8,attribution:'© OpenStreetMap contributors (ODbL)'}).addTo(leafletMap);
 leafletMap.on('tileerror',()=>{ /* Browser may block network; plot fallback remains accessible using offline button */ });drawLeaflet();
 const btn=L.control({position:'bottomright'});btn.onAdd=()=>{const b=L.DomUtil.create('button');b.textContent='Офлайн-вид';b.style.cssText='padding:8px;background:#221d44;color:#fff;cursor:pointer';L.DomEvent.disableClickPropagation(b);b.onclick=()=>{isOffline=true;refresh()};return b};btn.addTo(leafletMap);
 }else{isOffline=true;drawOffline()}}
init();
// The offline real-coordinate map is initialized BEFORE any external CDN call.
// Optional Leaflet load can enhance the display, but cannot block first paint.
let leafletLoading=false;
function loadLeafletOpt(){if(leafletMap){isOffline=false;$('offline').style.display='none';$('leaflet').style.display='block';leafletMap.invalidateSize();drawLeaflet();return}
if(leafletLoading)return;leafletLoading=true;
for(const href of ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.css','https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css','https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css']){let x=document.createElement('link');x.rel='stylesheet';x.href=href;document.head.append(x)}
const a=document.createElement('script');a.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';a.async=true;a.onload=()=>{const b=document.createElement('script');b.src='https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js';b.async=true;b.onload=()=>{isOffline=false;$('offline').style.display='none';$('leaflet').style.display='block';init()};b.onerror=()=>{leafletLoading=false};document.head.append(b)};a.onerror=()=>{leafletLoading=false};document.head.append(a);
}
$('streetview').onclick=loadLeafletOpt;
// A successful background load will switch to street tiles, otherwise offline remains usable.
loadLeafletOpt();</script>
</body></html>'''
    # Scripts with defer must appear before init. Move CDN scripts before app JS
    html=html.replace('__TITLE__',title).replace('__DISCLAIMER__',disclaimer).replace('__POINTS__',jsdata).replace('__CATS__',catdata)
    (ART/filename).write_text(html,encoding='utf-8')
    print('HTML',filename,len(mapped),len(html))

create_html('tripwise-moscow-coverage.html')
create_html('tripwise-moscow-verified.html',center_only=True)

# PNG: all points from REAL source, color by category. Never draw synthetic streets.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
plt.rcParams['font.family']='DejaVu Sans'
fig=plt.figure(figsize=(17,10),facecolor='#0d1124',dpi=150)
ax=fig.add_axes([.075,.095,.67,.77],facecolor='#141d36')
ax.set_aspect(1/np.cos(np.deg2rad(55.75))*1.0) # appearance correction for lon/lat
for cat,label,color in CATS:
    pp=[p for p in pois if p['primary_category']==cat]
    if pp:ax.scatter([x['lon'] for x in pp],[x['lat'] for x in pp],s=17,c=color,alpha=.72,edgecolors='none',label=label,rasterized=True)
# Outliers C receive outline, no invented map geometry.
review=[p for p in pois if p['quality_level']=='C']
ax.scatter([p['lon'] for p in review],[p['lat'] for p in review],s=60,facecolors='none',edgecolors='#f9df85',linewidths=.75,alpha=.75,label='Требует проверки')
ax.set_xlim(36.98,38.0);ax.set_ylim(55.49,56.05)
ax.grid(True,color='#d4ddff',alpha=.12,linestyle='--',linewidth=.6)
for v in ax.spines.values():v.set_color('#404b6e')
ax.tick_params(colors='#a9b5d5',labelsize=10)
ax.set_xlabel('Долгота °E',fontsize=11,color='#a9b5d5',labelpad=10)
ax.set_ylabel('Широта °N',fontsize=11,color='#a9b5d5',labelpad=10)
fig.text(.075,.947,'TRIPWISE   /   DATA ATLAS',fontsize=10,color='#b7a6ff',weight='bold')
fig.text(.075,.905,'Москва: реальный культурный слой',fontsize=24,color='#ffffff',weight='bold')
fig.text(.075,.876,'1 исходная OSM-производная карта  ·  124 района старой Москвы  ·  19 ноября 2026',fontsize=10.5,color='#b7c4e3')
fig.text(.785,.82,f"{stat['unique_total']:,}".replace(',',' '),fontsize=38,color='#ffffff',weight='bold')
fig.text(.785,.787,'уникальных POI',fontsize=12,color='#b1bad9')
fig.text(.785,.706,f"{stat['quality_counts']['B']:,}".replace(',',' '),fontsize=26,color='#a7f3d0',weight='bold')
fig.text(.785,.674,'уровень B · 1 источник',fontsize=11,color='#adb8d6')
fig.text(.785,.59,f"{stat['central_okrug_unique']:,}".replace(',',' '),fontsize=26,color='#d6c3ff',weight='bold')
fig.text(.785,.56,'в пределах ЦАО',fontsize=11,color='#adb8d6')
fig.text(.785,.48,'ПО КАТЕГОРИЯМ',fontsize=10,color='#ffffff',weight='bold')
for i,(c,label,col) in enumerate(CATS):
    y=.446-i*.038
    fig.text(.785,y,'●',fontsize=13,color=col)
    fig.text(.808,y,label,fontsize=10,color='#dfe6ff')
    fig.text(.965,y,str(stat['category_counts'][c]),fontsize=10,color='#ffffff',ha='right',weight='bold')
fig.text(.08,.035,'© OpenStreetMap contributors (ODbL 1.0)  ·  Source: ArtemIlinn / HSE, 2026',fontsize=9,color='#93a2c3')
fig.text(.985,.085,'Внимание: неполный набор — без кафе, магазинов, ТиНАО; нет проверки актуальности или дорог.',ha='right',fontsize=9,color='#f5c8cd',rotation=90)
fig.savefig(ART/'moscow-poi-coverage.png',dpi=160,bbox_inches='tight',facecolor='#0d1124')
plt.close(fig)
print('PNG ready')
