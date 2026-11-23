/* Real published rail topology; estimated times/fares; explicitly fictional buses. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.TripWiseTransit=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const dist=(a,b)=>{const r=Math.PI/180,h=Math.sin((b.lat-a.lat)*r/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin((b.lon-a.lon)*r/2)**2;return 12742000*Math.atan2(Math.sqrt(h),Math.sqrt(Math.max(0,1-h)));};
const point=p=>[p.lon,p.lat], label=p=>p.name||[p.name_en,p.name_zh].filter(Boolean).join(' / ');
class Heap{constructor(){this.a=[];}push(x){let i=this.a.length;this.a.push(x);while(i){let p=(i-1)>>1;if(this.a[p].d<=x.d)break;this.a[i]=this.a[p];i=p;}this.a[i]=x;}pop(){const a=this.a,first=a[0],last=a.pop();if(a.length){let i=0;while(i*2+1<a.length){let c=i*2+1;if(c+1<a.length&&a[c+1].d<a[c].d)c++;if(a[c].d>=last.d)break;a[i]=a[c];i=c;}a[i]=last;}return first;}get length(){return this.a.length;}}
function createRouter(data){
 if(!data||data.crs!=='EPSG:4326'||!data.metro||!data.buses)throw Error('Неверный локальный набор транспорта.');
 const nodes=new Map(),physical=new Map(),adj=new Map(),overrides=new Map(),cache=new Map(),nearestCache=new Map();
 const pair=(a,b)=>[a,b].sort().join('|');
 for(const t of data.metro.transfers||[])for(let i=0;i<t.line_ids.length;i++)for(let j=i+1;j<t.line_ids.length;j++)overrides.set(t.station_id+'|'+pair(t.line_ids[i],t.line_ids[j]),t);
 const register=(kind,p)=>{const id=(kind==='metro'?'m:':'b:')+p.id;physical.set(id,{...p,physical_id:id,mode:kind,states:[]});};
 (data.metro.stations||[]).forEach(p=>register('metro',p));(data.buses.stops||[]).forEach(p=>register('bus',p));
 function state(kind,pid,line){const phy=physical.get((kind==='metro'?'m:':'b:')+pid);if(!phy)return null;const key=phy.physical_id+'|'+line.id;if(!nodes.has(key)){nodes.set(key,{key,physical:phy,line,mode:kind});phy.states.push(key);adj.set(key,[]);}return key;}
 function edge(a,b,info){if(a&&b&&a!==b)adj.get(a).push({to:b,...info});}
 const metroSpeed={metro:35,apm:25,maglev:170,rail:70};
 for(const line of data.metro.lines||[])for(const branch of line.branches||[]){let ids=branch.station_ids.slice();if(branch.loop&&ids[0]!==ids[ids.length-1])ids.push(ids[0]);ids.forEach(id=>state('metro',id,line));for(let i=1;i<ids.length;i++){
  const a=state('metro',ids[i-1],line),b=state('metro',ids[i],line);if(!a||!b)continue;const metres=dist(nodes.get(a).physical,nodes.get(b).physical)*1.08,minutes=Math.max(1,metres/1000/(metroSpeed[line.kind]||35)*60+.7);
  edge(a,b,{kind:'ride',mode:'metro',line,metres,minutes,direction_key:branch.loop?'loop_forward:'+line.id:ids[ids.length-1],direction:branch.loop?'По кольцу через '+label(nodes.get(b).physical):label(physical.get('m:'+ids[ids.length-1]))});
  edge(b,a,{kind:'ride',mode:'metro',line,metres,minutes,direction_key:branch.loop?'loop_backward:'+line.id:ids[0],direction:branch.loop?'По кольцу через '+label(nodes.get(a).physical):label(physical.get('m:'+ids[0]))});
 }}
 for(const line of data.buses.routes||[]){const ids=line.stop_ids;ids.forEach(id=>state('bus',id,line));for(let i=1;i<ids.length;i++){
  const a=state('bus',ids[i-1],line),b=state('bus',ids[i],line);if(!a||!b)continue;const metres=dist(nodes.get(a).physical,nodes.get(b).physical)*1.28,minutes=Math.max(1,metres/1000/(line.speed_kmh||18)*60+.5);
  edge(a,b,{kind:'ride',mode:'bus',line,metres,minutes,direction:label(physical.get('b:'+ids[ids.length-1]))});
  if(line.bidirectional)edge(b,a,{kind:'ride',mode:'bus',line,metres,minutes,direction:label(physical.get('b:'+ids[0]))});
 }}
 const waiting=n=>n.mode==='bus'?(n.line.headway_minutes||15)/2:3;
 for(const p of physical.values())for(const a of p.states)for(const b of p.states){if(a===b)continue;const na=nodes.get(a),nb=nodes.get(b),override=p.mode==='metro'?overrides.get(p.id+'|'+pair(na.line.id,nb.line.id)):null;if(override?.blocked)continue;edge(a,b,{kind:'transfer',mode:'transfer',minutes:(override?.estimated_minutes||4)+waiting(nb),metres:0,line:nb.line});}
 // Transfers between a bus stop and a nearby station are planning estimates, not exits.
 const rail=[...physical.values()].filter(p=>p.mode==='metro'&&p.states.length),bus=[...physical.values()].filter(p=>p.mode==='bus'&&p.states.length);
 for(const stop of bus)for(const station of rail){const metres=dist(stop,station);if(metres>250)continue;for(const a of stop.states)for(const b of station.states){edge(a,b,{kind:'transfer',mode:'walk',minutes:metres/50+4+waiting(nodes.get(b)),metres,line:nodes.get(b).line});edge(b,a,{kind:'transfer',mode:'walk',minutes:metres/50+4+waiting(nodes.get(a)),metres,line:nodes.get(a).line});}}
 function route(from,to,options={}){
  const group={adult:1,child:0,senior:0,pet:0,...options.group};if(group.pet)return null;
  if(!from||!to||![from.lat,from.lon,to.lat,to.lon].every(Number.isFinite))return null;
  const cacheKey=[from.id,from.lat,from.lon,to.id,to.lat,to.lon,JSON.stringify(group),options.allow_synthetic_bus!==false,options.departure_min??'unknown'].join('|');
  if(cache.has(cacheKey)){
   const cached=cache.get(cacheKey);
   return cached===null?null:JSON.parse(cached);
  }
  const speed=group.senior?2800:group.child?3200:4200,maxWalk=group.senior?1200:1800;
  const allowed=n=>options.allow_synthetic_bus!==false||n.mode!=='bus';
  const busOpen=(n,elapsed)=>{if(n.mode!=='bus'||!Number.isFinite(options.departure_min))return true;const parse=t=>t.split(':').reduce((h,m)=>h*60+Number(m),0);const t=options.departure_min+elapsed;return t>=parse(n.line.service_start||'06:00')&&t<=parse(n.line.service_end||'22:00');};
  function nearby(p){
   // All dependencies: coordinates, bank filter, walking radius and bus permission.
   // Graph is immutable within this router. Departure, fares and labels do not
   // affect station selection; route/service checks remain time-specific below.
   const key=JSON.stringify([p.lat,p.lon,p.river_bank||null,maxWalk,options.allow_synthetic_bus!==false]);
   if(nearestCache.has(key))return nearestCache.get(key);
   const result=['metro','bus'].flatMap(mode=>[...physical.values()].filter(s=>s.mode===mode&&s.states.length&&(mode!=='bus'||options.allow_synthetic_bus!==false)).map(s=>({p:s,distance:dist(p,s)})).filter(s=>s.distance<=maxWalk&&(!p.river_bank||!s.p.river_bank||p.river_bank===s.p.river_bank)).sort((a,b)=>a.distance-b.distance).slice(0,3));
   if(nearestCache.size>=4096)nearestCache.delete(nearestCache.keys().next().value);
   nearestCache.set(key,result);return result;
  }
  const origins=nearby(from),destinations=nearby(to);if(!origins.length||!destinations.length)return null;
  const exits=new Map(destinations.map(d=>[d.p.physical_id,{...d,minutes:d.distance*1.25/speed*60+3}]));
  const best=new Map(),prev=new Map(),seeds=new Map(),heap=new Heap();
  for(const origin of origins)for(const key of origin.p.states){const node=nodes.get(key);if(!allowed(node))continue;const value=origin.distance*1.25/speed*60+3+waiting(node);if(!busOpen(node,value))continue;if(value<(best.get(key)??Infinity)){best.set(key,value);prev.set(key,null);seeds.set(key,origin);heap.push({key,d:value});}}
  let winner=null,total=Infinity;
  while(heap.length){const item=heap.pop();if(item.d!==best.get(item.key))continue;if(item.d>=total)break;const node=nodes.get(item.key),exit=exits.get(node.physical.physical_id);
   if(exit&&prev.get(item.key)&&item.d+exit.minutes<total){let key=item.key,hasRide=false;while(prev.get(key)){const rec=prev.get(key);if(rec.edge.kind==='ride')hasRide=true;key=rec.from;}if(hasRide){total=item.d+exit.minutes;winner={key:item.key,exit};}}
   for(const e of adj.get(item.key)){if(!allowed(nodes.get(e.to)))continue;const value=item.d+e.minutes;if(e.kind==='transfer'&&!busOpen(nodes.get(e.to),value))continue;if(value<(best.get(e.to)??Infinity)){best.set(e.to,value);prev.set(e.to,{from:item.key,edge:e});heap.push({key:e.to,d:value});}}
  }
  if(!winner){cache.set(cacheKey,null);return null;}
  const path=[];let key=winner.key;while(prev.get(key)){const rec=prev.get(key);path.push({from:nodes.get(rec.from),to:nodes.get(key),edge:rec.edge});key=rec.from;}path.reverse();const origin=seeds.get(key),instructions=[],geometry=[];
  const walk=(a,b,minutes,fromName,toName,source)=>{instructions.push({mode:'walk',from_name:fromName,to_name:toName,base_minutes:Math.ceil(minutes),estimated:true,synthetic:true,text:'Пешком: '+fromName+' → '+toName+' (доступ по приблизительным координатам, улицы уточняются отдельно)'});geometry.push({mode:'walk',coordinates:[point(a),point(b)],source,synthetic:true});};
  walk(from,origin.p,origin.distance*1.25/speed*60+3,label(from),label(origin.p),'estimated_access');
  let boarded=new Set(),lastRide=null,hasBus=false,hasMetro=false,cost=0,metres=origin.distance*1.25+winner.exit.distance*1.25;
  for(const step of path){const e=step.edge;metres+=e.metres;
   if(e.kind==='ride'){
    if(e.mode==='bus')hasBus=true;else hasMetro=true;
    if(!lastRide||lastRide.line_id!==e.line.id||lastRide.mode!==e.mode||lastRide.direction_key!==(e.direction_key||e.direction)){
     lastRide={mode:e.mode,line_id:e.line.id,line_name:e.mode==='bus'?e.line.id+' · '+e.line.name:e.line.name+' / '+(e.line.name_zh||''),from_name:label(step.from.physical),to_name:label(step.to.physical),direction:e.direction,direction_key:e.direction_key||e.direction,stop_count:1,base_minutes:e.minutes,estimated:true,synthetic:e.mode==='bus',color:e.line.color};instructions.push(lastRide);
     geometry.push({mode:e.mode,line_id:e.line.id,color:e.line.color,coordinates:[point(step.from.physical),point(step.to.physical)],source:e.mode==='bus'?'synthetic_bus':'real_topology_schematic',synthetic:e.mode==='bus',tracks_schematic:true});
     const people=group.adult+group.child+group.senior;if(e.mode==='bus')cost+=(e.line.fare_cny||2)*people;else if(!boarded.has('metro'))cost+=group.adult*4+group.child*2+group.senior*2;boarded.add(e.mode);
    }else{lastRide.stop_count++;lastRide.to_name=label(step.to.physical);lastRide.base_minutes+=e.minutes;geometry[geometry.length-1].coordinates.push(point(step.to.physical));}
   }else{
    lastRide=null;instructions.push({mode:'transfer',line_id:step.to.line.id,line_name:step.to.line.name,from_name:label(step.from.physical),to_name:label(step.to.physical),base_minutes:Math.ceil(e.minutes),estimated:true,synthetic:step.from.mode==='bus'||step.to.mode==='bus',text:'Пересадка: '+label(step.from.physical)+' → '+step.to.line.name+'; время перехода и ожидания оценочное'});
    if(e.metres)geometry.push({mode:'walk',coordinates:[point(step.from.physical),point(step.to.physical)],source:'estimated_transfer_access',synthetic:true});
   }
  }
  walk(winner.exit.p,to,winner.exit.distance*1.25/speed*60+3,label(winner.exit.p),label(to),'estimated_access');
  instructions.forEach(i=>i.base_minutes=Math.ceil(i.base_minutes));const base=Math.ceil(total),planned=Math.ceil((base*1.18+4)/5)*5;
  const result={base_minutes:base,planned_minutes:planned,buffer_minutes:planned-base,distance_m:Math.round(metres),transport_cost_cny:Math.round(cost*100)/100,mode:'transit',source:hasBus?(hasMetro?'MIXED_TRANSIT_ESTIMATED':'SYNTHETIC_BUS'):'REAL_METRO_TOPOLOGY_ESTIMATED_TIMES',instructions,geometry_segments:geometry,reason:'Локальный граф: опубликованная топология метро; DEMO-BUS — вымышленные автобусы. Время, доступ к станциям, ожидание и тарифы оценочные, движение и выходы не проверены.',fare_basis:'synthetic',time_basis:'planning_estimate',service_verified:false};
  // Keep the serialized snapshot so every caller gets a fresh deep copy,
  // without re-serializing instructions and geometry on cache hits.
  const snapshot=JSON.stringify(result);
  if(cache.size>5000)cache.clear();
  cache.set(cacheKey,snapshot);
  return JSON.parse(snapshot);
 }
 return {route,stats:{stations:rail.length,bus_stops:bus.length,states:nodes.size,edges:[...adj.values()].reduce((n,a)=>n+a.length,0),get nearest_cache_size(){return nearestCache.size;}}};
}
return {createRouter};
});
