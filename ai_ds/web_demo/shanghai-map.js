/* Vanilla interactive map. Road geometry is requested only for displayed legs. */
(function(root){
 'use strict';
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const merc=(lon,lat,z)=>{const n=256*2**z,r=lat*Math.PI/180;return [(lon+180)/360*n,(1-Math.asinh(Math.tan(r))/Math.PI)/2*n];};
 const unmerc=(x,y,z)=>{const n=256*2**z;return [x/n*360-180,Math.atan(Math.sinh(Math.PI*(1-2*y/n)))*180/Math.PI];};
 const coordinate=p=>[Number(p.lon),Number(p.lat)];
 const validCoord=p=>Array.isArray(p)&&p.length>=2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=85;
 function create(settings){
  const box=settings.container,svg=settings.svg,tiles=settings.tiles,background=settings.background;
  const placeName=p=>settings.getName?settings.getName(p):p.name;
  let day=null,byId=new Map(),stops=[],legs=[],zoom=13,center=[121.48,31.23],online=false;
  let selected=-1,epoch=0,planEpoch=0,calls=0,loaded=0,failed=0,lastRequest=0,controller=null,drag=null,drawPending=false;
  let tileGeneration='',tileTimeout=null,tilesTimedOut=false;const tileNodes=new Map(),roadCache=new Map();
  const localProxy=!settings.directTiles&&location.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(location.hostname);
  let proxyState=localProxy?'checking':'external';
  const status=text=>settings.onStatus?.(text);
  const controls=document.createElement('div');controls.className='tw-map-controls';
  controls.innerHTML='<button type="button" data-action="in" aria-label="Приблизить карту">+</button><button type="button" data-action="out" aria-label="Отдалить карту">−</button><button type="button" data-action="fit" aria-label="Показать весь маршрут">⌖</button>';
  box.appendChild(controls);
  const popup=document.createElement('div');popup.className='tw-map-popup';popup.hidden=true;popup.setAttribute('role','status');box.appendChild(popup);
  const dimensions=()=>({w:Math.max(240,box.clientWidth),h:Math.max(240,box.clientHeight)});
  function projection(){const {w,h}=dimensions(),c=merc(...center,zoom);return p=>{const q=merc(p[0],p[1],zoom);return [q[0]-c[0]+w/2,q[1]-c[1]+h/2];};}
  function allCoordinates(){let points=stops.map(coordinate);for(const leg of legs)for(const seg of leg.geometry_segments||[])for(const c of seg.coordinates||[])if(validCoord(c))points.push(c);return points;}
  function fit(points=allCoordinates()){if(!Array.isArray(points))points=allCoordinates();if(!points.length){center=[121.48,31.23];zoom=12;draw();return;}
   const {w,h}=dimensions();for(zoom=17;zoom>7;zoom--){const c=points.map(p=>merc(...p,zoom)),xs=c.map(p=>p[0]),ys=c.map(p=>p[1]);if(Math.max(...xs)-Math.min(...xs)<w-80&&Math.max(...ys)-Math.min(...ys)<h-100)break;}
   const c=points.map(p=>merc(...p,zoom)),xs=c.map(p=>p[0]),ys=c.map(p=>p[1]);center=unmerc((Math.min(...xs)+Math.max(...xs))/2,(Math.min(...ys)+Math.max(...ys))/2,zoom);draw();
  }
  function roadKey(a,b,mode,via){return [mode,...a.map(n=>n.toFixed(6)),...b.map(n=>n.toFixed(6)),...(via||[]).map(p=>p.map(n=>n.toFixed(6)).join(','))].join('|');}
  function linePath(coords,project){return coords.filter(validCoord).map(c=>project(c).map(n=>n.toFixed(2)).join(',')).join(' ');}
  function streetRequest(leg){const from=byId.get(leg.from_poi_id),to=byId.get(leg.to_poi_id);if(!from||!to)return null;return {a:coordinate(from),b:coordinate(to),mode:leg.mode==='taxi'?'car':'foot'};}
  function segments(leg){if(Array.isArray(leg.geometry_segments)&&leg.geometry_segments.length)return leg.geometry_segments;
   const request=streetRequest(leg);return request?[{mode:leg.mode,coordinates:[request.a,request.b],source:leg.source||'estimate',synthetic:true}]:[];
  }
  function segmentRoad(seg,leg){if(leg.use_plan_geometry)return null;if(seg.mode==='bus'&&seg.coordinates?.length>=2)return {a:seg.coordinates[0],b:seg.coordinates[seg.coordinates.length-1],via:seg.coordinates,mode:'car',synthetic_bus:true};if(seg.mode==='walk'||seg.mode==='taxi'||seg.mode==='car'){
    const c=seg.coordinates;if(c?.length>=2)return {a:c[0],b:c[c.length-1],mode:seg.mode==='walk'?'foot':'car'};
   }if(!leg.geometry_segments&&['walk','taxi'].includes(leg.mode))return streetRequest(leg);return null;
  }
  function describeStep(step){const names={left:'налево',right:'направо',straight:'прямо','slight left':'плавно налево','slight right':'плавно направо','sharp left':'резко налево','sharp right':'резко направо',uturn:'развернитесь'};
   const manoeuvre=step.maneuver||{},name=step.name?' по '+step.name:'',distance=Math.round(Number(step.distance)||0);
   if(manoeuvre.type==='arrive')return 'Прибытие к расчётной точке (вход отдельно не проверен).';
   if(manoeuvre.type==='depart')return 'Начните движение'+name+(distance?' — '+distance+' м':'');
   return (manoeuvre.type==='roundabout'?'На круговом движении, съезд '+(manoeuvre.exit||'по указателю'):('Двигайтесь '+(names[manoeuvre.modifier]||'по маршруту')))+name+(distance?' — '+distance+' м':'');
  }
  function transitText(step){if(typeof step==='string')return step;
   if(step.text)return step.text;
   const mode=step.mode||step.type,line=step.line_name||step.line_id||step.name||'';
   if(mode==='metro'||mode==='bus')return (mode==='bus'?'Синтетический автобус ':'Метро ')+line+': '+(step.from_name||step.from_station||'посадка')+' → '+(step.to_name||step.to_station||'выход')+(step.direction?' · направление '+step.direction:'')+(step.stop_count!=null?' · остановок '+step.stop_count:'');
   if(mode==='transfer')return 'Пересадка: '+(step.from_name||'')+' → '+(step.to_name||'')+(line?' · '+line:'');
   if(mode==='walk')return 'Пешком: '+(step.from_name||'доступ к станции')+' → '+(step.to_name||'место назначения')+' (время оценочное; путь уточняется отдельно).';
   return [line,step.from_name,step.to_name].filter(Boolean).join(' → ');
  }
  function instructions(index){const leg=legs[index];if(!leg)return '';
   let html='<strong>Переход '+(index+1)+'</strong><p>В плане '+Math.round(leg.planned_minutes||leg.end_min-leg.start_min)+' мин. Время карты не заменяет расписание автоматически.</p>';
   if(leg.instructions?.length)html+='<ol>'+leg.instructions.map(s=>'<li>'+esc(transitText(s))+'</li>').join('')+'</ol>';
   if(leg.source==='SYNTHETIC_BUS'||leg.geometry_segments?.some(s=>s.mode==='bus'))html+='<p class="tw-map-caution">DEMO-BUS — вымышленные автобусные маршруты. Не использовать как инструкции для настоящей поездки.</p>';
   let streetFound=false,streetMinutes=0;for(const seg of segments(leg)){const req=segmentRoad(seg,leg);if(!req)continue;const road=roadCache.get(roadKey(req.a,req.b,req.mode,req.via));if(road?.coordinates){streetFound=true;if(!req.synthetic_bus)streetMinutes+=Math.ceil(road.duration_seconds/60);html+='<p><b>'+(req.synthetic_bus?'Учебный автобус: форма пути по автомобильным дорогам':req.mode==='foot'?'Пешком по улицам':'По автомобильной дороге')+'</b> · автомобильный/пеший расчёт провайдера '+Math.ceil(road.duration_seconds/60)+' мин, '+Math.round(road.distance_meters)+' м'+(req.synthetic_bus?'; это не действующий рейс и не проверка автобусной проходимости':'')+'</p><ol>'+road.steps.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ol>';}}
   if(streetMinutes>leg.planned_minutes)html+='<p class="tw-map-caution"><b>Уличный расчёт занимает больше времени, чем отведено в плане.</b> Предусмотрите дополнительный запас.</p>';
   if(leg.use_plan_geometry&&leg.geometry?.type==='LineString')html+='<p>Линия и время рассчитаны по дорожной сети OpenStreetMap. Время включает резерв на задержки.</p>';
   else if(!streetFound)html+='<p>Пошаговые указания пока недоступны. Пунктир — условная связь, не проверенный путь.</p>';
   return html;
  }
  function selectLeg(index){selected=index;popup.hidden=true;settings.onSelect?.(instructions(index),index);
   const leg=legs[index];if(!leg){draw();return;}
   const points=segments(leg).flatMap(seg=>{const req=segmentRoad(seg,leg),road=req&&roadCache.get(roadKey(req.a,req.b,req.mode,req.via));return road?.coordinates||seg.coordinates||[];}).filter(validCoord);
   if(points.length)fit(points);else draw();
  }
  function selectStop(index){const p=stops[index];if(!p)return;center=coordinate(p);draw();popup.innerHTML='<b>'+esc(placeName(p))+'</b><br><small>'+esc(p.area||'Шанхай')+' · координата места, вход не проверен</small>';popup.hidden=false;}
  function tileStatus(){const mode=settings.mode;
   if(online&&proxyState==='old'){if(mode)mode.textContent='Нужен перезапуск сервера';return 'Локальный сервер устарел и не поддерживает карту. Запустите START_SHANGHAI_MAP.bat и откройте новый адрес, который он откроет. Точки маршрута доступны.';}
   if(online&&proxyState==='checking'){if(mode)mode.textContent='Проверка локальной карты…';return 'Проверяется локальный сервер карты.';}
   const label=loaded?'OpenStreetMap'+(failed?' · частично':''):(!online?'Локальная схема':failed||tilesTimedOut?'Без подложки · точки доступны':'Загрузка OSM…');
   if(mode){mode.textContent=label;mode.classList.toggle('online',loaded>0);}
   return loaded?'Подложка OpenStreetMap загружена.':!online?'Онлайн-подложка выключена.':failed||tilesTimedOut?'Подложка не загрузилась. Точки и маршруты остаются доступны на нейтральном фоне.':'Загружается подложка OpenStreetMap.';
  }
  function updateStatus(){status(tileStatus()+' Масштаб '+zoom+'. Перетащите карту; +/− меняют масштаб. Сплошные линии — полученные дороги, пунктир — схема.');}
  function drawTiles(w,h,c){const needed=new Set(),gen=[zoom,Math.round(c[0]/256),Math.round(c[1]/256),w,h,online].join('|');
   if(gen!==tileGeneration){tileGeneration=gen;loaded=failed=0;tilesTimedOut=false;clearTimeout(tileTimeout);if(online)tileTimeout=setTimeout(()=>{tilesTimedOut=true;updateStatus();},7000);}
   if(online&&proxyState!=='checking'&&proxyState!=='old'){const n=2**zoom,x0=Math.floor((c[0]-w/2)/256),x1=Math.floor((c[0]+w/2)/256),y0=Math.floor((c[1]-h/2)/256),y1=Math.floor((c[1]+h/2)/256);
    for(let x=x0;x<=x1;x++)for(let y=y0;y<=y1;y++){if(y<0||y>=n)continue;const col=((x%n)+n)%n,key=zoom+'/'+col+'/'+y;needed.add(key);let img=tileNodes.get(key);
     if(!img){img=new Image();img.alt='';img.draggable=false;img.decoding='async';img.referrerPolicy='strict-origin-when-cross-origin';img.dataset.tile=key;img.addEventListener('load',()=>{loaded++;updateStatus();});img.addEventListener('error',()=>{failed++;img.style.opacity='0';updateStatus();});img.src=localProxy?'/tiles/'+key+'.png':'https://tile.openstreetmap.org/'+key+'.png';tileNodes.set(key,img);tiles.appendChild(img);}
     img.style.left=(x*256-c[0]+w/2)+'px';img.style.top=(y*256-c[1]+h/2)+'px';
    }}
   for(const [key,img] of tileNodes)if(!needed.has(key)){img.remove();tileNodes.delete(key);}updateStatus();
  }
  function draw(){drawPending=false;const {w,h}=dimensions(),project=projection(),c=merc(...center,zoom);
   background.setAttribute('viewBox',`0 0 ${w} ${h}`);background.innerHTML=`<rect width="${w}" height="${h}" fill="#edf1f2"/><text x="12" y="${h-18}" fill="#73858a" font-size="11">Координаты WGS84</text>`;
   drawTiles(w,h,c);svg.setAttribute('viewBox',`0 0 ${w} ${h}`);let html='';
   legs.forEach((leg,index)=>{for(const seg of segments(leg)){const req=segmentRoad(seg,leg),road=req&&roadCache.get(roadKey(req.a,req.b,req.mode,req.via)),coords=road?.coordinates||seg.coordinates||[];
    const color=({metro:'#287fa5',mcc:'#ce4242',mcd:'#b55b21',tram:'#286b55',bus:'#257693'})[seg.network]||
      (seg.mode==='bus'?'#bd5555':seg.mode==='metro'?'#287fa5':leg.mode==='taxi'?'#c18b38':'#6b51ba');
    const hasStreetGeometry=Boolean(road?.coordinates||seg.verified);
    const label=seg.network?(seg.verified?'Геометрия маршрута OSM':'Схема между остановками'):seg.mode==='bus'?'Вымышленный автобус':seg.mode==='metro'?'Топология станций метро, не точный тоннель':hasStreetGeometry?'Расчёт по уличной сети':'Приблизительная связь';
    html+=`<polyline data-leg="${index}" points="${linePath(coords,project)}" fill="none" stroke="${color}" stroke-width="${selected===index?7:4}" stroke-dasharray="${hasStreetGeometry?'':'8 5'}" stroke-linejoin="round" stroke-linecap="round" style="pointer-events:stroke;cursor:pointer"><title>${esc(label)} · нажмите для инструкции</title></polyline>`;
   }});
   const stations=new Map();
   legs.forEach((leg,index)=>{for(const station of leg.stations||[]){
     if(!validCoord(station.coordinates))continue;
     const key=station.coordinates.join(',');
     if(!stations.has(key)||station.transfer)stations.set(key,{...station,index});
   }});
   for(const station of stations.values()){
     const [x,y]=project(station.coordinates);
     const label=(station.transfer?'Пересадка · ':'')+(station.name||'Остановка');
     html+=`<circle data-leg="${station.index}" cx="${x}" cy="${y}" r="${station.transfer?6:4}" fill="white" stroke="#206379" stroke-width="${station.transfer?3:2}" style="pointer-events:all;cursor:pointer"><title>${esc(label)}</title></circle>`;
   }
   stops.forEach((p,index)=>{const [x,y]=project(coordinate(p));html+=`<g data-stop="${index}" role="button" tabindex="0" aria-label="${esc(placeName(p))}" style="pointer-events:all;cursor:pointer"><title>${esc(placeName(p))}</title><circle cx="${x}" cy="${y}" r="13" fill="${p.kind==='restaurant'?'#db8c33':'#5b40b0'}" stroke="white" stroke-width="3"/><text x="${x}" y="${y+4}" fill="white" text-anchor="middle" font-size="12" font-weight="700">${index+1}</text></g>`;});
   if(!stops.length)html=`<text x="${w/2}" y="${h/2}" text-anchor="middle" fill="#657881">Нет точек маршрута</text>`;
   svg.innerHTML=html;
   svg.querySelectorAll('[data-stop]').forEach(g=>{const show=()=>selectStop(Number(g.dataset.stop));g.addEventListener('click',show);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();show();}});});
   svg.querySelectorAll('[data-leg]').forEach(g=>g.addEventListener('click',()=>selectLeg(Number(g.dataset.leg))));
  }
  function requestDraw(){if(!drawPending){drawPending=true;requestAnimationFrame(draw);}}
  function zoomTo(delta,anchor){const next=Math.max(7,Math.min(19,zoom+delta));if(next===zoom)return;const {w,h}=dimensions();if(anchor){const c=merc(...center,zoom),world=unmerc(c[0]+anchor[0]-w/2,c[1]+anchor[1]-h/2,zoom),n=merc(...world,next);center=unmerc(n[0]-anchor[0]+w/2,n[1]-anchor[1]+h/2,next);}zoom=next;popup.hidden=true;draw();}
  controls.addEventListener('click',e=>{e.stopPropagation();const a=e.target.dataset.action;if(a==='fit')fit();else if(a)zoomTo(a==='in'?1:-1);});
  box.addEventListener('wheel',e=>{e.preventDefault();const rect=box.getBoundingClientRect();zoomTo(e.deltaY<0?1:-1,[e.clientX-rect.left,e.clientY-rect.top]);},{passive:false});
  box.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('button,a,.tw-map-popup'))return;drag={x:e.clientX,y:e.clientY,center:merc(...center,zoom),moved:false};});
  box.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>4){if(!drag.moved)box.setPointerCapture(e.pointerId);drag.moved=true;popup.hidden=true;center=unmerc(drag.center[0]-dx,drag.center[1]-dy,zoom);requestDraw();}});
  box.addEventListener('pointerup',()=>{if(drag?.moved)draw();drag=null;});box.addEventListener('pointercancel',()=>{drag=null;});
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>requestDraw()).observe(box);
  function probeProxy(){if(!localProxy)return;proxyState='checking';fetch('/health',{cache:'no-store',signal:AbortSignal.timeout(4000)}).then(r=>r.ok?r.json():null).then(info=>{proxyState=info?.service==='tripwise-shanghai'&&info.tile_proxy===true?'ready':'old';tileGeneration='';draw();}).catch(()=>{proxyState='old';draw();});}
  probeProxy();
  function beginPlan(){planEpoch++;epoch++;calls=0;controller?.abort();controller=null;selected=-1;popup.hidden=true;}
  async function loadRoads(localEpoch,localPlan){
   if(!online||settings.fetchRoads===false)return;const requests=[];
   for(const leg of legs)for(const seg of segments(leg)){const req=segmentRoad(seg,leg);if(req&&validCoord(req.a)&&validCoord(req.b))requests.push(req);}
   for(const req of requests){if(localEpoch!==epoch||localPlan!==planEpoch)return;const key=roadKey(req.a,req.b,req.mode,req.via);if(roadCache.has(key))continue;if(calls>=20){settings.onNetworkStatus?.('Лимит 20 дорожных запросов для этого построения исчерпан; остальные связи остаются схемой.');return;}
    await new Promise(resolve=>setTimeout(resolve,Math.max(0,1100-(Date.now()-lastRequest))));if(localEpoch!==epoch||localPlan!==planEpoch)return;
    calls++;lastRequest=Date.now();const requestController=new AbortController();controller=requestController;const timeout=setTimeout(()=>requestController.abort(),9000);
    try{const profile=req.mode==='foot'?'routed-foot':'routed-car',locations=(req.via||[req.a,req.b]).map(p=>p.join(',')).join(';'),url='https://routing.openstreetmap.de/'+profile+'/route/v1/driving/'+locations+'?overview=full&geometries=geojson&steps=true&generate_hints=false';
     settings.onNetworkStatus?.('Уточняем выбранные улицы: запрос '+calls+' / 20. Плановое расписание остаётся оценочным.');
     const response=await fetch(url,{signal:requestController.signal});if(!response.ok)throw Error('HTTP '+response.status);const body=await response.json(),route=body.routes?.[0];
     if(body.code!=='Ok'||route?.geometry?.type!=='LineString'||!Array.isArray(route.geometry.coordinates)||route.geometry.coordinates.length<2||!route.geometry.coordinates.every(validCoord)||!Number.isFinite(route.duration)||route.duration<0||!Number.isFinite(route.distance)||route.distance<0)throw Error('Нет корректного маршрута');
     roadCache.set(key,{request:req,coordinates:route.geometry.coordinates,duration_seconds:route.duration,distance_meters:route.distance,steps:(route.legs||[]).flatMap(l=>l.steps||[]).map(describeStep)});
     if(localEpoch===epoch){draw();if(selected>=0)settings.onSelect?.(instructions(selected),selected);}
    }catch(error){if(localEpoch!==epoch||localPlan!==planEpoch)return;roadCache.set(key,{error:String(error.message||error)});settings.onNetworkStatus?.('Дорожный сервис не ответил для одной из связей. Она показана пунктиром; автоматического повтора нет.');}
    finally{clearTimeout(timeout);if(controller===requestController)controller=null;}
   }
   if(localEpoch===epoch)settings.onNetworkStatus?.('Уточнение выбранных улиц завершено. Нажмите линию или переход для пошаговой инструкции. Время метро и автобусов оценочное.');
  }
  function setDay(value,idMap,settingsForDay={}){epoch++;controller?.abort();controller=null;day=value;byId=idMap;online=settingsForDay.online!==false;
   stops=(day.events||[]).filter(e=>e.type==='visit'||e.type==='meal').map(e=>byId.get(e.poi_id)).filter(Boolean);legs=(day.events||[]).filter(e=>e.type==='travel');selected=-1;popup.hidden=true;fit();
   settings.onSelect?.('<p>Нажмите переход в расписании или линию карты: появятся станции, пересадки и доступные инструкции по улицам.</p>',-1);loadRoads(epoch,planEpoch);
  }
  function getOsmTables(){
   const at=new Map(),xy=p=>p.map(n=>n.toFixed(6)).join(',');for(const p of byId.values()){const key=xy(coordinate(p));if(!at.has(key))at.set(key,[]);at.get(key).push(p.id);}
   const result={};for(const mode of ['foot','car']){const pairs=[];for(const value of roadCache.values()){if(!value.request||value.request.synthetic_bus||value.request.mode!==mode||!value.coordinates)continue;const starts=at.get(xy(value.request.a))||[],ends=at.get(xy(value.request.b))||[];for(const from of starts)for(const to of ends)pairs.push({from,to,seconds:value.duration_seconds,metres:value.distance_meters});}if(!pairs.length)continue;const ids=[...new Set(pairs.flatMap(p=>[p.from,p.to]))],index=new Map(ids.map((id,i)=>[id,i])),durations=ids.map(()=>ids.map(()=>null)),distances=ids.map(()=>ids.map(()=>null));for(const p of pairs){durations[index.get(p.from)][index.get(p.to)]=p.seconds;distances[index.get(p.from)][index.get(p.to)]=p.metres;}result[mode]={ids,durations,distances};}return result;
  }
  return {setDay,beginPlan,fit,selectLeg,selectStop,getOsmTables,retry(){for(const [key,value] of roadCache)if(value.error)roadCache.delete(key);tileGeneration='';for(const img of tileNodes.values())img.remove();tileNodes.clear();if(proxyState==='old')probeProxy();draw();epoch++;loadRoads(epoch,planEpoch);},debug(){return {center:center.slice(),zoom,request_count:calls,stop_count:stops.length,leg_count:legs.length,tile_proxy:proxyState};}};
 }
 root.TripWiseMap={create};
})(globalThis);
