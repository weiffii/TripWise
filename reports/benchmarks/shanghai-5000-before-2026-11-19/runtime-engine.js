/* TripWise Shanghai Lab v4. Two stages: select/cluster POIs, then schedule. Demo fields synthetic;
   optional OSRM road-network matrix supplied by browser, never silently presented as OSM offline. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.TripWiseShanghai=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const CATEGORIES=['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual'];
const LABELS={museums:'Музеи',history:'История',nature:'Природа',cafes:'Кафе',entertainment:'Развлечения',shopping:'Шопинг',viewpoints:'Смотровые',unusual:'Необычные места'};
const PACES={relaxed:{factor:1.17,max:4,target:3,label:'Спокойный'},balanced:{factor:0.72,max:6,target:5,label:'Сбалансированный'},intensive:{factor:0.65,max:8,target:7,label:'Насыщенный'}};
const FX_TO_CNY=Object.freeze({CNY:1,RUB:0.087,USD:7.10,EUR:7.75,GBP:9.30});
const TRANSIT_ROUTERS=new WeakMap();
const sum=a=>a.reduce((s,n)=>s+n,0),unique=a=>[...new Set(a)],round=n=>Math.round(n*100)/100;
function must(ok,message){if(!ok)throw Error(message);}
function minute(t){must(typeof t==='string'&&(/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(t)||t==='24:00'),'Неверный формат времени.');const [h,m]=t.split(':').map(Number);return h*60+m;}
function dates(start,n){must(/^\d{4}-\d\d-\d\d$/.test(start),'Неверная дата начала.');const d=new Date(start+'T00:00:00Z');must(!Number.isNaN(d.getTime())&&d.toISOString().slice(0,10)===start,'Несуществующая дата.');return Array.from({length:n},(_,i)=>new Date(d.valueOf()+i*86400000).toISOString().slice(0,10));}
function transitRouter(data){
 if(!data)return null;
 must(typeof data==='object','Неверный транспортный набор.');
 if(TRANSIT_ROUTERS.has(data))return TRANSIT_ROUTERS.get(data);
 const api=typeof globalThis!=='undefined'&&globalThis.TripWiseTransit||
  (typeof module==='object'&&module.exports&&typeof require==='function'?require('./shanghai-transit.js'):null);
 must(api&&typeof api.createRouter==='function','Модуль транспортных маршрутов не загружен.');
 const router=api.createRouter(data);must(router&&typeof router.route==='function','Неверный модуль транспортных маршрутов.');
 TRANSIT_ROUTERS.set(data,router);return router;
}
function normalize(dataset,o){
 must(dataset&&Array.isArray(dataset.pois)&&dataset.pois.length,'Каталог Шанхая пуст.');must(o&&typeof o==='object','Нет анкеты.');
 const primary=unique(o.primary_interests||o.interests||[]),secondary=unique(o.secondary_interests||[]).filter(x=>!primary.includes(x)),interests=primary.concat(secondary);
 must(interests.length>0&&interests.every(x=>CATEGORIES.includes(x)),'Выберите хотя бы один интерес.');
 const pace=o.pace||'balanced';must(!!PACES[pace],'Неизвестный темп.');
 const n=o.days??2;must(Number.isInteger(n)&&n>=1&&n<=5,'Число дней: 1–5.');
 const start=minute(o.day_start||'09:00'),end=minute(o.day_end||'19:00');must(end-start>=60,'Дневное окно должно быть от одного часа.');
 const budget=o.budget_cny??1500;must(Number.isFinite(budget)&&budget>=0&&budget<=1e7,'Неверный бюджет.');
 const group={adult:1,child:0,senior:0,pet:0,...(o.group||{})};must(['adult','child','senior','pet'].every(k=>Number.isInteger(group[k])&&group[k]>=0&&group[k]<=30)&&group.adult+group.child+group.senior>0,'Проверьте состав группы.');
 const transport=o.transport||'mixed';must(['mixed','walk','transit','taxi'].includes(transport),'Неверный вид транспорта.');
 const byId=new Map();for(const p of dataset.pois){must(p&&typeof p.id==='string'&&!byId.has(p.id),'Повторный ID объекта.');must(['attraction','restaurant'].includes(p.kind)&&Array.isArray(p.categories),'Неверный тип объекта.');must(Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&p.lat>=30&&p.lat<=32&&p.lon>=120&&p.lon<=123,'Неверные координаты.');must(p.opening&&Array.isArray(p.opening.closed_weekdays),'Отсутствуют часы работы.');byId.set(p.id,p);}
 const required=unique(o.required_ids||[]);must(required.every(id=>byId.has(id)),'Обязательное место отсутствует в каталоге.');
  return {primary,secondary,interests,pace,days:dates(o.start_date||'2026-11-18',n),start,end,budget,group,transport,required,pois:dataset.pois,byId,osm_tables:o.osm_tables||null,transit_router:transitRouter(dataset.transport)};
}
function distance(a,b){if(!a||!b)return 0;const r=n=>n*Math.PI/180,dl=r(b.lat-a.lat),dn=r(b.lon-a.lon),h=Math.sin(dl/2)**2+Math.cos(r(a.lat))*Math.cos(r(b.lat))*Math.sin(dn/2)**2;return 12742000*Math.atan2(Math.sqrt(h),Math.sqrt(Math.max(0,1-h)));}
function groupPrice(p,g){const rates={adult:1,child:p.kind==='restaurant'?.65:.5,senior:p.kind==='restaurant'?.9:.7,...(p.price_multipliers||{})};return round(p.price_cny*(g.adult*rates.adult+g.child*rates.child+g.senior*rates.senior)+(p.pet_fee_cny||0)*g.pet);}
function flags(p,date){const d=new Date(date+'T00:00:00Z'),dow=d.getUTCDay()||7,month=d.getUTCMonth()+1,weekend=dow>=6;
 const seasonal=Array.isArray(p.season_months)&&p.season_months.length>0;
 const closed=(p.opening.closed_weekdays||[]).includes(dow)||(p.opening.closed_dates||[]).includes(date);
 const offSeason=seasonal&&!p.season_months.includes(month);
 const crowd=weekend?(p.weekend_crowd_level||'medium'):(p.weekday_crowd_level||'low');
 return {available:!closed&&!offSeason,crowd,weekend,seasonal,offSeason,closed,open:minute(p.opening.open),close:minute(p.opening.close),crowdPenalty:weekend&&crowd==='high'?90:weekend&&crowd==='medium'?15:0};
}
function suitable(p,g){return !(g.pet&&p.pet_friendly!==true)&&!(g.child&&p.family_friendly===false)&&!(g.senior&&p.senior_friendly===false);}
function duration(p,pace){return Math.max(50,Math.min(pace==='relaxed'?170:pace==='balanced'?105:95,Math.round(p.duration_minutes*PACES[pace].factor/5)*5));}
function matrixCell(osm,a,b,mode){if(!osm||!a||!b)return null;const table=osm[mode==='walk'?'foot':'car'];if(!table||!Array.isArray(table.ids))return null;const i=table.ids.indexOf(a.id),j=table.ids.indexOf(b.id);if(i<0||j<0)return null;const n=table.durations?.[i]?.[j],d=table.distances?.[i]?.[j];return Number.isFinite(n)&&n>=0?{seconds:n,metres:Number.isFinite(d)&&d>=0?d:null}:null;}
function transportLeg(a,b,transport,g,date,osm=null,router=null,departureMinute=null){
 if(!a||a.id===b.id)return {base_minutes:0,buffer_minutes:0,planned_minutes:0,distance_m:0,transport_cost_cny:0,mode:'start',source:'start',reason:'Отель не задан.'};
 const direct=distance(a,b),cross=!!(a.river_bank&&b.river_bank&&a.river_bank!==b.river_bank);const persons=g.adult+g.child+g.senior;
 let mode=transport;if(mode==='mixed')mode=!cross&&direct<=1750?'walk':g.pet||persons>=4?'taxi':'transit';
 if(mode==='walk'&&cross&&!matrixCell(osm,a,b,'walk'))return null;
 if(mode==='walk'&&direct>8000)return null;
 if(mode==='transit'&&g.pet)return null;
  if(mode==='transit'&&router){
   const result=router.route(a,b,{group:g,date,departure_min:departureMinute});if(!result)return null;
   must(Number.isFinite(result.base_minutes)&&result.base_minutes>0&&Number.isFinite(result.planned_minutes)&&result.planned_minutes>=result.base_minutes&&Number.isFinite(result.transport_cost_cny)&&result.transport_cost_cny>=0&&Number.isFinite(result.distance_m)&&result.distance_m>=0,'Транспортный модуль вернул некорректный переход.');
   return {...result,mode:'transit'};
  }
 const road=direct*1.25;
 let base,cost=0,reason,source='synthetic_distance_estimate',metres=road;
 const real=mode==='transit'?null:matrixCell(osm,a,b,mode);
 if(real){metres=real.metres??road;source='OSM_OSRM';base=Math.max(1,Math.ceil(real.seconds/60));reason='По дорожной сети OpenStreetMap (OSRM); время без пробок и без гарантий.';}
 else if(mode==='walk'){base=Math.max(1,Math.ceil(road/(g.senior?2800:g.child||g.pet?3200:4200)*60));reason='Оценка пешего перехода по расстоянию; дорога OSM не подтверждена.';}
 else if(mode==='taxi'){base=Math.ceil((cross?9:4)+road/285);reason='Синтетическая оценка такси; дорожная сеть не подтверждена.';}
 else{base=Math.ceil((cross?21:13)+road/370);reason='Общественный транспорт: синтетическая оценка без GTFS и реального расписания.';}
 if(mode==='taxi'){const cars=Math.ceil((persons+Math.ceil(g.pet/2))/4);cost=round((16+Math.max(0,metres/1000-3)*2.8)*cars);}
 if(mode==='transit')cost=round(g.adult*4+g.child*2+g.senior*2);
 const planned=Math.ceil((base*(mode==='walk'?1.1:1.18)+(mode==='walk'?2:4))/5)*5;
 return {base_minutes:base,buffer_minutes:planned-base,planned_minutes:planned,transport_cost_cny:cost,distance_m:Math.round(metres),mode,source,reason};
}
function scorePOI(p,x,chosen,day){const inter=p.categories,primary=x.primary.filter(c=>inter.includes(c)),secondary=x.secondary.filter(c=>inter.includes(c)),isRequired=x.required.includes(p.id);
 const f=flags(p,day);let nearest=chosen.length?Math.min(...chosen.map(z=>distance(z,p))):0;
 const popularity=Math.min(50,Math.log2(1+Math.max(0,p.review_count||0))*3);
 return (isRequired?30000:0)+primary.length*2000+secondary.length*240+(primary.some(c=>!chosen.some(z=>z.categories.includes(c)))?2000:0)+(secondary.some(c=>!chosen.some(z=>z.categories.includes(c)))?500:0)+(f.seasonal&&!f.offSeason?160:0)-f.crowdPenalty*30-(nearest>7000?nearest/4:nearest/10)+((p.rating||4)*5)+popularity-groupPrice(p,x.group)*.4;
}
function rankPois(dataset,options){const x=normalize(dataset,options);return x.pois.map(p=>{const matched=x.interests.filter(c=>p.categories.includes(c));const active=x.days.some(d=>flags(p,d).available&&suitable(p,x.group));return {poi_id:p.id,matched_interests:matched,match_count:matched.length,priority_score:round(scorePOI(p,x,[],x.days[0])),reasons:[matched.length?'Совпадение: '+matched.map(c=>LABELS[c]).join(', '):'Другие категории',x.required.includes(p.id)?'Обязательно':'',!active?'Нет доступного дня':''].filter(Boolean)};}).sort((a,b)=>b.priority_score-a.priority_score||a.poi_id.localeCompare(b.poi_id)).map((v,i)=>({...v,rank:i+1}));}
function selectAttractions(x){
 const candidates=x.pois.filter(p=>p.kind==='attraction'&&x.days.some(d=>flags(p,d).available)&&suitable(p,x.group));
 const byId=new Map(candidates.map(p=>[p.id,p]));const ordered=[],selected=new Set();const record=(p,reason)=>{if(p&&!selected.has(p.id)){selected.add(p.id);ordered.push({poi:p,reason});}};
 // HARD priorities first: chosen required attractions, then one from every primary and every secondary category.
 for(const id of x.required){const p=byId.get(id);if(p&&p.kind==='attraction')record(p,'Обязательное место');}
 for(const category of x.primary.concat(x.secondary).filter(c=>c!=='cafes')){
   if(ordered.some(z=>z.poi.categories.includes(category)))continue;
   const choices=candidates.filter(p=>!selected.has(p.id)&&p.categories.includes(category));
   choices.sort((a,b)=>scorePOI(b,x,ordered.map(z=>z.poi),x.days[0])-scorePOI(a,x,ordered.map(z=>z.poi),x.days[0])||a.id.localeCompare(b.id));
   if(choices[0])record(choices[0],`Покрытие ${x.primary.includes(category)?'главного':'дополнительного'} интереса «${LABELS[category]}»`);
 }
 // Additional backups for category coverage: a category must not depend on one long visit fitting.
 for(const category of x.primary.concat(x.secondary).filter(c=>c!=='cafes')){
   const want=Math.min(x.days.length,2);
   while(ordered.filter(z=>z.poi.categories.includes(category)).length<want){
    const choices=candidates.filter(p=>!selected.has(p.id)&&p.categories.includes(category));
    choices.sort((a,b)=>scorePOI(b,x,ordered.map(z=>z.poi),x.days[0])-scorePOI(a,x,ordered.map(z=>z.poi),x.days[0])||a.id.localeCompare(b.id));
    if(!choices.length)break;
    record(choices[0],'Резерв покрытия категории «'+LABELS[category]+'»');
   }
 }
 const cap=x.days.length*PACES[x.pace].target;
 let candidatesRemaining=candidates.filter(p=>!selected.has(p.id)&&p.categories.some(c=>x.interests.includes(c)));
 // Global shortlist first, before assignment to days. Geographic compactness discourages outliers.
 while(ordered.length<Math.max(cap,ordered.length)&&candidatesRemaining.length){
  const mainCount=ordered.filter(z=>z.poi.categories.some(c=>x.primary.includes(c))).length;
  const targetMain=Math.ceil(cap*.65);
  const secondaryOnly=candidatesRemaining.filter(p=>p.categories.some(c=>x.secondary.includes(c))&&!p.categories.some(c=>x.primary.includes(c)));
  const pool=mainCount>=targetMain&&secondaryOnly.length?secondaryOnly:candidatesRemaining;
  pool.sort((a,b)=>{
    const score=p=>scorePOI(p,x,ordered.map(z=>z.poi),x.days[0])+(minute(p.opening.close)>=19*60?180:0);
    return score(b)-score(a)||a.id.localeCompare(b.id);
  });
  const next=pool[0];record(next,'Дополнительный объект по профилю и времени работы');
  candidatesRemaining=candidatesRemaining.filter(p=>p.id!==next.id);
 }
 return {shortlist:ordered,available:candidates.length,capacity:cap,tooManyRequired:x.required.filter(id=>x.byId.get(id)?.kind==='attraction').length>x.days.length*PACES[x.pace].max};
}
function assignDays(x,shortlist){
 const days=x.days.map(d=>({date:d,pois:[]}));const max=PACES[x.pace].max;
 // Priorities are spread before extras; assign where geographically closest to existing day's cluster.
 const roadMetric=(a,b)=>{
  const nearby=distance(a,b)<=1750&&a.river_bank===b.river_bank;
  const mode=x.transport==='walk'||x.transport==='mixed'&&nearby?'walk':'taxi';
  const road=matrixCell(x.osm_tables,a,b,mode);
  return road?.metres??distance(a,b)*1.3;
 };
 const distanceTo=(p,list)=>list.length?Math.min(...list.map(z=>roadMetric(z,p))):0;
 for(const item of shortlist){const p=item.poi;let choices=days.filter(d=>d.pois.length<max&&flags(p,d.date).available);
  if(!choices.length)continue;
  // Select the day whose current cluster matches, while balancing sparse days.
  choices.sort((a,b)=>{
   const cost=d=>d.pois.length?distanceTo(p,d.pois)/1000*8+d.pois.length*7:30+d.pois.length*5;
   const overflow=d=>d.pois.length>=PACES[x.pace].target?60:0;
   return cost(a)+overflow(a)-(cost(b)+overflow(b))||a.pois.length-b.pois.length;
  });
  choices[0].pois.push(p);
 }
 // Local swap: try moving each optional POI to closer cluster if it fits; keep mandatory coverage.
 for(let pass=0;pass<2;pass++)for(const day of days)for(const p of [...day.pois]){
   if(x.required.includes(p.id))continue;
   const other=days.filter(d=>d!==day&&d.pois.length<PACES[x.pace].target&&flags(p,d.date).available);
   const before=distanceTo(p,day.pois.filter(z=>z!==p));const best=other.sort((a,b)=>distanceTo(p,a.pois)-distanceTo(p,b.pois))[0];
   if(best&&best.pois.length&&before>distanceTo(p,best.pois)+2500&&day.pois.length>2){day.pois=day.pois.filter(z=>z!==p);best.pois.push(p);}
 }
 return days;
}
function mealSlots(x){const a=[];if(x.start<12*60)a.push({kind:'breakfast',earliest:x.start,latest:Math.min(x.start+90,12*60-1,x.end-50),target:x.start,label:'Завтрак'});
 if(x.start<14*60&&x.end>=13*60)a.push({kind:'lunch',earliest:11*60+30,latest:14*60+30,target:13*60,label:'Обед'});
  if(x.end>17*60)a.push({kind:'dinner',earliest:Math.max(x.start,16*60),latest:Math.min(x.end-50,21*60),target:Math.min(Math.max(x.start,18*60),Math.min(x.end-50,21*60)),label:'Ужин'});
 if(x.interests.includes('cafes')&&!a.length)a.push({kind:'snack',earliest:x.start,latest:x.end-50,target:x.start,label:'Кафе'});
 return a.filter(s=>s.earliest<=x.end-50);
}
function planTrip(dataset,options){
 const x=normalize(dataset,options),selection=selectAttractions(x),clusters=assignDays(x,selection.shortlist);const ranked=rankPois(dataset,options);
 const restaurants=x.pois.filter(p=>p.kind==='restaurant');const used=new Set(),covered=new Set(),required=new Set(x.required);
 const warnings=['Цены, часы, рейтинги, скидки, загруженность и сезонность моделируются. Время ОТ — оценка, не расписание.'];
 if(selection.tooManyRequired)warnings.push(`Обязательных мест больше, чем вместимость выбранного темпа: ${x.required.length} мест на ${x.days.length} дн. Лучше добавить дни или изменить темп.`);
 const totalPrimary=x.primary.filter(c=>c!=='cafes');if(totalPrimary.length>selection.capacity)warnings.push('Главных категорий больше запланированного числа посещений — возможно неполное покрытие.');
 const skipped={season:0,weekend:0,group:0,opening:0,budget:0,capacity:0},days=[];let spent=0,missedMeals=0;
  let remainingMealSlots=mealSlots(x).length*x.days.length;
  const eligibleFood=restaurants.filter(p=>suitable(p,x.group));
  const minMealPrice=eligibleFood.length?Math.min(...eligibleFood.map(p=>groupPrice(p,x.group))):0;
  const foodReserve=(currentMeal=false)=>minMealPrice*Math.max(0,remainingMealSlots-(currentMeal?1:0));
 const requiredReserve=(except=null)=>sum(x.required.filter(id=>id!==except&&!used.has(id)).map(id=>groupPrice(x.byId.get(id),x.group)));
 if(requiredReserve()>x.budget)warnings.push('Нижняя оценка стоимости обязательных билетов выше бюджета. Некоторые обязательные объекты разместить невозможно.');
  if(requiredReserve()+foodReserve()>x.budget)warnings.push('Бюджет ниже минимальной оценки обязательных билетов и предусмотренных приёмов пищи. Полное питание и все обязательные места нельзя обещать в этом сценарии.');
 function mealChoice(state,slot,anchor){
   const source=anchor||state.lastAttraction||state.queue[0]||state.position||null;
  const picks=[];
  for(const p of restaurants){if(used.has(p.id)||!suitable(p,x.group))continue; if(source && distance(source,p)>(x.interests.includes('cafes')?2500:1500))continue;const f=flags(p,state.date);if(!f.available)continue;
    const from=state.position;let leg=transportLeg(from,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now);if(!leg)continue;
   const duration=Math.max(50,Math.min(60,Math.round(p.duration_minutes)));
    const rawArrival=state.now+leg.planned_minutes;let arrival=Math.max(rawArrival,f.open,slot.earliest);
    const depart=state.now+(arrival-rawArrival);
    if(depart!==state.now&&leg.mode==='transit'&&x.transit_router){
     const revised=transportLeg(from,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,depart);if(!revised)continue;
     leg=revised;arrival=depart+leg.planned_minutes;
     // Один консервативный пересчёт: если нужен ещё один сдвиг, кандидат неустойчив.
     if(arrival<f.open||arrival<slot.earliest)continue;
    }
    const price=groupPrice(p,x.group)+leg.transport_cost_cny;
    if(arrival+duration>f.close||arrival+duration>x.end||arrival>slot.latest)continue;
   if(spent+price+requiredReserve(p.id)+foodReserve(true)>x.budget+1e-7)continue;
    if(source?.river_bank&&p.river_bank&&source.river_bank!==p.river_bank)continue;
    const metres=source?distance(source,p):0;if(metres>(x.interests.includes('cafes')?2500:1500))continue;
   const nearAnchor=anchor?distance(p,anchor):0;
    picks.push({p,leg,duration,price,arrival,depart,metres,nearAnchor,score:metres+nearAnchor*.35+leg.planned_minutes*20+f.crowdPenalty*5-(p.rating||4)*30});
  }
   picks.sort((a,b)=>Math.floor(a.metres/500)-Math.floor(b.metres/500)||(b.p.rating||0)-(a.p.rating||0)||(b.p.review_count||0)-(a.p.review_count||0)||a.score-b.score||a.p.id.localeCompare(b.p.id));return picks[0]||null;
 }
 function append(state,p,type,leg,dur,reason,kind){
   if(leg.planned_minutes){state.events.push({type:'travel',start_min:state.now,end_min:state.now+leg.planned_minutes,from_poi_id:state.position.id,to_poi_id:p.id,...leg});state.now+=leg.planned_minutes;}
   const price=groupPrice(p,x.group);const evt={type,poi_id:p.id,start_min:state.now,end_min:state.now+dur,price_cny:price,reason,matched_interests:x.interests.filter(c=>p.categories.includes(c))};if(kind)evt.meal_kind=kind;
   state.events.push(evt);state.now+=dur;state.position=p;spent=round(spent+price+leg.transport_cost_cny);state.cost=round(state.cost+price+leg.transport_cost_cny);used.add(p.id);
    if(type==='visit'){state.lastAttraction=p;state.visit_ids.push(p.id);evt.matched_interests.forEach(c=>covered.add(c));}else{state.meal_ids.push(p.id);if(x.interests.includes('cafes'))covered.add('cafes');}
 }
  function appendMeal(state,meal,reason,kind){state.now=meal.depart;append(state,meal.p,'meal',meal.leg,meal.duration,reason,kind);remainingMealSlots=Math.max(0,remainingMealSlots-1);}
  function visitChoices(state){const picks=[];for(const p of state.queue){if(used.has(p.id))continue;const f=flags(p,state.date);if(!f.available||!suitable(p,x.group))continue;
   const leg=transportLeg(state.position,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now);if(!leg)continue;const dur=duration(p,x.pace),arrival=state.now+leg.planned_minutes;
   if(arrival<f.open||arrival+dur>f.close||arrival+dur>x.end){skipped.opening++;continue;}
   const ticket=groupPrice(p,x.group);if(spent+ticket+leg.transport_cost_cny+requiredReserve(p.id)+foodReserve()>x.budget+1e-7){skipped.budget++;continue;}
   const mandatory=required.has(p.id),newPrimary=x.primary.filter(c=>p.categories.includes(c)&&!covered.has(c)).length;
   const newSecondary=x.secondary.filter(c=>p.categories.includes(c)&&!covered.has(c)).length;
   // Geographic proximity determines ordering once the attraction shortlist has been fixed.
   const anchor=state.lastAttraction||state.queue[0]||null;
   const anchorLeg=transportLeg(anchor,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now);
   const spatial=anchorLeg?anchorLeg.planned_minutes+anchorLeg.distance_m/200:leg.planned_minutes+leg.distance_m/200;
   const popularity=Math.min(10,Math.log2(1+Math.max(0,p.review_count||0))*.5);
   const score=(mandatory?600:0)+newPrimary*180+newSecondary*65+((p.rating||4)*2)+popularity-spatial*2-f.crowdPenalty*12-(dur>140?12:0);
   picks.push({p,leg,dur,score,mandatory,newPrimary,newSecondary});
  }return picks.sort((a,b)=>b.score-a.score||a.leg.planned_minutes-b.leg.planned_minutes||a.p.id.localeCompare(b.p.id));}
 for(const d of clusters){const state={date:d.date,now:x.start,position:null,events:[],visit_ids:[],meal_ids:[],warnings:[],cost:0,queue:[...d.pois]};const meals=mealSlots(x);let slotIndex=0;let guard=0;
  // breakfast (when selected) should be near the upcoming attraction, not a random distant venue.
  if(meals[0]?.kind==='breakfast'){const m=mealChoice(state,meals[0],d.pois[0]||null);if(m)appendMeal(state,m,'Завтрак возле выбранных достопримечательностей.',meals[0].kind);else{state.warnings.push('Не найден завтрак рядом с дневным кластером в указанное время.');missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);}slotIndex++;}
  while(guard++<50&&state.visit_ids.length<PACES[x.pace].max){
   const choices=visitChoices(state),slot=meals[slotIndex];
   let meal=null;
   if(slot){const next=choices[0];const wouldEnd=next?state.now+next.leg.planned_minutes+next.dur:Infinity;
    if(state.now>=slot.earliest&&(state.now>=slot.target-40||wouldEnd>slot.latest))meal=mealChoice(state,slot,next?.p||null);
    if(meal){appendMeal(state,meal,'Питание вблизи следующего запланированного места (радиус до 2,5 км).',slot.kind);slotIndex++;continue;}
    if(state.now>slot.latest){state.warnings.push(`${slot.label}: не найдено подходящего кафе рядом с маршрутом.`);missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);slotIndex++;continue;}
   }
   if(!choices.length){if(slot){meal=mealChoice(state,slot,null);if(meal){appendMeal(state,meal,'Питание возле текущего дневного маршрута.',slot.kind);slotIndex++;continue;}}
    break;}
   // Avoid crossing a meal window with a lengthy attraction unless meal is unavailable.
   let chosen=choices.find(v=>!slot||state.now+v.leg.planned_minutes+v.dur<slot.latest-20)||choices[0];
   if(slot&&state.now>=slot.earliest&&state.now+chosen.leg.planned_minutes+chosen.dur>slot.latest){meal=mealChoice(state,slot,chosen.p);if(meal){appendMeal(state,meal,'Питание рядом с планируемым посещением.',slot.kind);slotIndex++;continue;}}
   const f=flags(chosen.p,d.date);append(state,chosen.p,'visit',chosen.leg,chosen.dur,`${chosen.mandatory?'Обязательное место. ':''}${chosen.newPrimary?'Главный интерес. ':''}Сгруппировано по близости; ${f.weekend?'учтена загруженность выходного':'учтены часы работы и расположение'}.`);
  }
  // End-of-day meal only if feasible and still relevant to the itinerary.
  while(slotIndex<meals.length){const slot=meals[slotIndex++];if(state.now>slot.latest){missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);state.warnings.push(`${slot.label}: время прошло.`);continue;}const m=mealChoice(state,slot,null);if(m)appendMeal(state,m,'Кафе рядом с последним местом маршрута.',slot.kind);else{missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);state.warnings.push(`${slot.label}: не удалось найти подходящее кафе в выбранном окне.`);}}
  const remaining=state.queue.filter(p=>!used.has(p.id));if(remaining.length)state.warnings.push(`Не поместились в этот день: ${remaining.slice(0,5).map(p=>p.name).join(', ')}${remaining.length>5?' и ещё '+(remaining.length-5):''}.`);
  days.push({date:d.date,events:state.events,visit_ids:state.visit_ids,meal_ids:state.meal_ids,total_cost_cny:round(state.cost),active_minutes:sum(state.events.filter(e=>e.type==='visit'||e.type==='travel').map(e=>e.end_min-e.start_min)),active_limit_minutes:x.end-x.start,rest_minutes:0,starts_at:state.events[0]?.start_min??null,ends_at:state.events.length?state.now:null,warnings:state.warnings});
 }
 if(x.pace==='balanced'&&x.end-x.start>=8*60){
  const sparse=days.map((d,i)=>d.visit_ids.length<3?i+1:null).filter(Boolean);
  if(sparse.length)warnings.push(`Менее трёх посещений в дни ${sparse.join(', ')}: кандидаты этого района не вместились из-за расстояний, режима транспорта, часов или бюджета. Для более плотного дня попробуйте смешанный транспорт, другой интервал или дополнительные дни.`);
 }
 const unplaced=x.required.filter(id=>!used.has(id)),uncovered=x.interests.filter(c=>!covered.has(c));
 if(unplaced.length)warnings.push(`Не выполнены обязательные посещения (${unplaced.length}): ${unplaced.map(id=>x.byId.get(id).name).join(', ')}. Попробуйте больше дней или бюджет; некоторые объекты могут быть закрыты.`);
 const missingPrimary=x.primary.filter(c=>!covered.has(c));if(missingPrimary.length)warnings.push(`Не покрыты главные интересы: ${missingPrimary.map(c=>LABELS[c]).join(', ')}. Это не полный маршрут по заданным требованиям.`);
 if(uncovered.length)warnings.push(`Не представлены категории: ${uncovered.map(c=>LABELS[c]).join(', ')}.`);
 const all=days.flatMap(d=>d.events),visits=all.filter(e=>e.type==='visit'),food=all.filter(e=>e.type==='meal'),legs=all.filter(e=>e.type==='travel');
 const checks={time:days.every(d=>d.events.every((e,i)=>e.start_min>=x.start&&e.end_min<=x.end&&e.end_min>e.start_min&&(!i||e.start_min>=d.events[i-1].end_min)))?'passed':'failed',
 budget:spent<=x.budget+1e-6?'passed':'failed',opening_hours:days.every(d=>d.events.filter(e=>e.type==='visit'||e.type==='meal').every(e=>{const f=flags(x.byId.get(e.poi_id),d.date);return f.available&&e.start_min>=f.open&&e.end_min<=f.close;}))?'passed':'failed',
 meals:missedMeals?'failed':'passed',required_places:unplaced.length?'failed':'passed',interest_coverage:uncovered.length?'failed':'passed',primary_interests:missingPrimary.length?'failed':'passed',
 no_gaps:days.every(d=>d.events.every((e,i)=>i?e.start_min===d.events[i-1].end_min:true))?'passed':'failed',no_duplicates:new Set(all.filter(e=>e.poi_id).map(e=>e.poi_id)).size===visits.length+food.length?'passed':'failed'};
 const sourceLegs=legs.filter(e=>e.source==='OSM_OSRM').length;
 const status=Object.entries(checks).filter(([name])=>name!=='no_gaps').every(([,v])=>v==='passed')&&visits.length?'demo':'partial';
 return {schema_version:'shanghai-demo-v1',model_version:'two-stage-geo-cluster-routing-v4',status,days,selected_ids:[...used],warnings:unique(warnings.concat(days.flatMap(d=>d.warnings))),ranking:ranked,checks,unplaced_required_ids:unplaced,uncovered_interests:uncovered,
 summary:{total_cost_cny:round(spent),budget_cny:x.budget,visit_count:visits.length,meal_count:food.length,planned_travel_minutes:sum(legs.map(e=>e.planned_minutes)),estimated_travel_minutes:sum(legs.map(e=>e.base_minutes)),transport_cost_cny:round(sum(legs.map(e=>e.transport_cost_cny))),covered_interests:[...covered],days_count:days.length,group:x.group,transport_mode:x.transport,synthetic_data:true,first_last_poi:true,skipped_filters:skipped,daily_visits:days.map(d=>d.visit_ids.length),osm_legs:sourceLegs,stage1_selected_ids:selection.shortlist.map(z=>z.poi.id),stage1_count:selection.shortlist.length,clusters:clusters.map(d=>({date:d.date,ids:d.pois.map(p=>p.id)})),required_total:x.required.length,required_placed:x.required.length-unplaced.length}};
}
return {planTrip,rankPois,selectAttractions,assignDays,normalize,groupPrice,transportLeg,FX_TO_CNY,CATEGORIES,LABELS,PACES,distance};
});
