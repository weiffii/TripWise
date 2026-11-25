(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const svg = byId('atlas-svg'), choices = byId('atlas-country-choices'), list = byId('atlas-places');
  const worldBounds = [-159, -85, 159, 57];
  const countryMeta = {RU:{name:'Россия',color:'#d3a6bc',label:[93,62],center:[96,62]},
    CN:{name:'Китай',color:'#a7cbc4',label:[103,36],center:[104,35]}};
  const cityMeta = {
    moscow:{name:'Москва',country:'RU',color:'#854b94'},
    shanghai:{name:'Шанхай',country:'CN',color:'#9e5a35'},
  };
  let mode = 'countries', country = '', data, loading = false, loadedError = false;
  let cities = new Map(), countries = new Map(), base = [-165,-88,330,148], view = [...base], dragging = null;
  const svgNode = tag => document.createElementNS('http://www.w3.org/2000/svg',tag);
  const setView = box => {
    view = [...box];
    svg.setAttribute('viewBox',view.join(' '));
  };

  function bindTabs(ids, activate) {
    const tabs = ids.map(byId);
    tabs.forEach((tab,index) => {
      tab.addEventListener('click',()=>activate(index));
      tab.addEventListener('keydown',event=>{
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index;
        activate(next);
        tabs[next].focus();
      });
    });
    return selected => tabs.forEach((tab,index)=>{
      tab.setAttribute('aria-selected',String(index===selected));
      tab.tabIndex=index===selected?0:-1;
    });
  }

  async function loadMap() {
    if (data || loading) return;
    loading = true;
    loadedError = false;
    render();
    try {
      const response = await fetch('/assets/travel-atlas.json?v=1');
      if (!response.ok) throw Error();
      data = await response.json();
    } catch { loadedError = true; }
    finally { loading = false; render(); }
  }

  const mapTabs = bindTabs(['atlas-countries','atlas-cities'],index=>changeMode(index===1?'cities':'countries'));
  function changeMode(value, target = country) {
    mode = value;
    country = countries.has(target) ? target : countries.keys().next().value || '';
    mapTabs(mode==='cities'?1:0);
    byId('atlas-mode-panel').setAttribute('aria-labelledby',mode==='cities'?'atlas-cities':'atlas-countries');
    render();
  }

  function button(text,action) {
    const chip = document.createElement('button');
    chip.type = 'button'; chip.textContent=text; chip.onclick=action;
    return chip;
  }
  function outline(record,{key,meta,count,active=false,action}={}) {
    const path = svgNode('path');
    path.setAttribute('d',record.path);
    path.classList.add('atlas-outline');
    if (key) path.dataset.atlasShape=key;
    if (active) {
      path.classList.add('atlas-highlight');
      path.style.setProperty('--atlas-color',meta.color);
      const title = svgNode('title');
      title.textContent=meta.name+' · поездок: '+count;
      path.append(title);
      path.setAttribute('aria-label',title.textContent);
      if(action){
        path.setAttribute('role','button');
        path.setAttribute('tabindex','0');
        path.addEventListener('click',event=>{ if (!dragging?.moved && event.detail !== 0) action(); });
        path.addEventListener('keydown',event=>{
          if(event.key==='Enter'||event.key===' '){event.preventDefault();action();}
        });
      }
    }
    if (!active) {
      const palette = ['#e3cbe7','#cde4d8','#efd7bc','#cedfed','#e8d4c8','#d8d3ed','#d3e5e1'];
      const seed = [...(key || '')].reduce((sum,char)=>sum+char.charCodeAt(0),0);
      path.style.setProperty('--atlas-land-color',palette[seed % palette.length]);
    }
    svg.append(path);
  }
  function addLegend(key,meta,count,action) {
    const li = document.createElement('li');
    const row = button('',action);
    row.className='atlas-legend-row';
    row.dataset.atlasPlace=key;
    const dot = document.createElement('span');
    dot.className='atlas-dot'; dot.style.setProperty('--atlas-color',meta.color);
    dot.setAttribute('aria-hidden','true');
    const title=document.createElement('strong'), countLabel=document.createElement('small');
    title.textContent=meta.name; countLabel.textContent='Поездок: '+count;
    row.append(dot,title,countLabel); li.append(row); list.append(li);
  }
  const radians = Math.PI / 180;
  // Natural Earth: сглаживаем искажения плоской сетки широт и долгот.
  function worldPoint(lon, lat) {
    const phi = lat * radians, t = phi * phi, t2 = t * t, t3 = t * t2;
    const x = lon * (0.8707 - 0.131979*t + t2*(-0.013791 + t3*(0.003971*t - 0.001529*t2 - 0.001944)));
    const y = -lat * (1.007226 + t*(0.015085 + t2*(-0.044475 + 0.028874*t - 0.005916*t2)));
    return [x,y];
  }
  // Для страны сохраняем расстояния от её центра. Долготы по обе
  // стороны 180° сходятся естественно, включая дальневосточные острова.
  function countryProjector(code) {
    const [centralLon, centralLat] = countryMeta[code].center;
    const sinCenter = Math.sin(centralLat*radians), cosCenter = Math.cos(centralLat*radians);
    return (lon, lat) => {
      const d = (lon-centralLon)*radians, phi = lat*radians;
      const cosPhi = Math.cos(phi), sinPhi = Math.sin(phi);
      const angle = Math.acos(Math.max(-1,Math.min(1,sinCenter*sinPhi+cosCenter*cosPhi*Math.cos(d))));
      const scale = (angle < 1e-8 ? 1 : angle/Math.sin(angle))/radians;
      return [scale*cosPhi*Math.sin(d), -scale*(cosCenter*sinPhi-sinCenter*cosPhi*Math.cos(d))];
    };
  }
  const projectAssetPoint = (project,x,y) => project(x-180,90-y);
  const coordinatePair = /(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g;
  function projectRecord(record, project) {
    return {path:record.path.replace(coordinatePair, (_,x,y) => projectAssetPoint(project,Number(x),Number(y)).map(v=>v.toFixed(3)).join(','))};
  }
  function pathBounds(path) {
    const limits = [Infinity,Infinity,-Infinity,-Infinity];
    for (const match of path.matchAll(coordinatePair)) {
      const x=Number(match[1]), y=Number(match[2]);
      limits[0]=Math.min(limits[0],x); limits[1]=Math.min(limits[1],y);
      limits[2]=Math.max(limits[2],x); limits[3]=Math.max(limits[3],y);
    }
    return limits;
  }
  function fit([left,top,right,bottom], local) {
    const ratio = (svg.clientWidth || 900)/(svg.clientHeight || 520);
    const width = Math.max((right-left)*(local ? 1.09 : 1.025),(bottom-top)*ratio*1.13);
    const height = width/ratio;
    return [(left+right-width)/2,(top+bottom-height)/2,width,height];
  }
  function focusCity(key) {
    if (!data?.cities[key]) return;
    country=cityMeta[key].country;
    setView(base);
    byId('atlas-message').textContent = cityMeta[key].name + ' · поездок: ' + cities.get(key) + '. Город отмечен на карте страны.';
    svg.querySelectorAll('[data-atlas-city-marker]').forEach(marker=>marker.classList.toggle('is-focused',marker.dataset.atlasCityMarker===key));
  }
  function mapLabel(value,x,y,size,className) {
    const text=svgNode('text');text.textContent=value;text.setAttribute('x',x);text.setAttribute('y',y);text.setAttribute('font-size',size);text.setAttribute('class',className);text.setAttribute('pointer-events','none');svg.append(text);return text;
  }
  function cityMarker(key,scale,project) {
    const bounds=data.cities[key]?.bounds;if(!bounds)return;
    const [x,y]=projectAssetPoint(project,(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2);
    const group=svgNode('g');group.dataset.atlasCityMarker=key;group.setAttribute('role','button');group.setAttribute('tabindex','0');group.setAttribute('aria-label',cityMeta[key].name+' · поездок: '+cities.get(key));group.classList.add('atlas-city-marker');
    const halo=svgNode('circle');halo.setAttribute('cx',x);halo.setAttribute('cy',y);halo.setAttribute('r',scale*2);halo.setAttribute('class','atlas-pin-halo');
    const dot=svgNode('circle');dot.setAttribute('cx',x);dot.setAttribute('cy',y);dot.setAttribute('r',scale);dot.setAttribute('fill',cityMeta[key].color);dot.setAttribute('stroke','#fffdf9');dot.setAttribute('stroke-width',scale*.3);
    group.append(halo,dot);group.addEventListener('click',()=>focusCity(key));group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();focusCity(key);}});svg.append(group);
    mapLabel(cityMeta[key].name,x+scale*1.7,y-scale*.4,scale*2.2,'atlas-city-label');
  }
  function graticules(project) {
    const grid=svgNode('g');grid.setAttribute('class','atlas-graticules');grid.setAttribute('aria-hidden','true');
    function line(points) {
      const path=svgNode('path');path.setAttribute('d',`M${points.map(([lon,lat])=>project(lon,lat).join(',')).join('L')}`);grid.append(path);
    }
    for(let lon=-180;lon<=180;lon+=30)line(Array.from({length:31},(_,i)=>[lon,-60+i*5]));
    for(let lat=-60;lat<=90;lat+=30)line(Array.from({length:73},(_,i)=>[-180+i*5,lat]));
    svg.append(grid);
  }

  function render() {
    const isCity = mode==='cities';
    byId('atlas-legend-title').textContent = isCity ? 'Ваши города' : 'Ваши страны';
    choices.hidden=!isCity||!countries.size;
    choices.replaceChildren();
    list.replaceChildren();
    svg.replaceChildren();
    for(const [code] of countries) {
      const action=()=>changeMode('cities',code);
      if(isCity) {
        const chip=button(countryMeta[code].name,action);
        chip.dataset.atlasCountry=code;
        chip.setAttribute('aria-pressed',String(country===code));
        choices.append(chip);
      } else addLegend(code,countryMeta[code],countries.get(code),action);
    }
    const cityKeys = [...cities.keys()].filter(key=>cityMeta[key].country===country);
    if(isCity) for(const key of cityKeys) addLegend(key,cityMeta[key],cities.get(key),()=>focusCity(key));
    const message = byId('atlas-message');
    message.textContent = loadedError ? 'Не удалось загрузить фон карты.' :
      loading ? 'Загружаем карту…' : !countries.size ? 'Сохраните первый маршрут, и он появится на карте.' :
      isCity ? 'Города отмечены на географической карте страны. Выберите город в списке.' : 'Нажмите на цветную страну, чтобы увидеть её города.';
    byId('atlas-city-credit').hidden = !isCity || !cityKeys.length;
    if (!data) return;
    const local = isCity && country && data.world[country];
    const project = local ? countryProjector(country) : worldPoint;
    const selectedRecord = local ? projectRecord(data.world[country], project) : null;
    base = fit(selectedRecord ? pathBounds(selectedRecord.path) : worldBounds, Boolean(local));
    if (!isCity) graticules(project);
    for (const [code,record] of Object.entries(data.world)) {
      if (isCity && code !== country) continue;
      const count = countries.get(code);
      outline(selectedRecord || projectRecord(record,project),{key:code,meta:countryMeta[code],count,active:Boolean(count),action:()=>changeMode('cities',code)});
    }
    if (isCity) for (const key of cityKeys) {
      if(data.cities[key]) outline(projectRecord(data.cities[key],project),{key,meta:cityMeta[key],count:cities.get(key),active:true,action:()=>focusCity(key)});
    }
    const scale=base[2]/(svg.clientWidth||900)*5.6;
    for (const code of isCity ? (country ? [country] : []) : countries.keys()) {
      const meta = countryMeta[code];
      const [x,y] = project(...meta.label);
      mapLabel(meta.name, x, y, scale*(isCity ? 3 : 2.65), 'atlas-country-label').dataset.atlasLabel = code;
    }
    for(const key of isCity ? cityKeys : [])cityMarker(key,scale,project);
    setView(base);
  }

  function zoom(factor) {
    const width = Math.max(base[2]/12,Math.min(base[2]*2,view[2]*factor));
    const height = width * view[3]/view[2];
    setView([view[0]+(view[2]-width)/2,view[1]+(view[3]-height)/2,width,height]);
  }
  for(const control of document.querySelectorAll('[data-atlas-zoom]')) control.onclick=()=>{
    const action=control.dataset.atlasZoom;
    if(action==='reset')setView(base);
    else zoom(action==='in'?.7:1/.7);
  };
  svg.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;
    dragging={x:event.clientX,y:event.clientY,view:[...view],moved:false};
  });
  svg.addEventListener('pointermove',event=>{
    if(!dragging)return;
    const dx=event.clientX-dragging.x,dy=event.clientY-dragging.y;
    if(!dragging.moved&&Math.hypot(dx,dy)<5)return;
    if(!dragging.moved){svg.setPointerCapture(event.pointerId);dragging.moved=true;}
    const scale=Math.max(view[2]/svg.clientWidth,view[3]/svg.clientHeight);
    setView([dragging.view[0]-dx*scale,dragging.view[1]-dy*scale,view[2],view[3]]);
  });
  for(const type of ['pointerup','pointercancel','lostpointercapture']) svg.addEventListener(type,()=>{dragging=null;});

  const credit = byId('atlas-city-credit');
  for(const [index,[label,url]] of [
    ['geoBoundaries','https://www.geoboundaries.org/'],
    ['© OpenStreetMap contributors','https://www.openstreetmap.org/copyright'],
    ['Wikimedia Commons','https://commons.wikimedia.org/'],
  ].entries()){
    const link = document.createElement('a'); link.textContent=label; link.href=url;
    link.target='_blank'; link.rel='noopener noreferrer';
    credit.append(index?', ':'Границы городов: ',link);
  }

  let resizeTimer, measuredWidth;
  new ResizeObserver(([entry])=>{
    if (entry.contentRect.width === measuredWidth) return;
    measuredWidth = entry.contentRect.width;
    clearTimeout(resizeTimer);
    resizeTimer=setTimeout(()=>{if(data&&!byId('profile-atlas').hidden)render();},120);
  }).observe(svg);
  if(new URLSearchParams(location.search).get('view')==='atlas')void loadMap();

  window.TripWiseAtlas = {
    update(routes) {
      cities = new Map(); countries = new Map();
      for (const route of routes) {
        const city = cityMeta[route.city];
        if (!city) continue;
        cities.set(route.city,(cities.get(route.city)||0)+1);
        countries.set(city.country,(countries.get(city.country)||0)+1);
      }
      changeMode(mode);
    },
  };
})();
