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
function groupPrice(p,g){const rates={adult:1,child:p.kind==='restaurant'?.65:.5,senior:p.kind==='restaurant'?.9:.7,...(p.price_multipliers||{})};const unit=p.kind==='restaurant'&&Number.isFinite(p.avg_check_cny)&&p.avg_check_cny>=0?p.avg_check_cny:p.price_cny;return round(unit*(g.adult*rates.adult+g.child*rates.child+g.senior*rates.senior)+(p.pet_fee_cny||0)*g.pet);}
function flags(p,date){const d=new Date(date+'T00:00:00Z'),dow=d.getUTCDay()||7,month=d.getUTCMonth()+1,weekend=dow>=6;
 const seasonal=Array.isArray(p.season_months)&&p.season_months.length>0;
 const closed=(p.opening.closed_weekdays||[]).includes(dow)||(p.opening.closed_dates||[]).includes(date);
 const offSeason=seasonal&&!p.season_months.includes(month);
 const crowd=weekend?(p.weekend_crowd_level||'medium'):(p.weekday_crowd_level||'low');
 return {available:!closed&&!offSeason,crowd,weekend,seasonal,offSeason,closed,open:minute(p.opening.open),close:minute(p.opening.close),crowdPenalty:weekend&&crowd==='high'?90:weekend&&crowd==='medium'?15:0};
}
function suitable(p,g){return !(g.pet&&p.pet_friendly!==true)&&!(g.child&&p.family_friendly===false)&&!(g.senior&&p.senior_friendly===false);}
function duration(p,pace){const base=Number.isFinite(p.duration_minutes)&&p.duration_minutes>0?p.duration_minutes:60;return Math.max(20,Math.ceil(base*({relaxed:1.15,balanced:1,intensive:.9}[pace]||1)/5)*5);}
function matrixCell(osm,a,b,mode){if(!osm||!a||!b)return null;const table=osm[mode==='walk'?'foot':'car'];if(!table||!Array.isArray(table.ids))return null;const i=table.ids.indexOf(a.id),j=table.ids.indexOf(b.id);if(i<0||j<0)return null;const n=table.durations?.[i]?.[j],d=table.distances?.[i]?.[j];return Number.isFinite(n)&&n>=0?{seconds:n,metres:Number.isFinite(d)&&d>=0?d:null}:null;}
function transportLeg(a,b,transport,g,date,osm=null,router=null,departureMinute=null,maxCost=Infinity){
 if(!a||a.id===b.id)return {base_minutes:0,buffer_minutes:0,planned_minutes:0,distance_m:0,transport_cost_cny:0,mode:'start',source:'start',reason:'Отель не задан.'};
 if(transport==='mixed'){
  const preference={walk:0,transit:1,taxi:2};
  const candidates=['walk','transit','taxi'].map(mode=>transportLeg(a,b,mode,g,date,osm,router,departureMinute,maxCost)).filter(Boolean);
  candidates.sort((a,b)=>a.planned_minutes-b.planned_minutes||a.base_minutes-b.base_minutes||preference[a.mode]-preference[b.mode]||a.transport_cost_cny-b.transport_cost_cny);
  return candidates.length?{...candidates[0],selection_reason:'fastest_affordable_planned_time',mixed_candidates:candidates.map(c=>({mode:c.mode,planned_minutes:c.planned_minutes,transport_cost_cny:c.transport_cost_cny,source:c.source}))}:null;
 }
 const direct=distance(a,b),cross=!!(a.river_bank&&b.river_bank&&a.river_bank!==b.river_bank);const persons=g.adult+g.child+g.senior;
 let mode=transport;
 if(mode==='walk'&&cross&&!matrixCell(osm,a,b,'walk'))return null;
 if(mode==='walk'&&direct>8000)return null;
 if(mode==='transit'&&g.pet)return null;
  if(mode==='transit'&&router){
   const result=router.route(a,b,{group:g,date,departure_min:departureMinute});if(!result)return null;
   must(Number.isFinite(result.base_minutes)&&result.base_minutes>0&&Number.isFinite(result.planned_minutes)&&result.planned_minutes>=result.base_minutes&&Number.isFinite(result.transport_cost_cny)&&result.transport_cost_cny>=0&&Number.isFinite(result.distance_m)&&result.distance_m>=0,'Транспортный модуль вернул некорректный переход.');
   return result.transport_cost_cny<=maxCost+1e-7?{...result,mode:'transit'}:null;
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
 return cost<=maxCost+1e-7?{base_minutes:base,buffer_minutes:planned-base,planned_minutes:planned,transport_cost_cny:cost,distance_m:Math.round(metres),mode,source,reason}:null;
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
 // Bounded open-path 2-opt within each fixed day. Keep its first attraction as the
 // anchor; change neither membership nor mandatory/interest selection.
 for(const day of days){const cost=list=>sum(list.slice(1).map((p,i)=>roadMetric(list[i],p)));let value=cost(day.pois);const before=value;
  for(let pass=0;pass<3;pass++){let best=null,bestCost=value;for(let i=1;i<day.pois.length-1;i++)for(let j=i+1;j<day.pois.length;j++){
   const candidate=day.pois.slice(0,i).concat(day.pois.slice(i,j+1).reverse(),day.pois.slice(j+1)),v=cost(candidate);if(v<bestCost-1){best=candidate;bestCost=v;}
  }if(!best)break;day.pois=best;value=bestCost;}day.geographic_optimization={method:'bounded_open_path_2opt_fixed_anchor',before_metres:Math.round(before),after_metres:Math.round(value),basis:'partial_OSRM_distance_else_geometric_estimate'};
 }
 return days;
}
function mealSlots(x){const a=[];if(x.start<12*60)a.push({kind:'breakfast',earliest:x.start,latest:Math.min(x.start+90,12*60-1,x.end-50),target:x.start,label:'Завтрак'});
 if(x.start<14*60&&x.end>=13*60)a.push({kind:'lunch',earliest:11*60+30,latest:14*60+30,target:13*60,label:'Обед'});
  if(x.end>17*60)a.push({kind:'dinner',earliest:Math.max(x.start,16*60),latest:Math.min(x.end-50,21*60),target:Math.min(Math.max(x.start,18*60),Math.min(x.end-50,21*60)),label:'Ужин'});
 if(x.interests.includes('cafes')&&!a.length)a.push({kind:'snack',earliest:x.start,latest:x.end-50,target:x.start,label:'Кафе'});
 return a.filter(s=>s.earliest<=x.end-50);
}
function legacyPlanTrip(dataset,options){
 const x=normalize(dataset,options),selection=selectAttractions(x),clusters=assignDays(x,selection.shortlist);const ranked=rankPois(dataset,options);
 const restaurants=x.pois.filter(p=>p.kind==='restaurant');const used=new Set(),covered=new Set(),required=new Set(x.required);
 const warnings=['Цены, часы, рейтинги, скидки, загруженность и сезонность моделируются. Время ОТ — оценка, не расписание.'];
 if(selection.tooManyRequired)warnings.push(`Обязательных мест больше, чем вместимость выбранного темпа: ${x.required.length} мест на ${x.days.length} дн. Лучше добавить дни или изменить темп.`);
 const totalPrimary=x.primary.filter(c=>c!=='cafes');if(totalPrimary.length>selection.capacity)warnings.push('Главных категорий больше запланированного числа посещений — возможно неполное покрытие.');
 const skipped={season:0,weekend:0,group:0,opening:0,budget:0,capacity:0},days=[];let spent=0,missedMeals=0,mealRepairs=0;
  let remainingMealSlots=mealSlots(x).length*x.days.length;
  const eligibleFood=restaurants.filter(p=>suitable(p,x.group));
  const minMealPrice=eligibleFood.length?Math.min(...eligibleFood.map(p=>groupPrice(p,x.group))):0;
  const foodReserve=(currentMeal=false)=>minMealPrice*Math.max(0,remainingMealSlots-(currentMeal?1:0));
 const requiredReserve=(except=null)=>sum(x.required.filter(id=>id!==except&&!used.has(id)).map(id=>groupPrice(x.byId.get(id),x.group)));
 if(requiredReserve()>x.budget)warnings.push('Нижняя оценка стоимости обязательных билетов выше бюджета. Некоторые обязательные объекты разместить невозможно.');
  if(requiredReserve()+foodReserve()>x.budget)warnings.push('Бюджет ниже минимальной оценки обязательных билетов и предусмотренных приёмов пищи. Полное питание и все обязательные места нельзя обещать в этом сценарии.');
 function mealChoice(state,slot,anchor){
   const anchors=unique([state.lastAttraction,anchor,...state.visit_ids.map(id=>x.byId.get(id))].filter(Boolean));
   if(!anchors.length)anchors.push(state.queue[0]||state.position||null);
   const bankAnchor=state.lastAttraction||anchor||anchors[0];
  const picks=[];
  for(const p of restaurants){if(used.has(p.id)||!suitable(p,x.group))continue;const f=flags(p,state.date);if(!f.available)continue;
    const allowance=x.budget-spent-groupPrice(p,x.group)-requiredReserve(p.id)-foodReserve(true);
    const from=state.position;let leg=transportLeg(from,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now,allowance);if(!leg)continue;
   const duration=Math.max(50,Math.min(60,Math.round(p.duration_minutes)));
    const rawArrival=state.now+leg.planned_minutes;let arrival=Math.max(rawArrival,f.open,slot.earliest);
    const depart=state.now+(arrival-rawArrival);
    if(depart!==state.now&&leg.mode==='transit'&&x.transit_router){
     const revised=transportLeg(from,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,depart,allowance);if(!revised)continue;
     leg=revised;arrival=depart+leg.planned_minutes;
     // Один консервативный пересчёт: если нужен ещё один сдвиг, кандидат неустойчив.
     if(arrival<f.open||arrival<slot.earliest)continue;
    }
    const price=groupPrice(p,x.group)+leg.transport_cost_cny;
    if(arrival+duration>f.close||arrival+duration>x.end||arrival>slot.latest)continue;
   if(spent+price+requiredReserve(p.id)+foodReserve(true)>x.budget+1e-7)continue;
    if(bankAnchor?.river_bank&&p.river_bank&&bankAnchor.river_bank!==p.river_bank)continue;
    const metres=anchors[0]?Math.min(...anchors.filter(Boolean).map(source=>distance(source,p))):0;if(metres>(x.interests.includes('cafes')?2500:1500))continue;
   const nearAnchor=state.lastAttraction?distance(p,state.lastAttraction):anchor?distance(p,anchor):0;
    picks.push({p,leg,duration,price,arrival,depart,metres,nearAnchor,score:metres+nearAnchor*.35+leg.planned_minutes*20+f.crowdPenalty*5-(p.rating||4)*30});
  }
   picks.sort((a,b)=>Math.floor(a.metres/500)-Math.floor(b.metres/500)||(b.p.rating||0)-(a.p.rating||0)||(b.p.review_count||0)-(a.p.review_count||0)||a.score-b.score||a.p.id.localeCompare(b.p.id));return picks[0]||null;
 }
 function append(state,p,type,leg,dur,reason,kind){
   if(type==='visit')state.lastCheckpoint=snapshot(state);
   if(leg.planned_minutes){state.events.push({type:'travel',start_min:state.now,end_min:state.now+leg.planned_minutes,from_poi_id:state.position.id,to_poi_id:p.id,...leg});state.now+=leg.planned_minutes;}
   const price=groupPrice(p,x.group);const evt={type,poi_id:p.id,start_min:state.now,end_min:state.now+dur,price_cny:price,reason,matched_interests:x.interests.filter(c=>p.categories.includes(c))};if(kind)evt.meal_kind=kind;
   state.events.push(evt);state.now+=dur;state.position=p;spent=round(spent+price+leg.transport_cost_cny);state.cost=round(state.cost+price+leg.transport_cost_cny);used.add(p.id);
    if(type==='visit'){state.lastAttraction=p;state.visit_ids.push(p.id);evt.matched_interests.forEach(c=>covered.add(c));}else{state.meal_ids.push(p.id);if(x.interests.includes('cafes'))covered.add('cafes');}
 }
  function appendMeal(state,meal,reason,kind){state.now=meal.depart;append(state,meal.p,'meal',meal.leg,meal.duration,reason,kind);remainingMealSlots=Math.max(0,remainingMealSlots-1);}
  function visitChoices(state){const picks=[];for(const p of state.queue){if(used.has(p.id))continue;const f=flags(p,state.date);if(!f.available||!suitable(p,x.group))continue;
   if(state.blockedForFood?.has(p.id))continue;
   const allowance=x.budget-spent-groupPrice(p,x.group)-requiredReserve(p.id)-foodReserve();
   const leg=transportLeg(state.position,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now,allowance);if(!leg)continue;const dur=duration(p,x.pace),arrival=state.now+leg.planned_minutes;
   if(arrival<f.open||arrival+dur>f.close||arrival+dur>x.end){skipped.opening++;continue;}
   const ticket=groupPrice(p,x.group);if(spent+ticket+leg.transport_cost_cny+requiredReserve(p.id)+foodReserve()>x.budget+1e-7){skipped.budget++;continue;}
   const mandatory=required.has(p.id),newPrimary=x.primary.filter(c=>p.categories.includes(c)&&!covered.has(c)).length;
   const newSecondary=x.secondary.filter(c=>p.categories.includes(c)&&!covered.has(c)).length;
   // Geographic proximity determines ordering once the attraction shortlist has been fixed.
   const anchor=state.lastAttraction||state.queue[0]||null;
   const anchorLeg=transportLeg(anchor,p,x.transport,x.group,state.date,x.osm_tables,x.transit_router,state.now,allowance);
   const spatial=anchorLeg?anchorLeg.planned_minutes+anchorLeg.distance_m/200:leg.planned_minutes+leg.distance_m/200;
   const popularity=Math.min(10,Math.log2(1+Math.max(0,p.review_count||0))*.5);
   const score=(mandatory?600:0)+newPrimary*180+newSecondary*65+((p.rating||4)*2)+popularity-spatial*2-f.crowdPenalty*12-(dur>140?12:0);
   picks.push({p,leg,dur,score,mandatory,newPrimary,newSecondary});
  }return picks.sort((a,b)=>b.score-a.score||a.leg.planned_minutes-b.leg.planned_minutes||a.p.id.localeCompare(b.p.id));}
 function snapshot(state){return {now:state.now,position:state.position,lastAttraction:state.lastAttraction,events:state.events.slice(),visit_ids:state.visit_ids.slice(),meal_ids:state.meal_ids.slice(),cost:state.cost,lastCheckpoint:state.lastCheckpoint,blockedForFood:new Set(state.blockedForFood||[]),spent,used:[...used],covered:[...covered],remainingMealSlots};}
 function restore(state,s){for(const key of ['now','position','lastAttraction','events','visit_ids','meal_ids','cost','lastCheckpoint'])state[key]=s[key];state.blockedForFood=new Set(s.blockedForFood);spent=s.spent;remainingMealSlots=s.remainingMealSlots;used.clear();s.used.forEach(id=>used.add(id));covered.clear();s.covered.forEach(id=>covered.add(id));}
 function protectedMealChoice(state,slot,anchor){let meal=mealChoice(state,slot,anchor);if(meal)return meal;
  const original=snapshot(state),blocked=new Set(state.blockedForFood||[]);
  for(let attempt=0;attempt<2;attempt++){const last=state.events[state.events.length-1];if(last?.type!=='visit'||required.has(last.poi_id)||!state.lastCheckpoint)break;
   blocked.add(last.poi_id);const checkpoint=state.lastCheckpoint;restore(state,checkpoint);state.blockedForFood=new Set(blocked);
   const nextAnchor=state.lastAttraction||state.queue.find(p=>!used.has(p.id)&&!blocked.has(p.id))||null;
   meal=mealChoice(state,slot,nextAnchor);if(meal){mealRepairs++;return meal;}
  }restore(state,original);return null;
 }
 for(const d of clusters){const state={date:d.date,now:x.start,position:null,events:[],visit_ids:[],meal_ids:[],warnings:[],cost:0,queue:[...d.pois],blockedForFood:new Set()};const meals=mealSlots(x);let slotIndex=0;let guard=0;
  // breakfast (when selected) should be near the upcoming attraction, not a random distant venue.
  if(meals[0]?.kind==='breakfast'){const m=protectedMealChoice(state,meals[0],d.pois[0]||null);if(m)appendMeal(state,m,'Завтрак возле выбранных достопримечательностей.',meals[0].kind);else{state.warnings.push('Не найден завтрак рядом с дневным кластером в указанное время.');missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);}slotIndex++;}
  while(guard++<50&&state.visit_ids.length<PACES[x.pace].max){
   const choices=visitChoices(state),slot=meals[slotIndex];
   let meal=null;
   if(slot){const next=choices[0];const wouldEnd=next?state.now+next.leg.planned_minutes+next.dur:Infinity;
    if(state.now>=slot.earliest&&(state.now>=slot.target-40||wouldEnd>slot.latest))meal=protectedMealChoice(state,slot,next?.p||null);
    if(meal){appendMeal(state,meal,'Питание вблизи следующего запланированного места (радиус до 2,5 км).',slot.kind);slotIndex++;continue;}
    if(state.now>slot.latest){state.warnings.push(`${slot.label}: не найдено подходящего кафе рядом с маршрутом.`);missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);slotIndex++;continue;}
   }
   if(!choices.length){if(slot){meal=protectedMealChoice(state,slot,null);if(meal){appendMeal(state,meal,'Питание возле текущего дневного маршрута.',slot.kind);slotIndex++;continue;}}
    break;}
   // Avoid crossing a meal window with a lengthy attraction unless meal is unavailable.
   let chosen=choices.find(v=>!slot||state.now+v.leg.planned_minutes+v.dur<slot.latest-20)||choices[0];
   if(slot&&state.now>=slot.earliest&&state.now+chosen.leg.planned_minutes+chosen.dur>slot.latest){meal=protectedMealChoice(state,slot,chosen.p);if(meal){appendMeal(state,meal,'Питание рядом с планируемым посещением.',slot.kind);slotIndex++;continue;}}
   const f=flags(chosen.p,d.date);append(state,chosen.p,'visit',chosen.leg,chosen.dur,`${chosen.mandatory?'Обязательное место. ':''}${chosen.newPrimary?'Главный интерес. ':''}Сгруппировано по близости; ${f.weekend?'учтена загруженность выходного':'учтены часы работы и расположение'}.`);
  }
  // End-of-day meal only if feasible and still relevant to the itinerary.
  while(slotIndex<meals.length){const slot=meals[slotIndex++];const m=protectedMealChoice(state,slot,null);if(m)appendMeal(state,m,'Кафе рядом с последним местом маршрута.',slot.kind);else{missedMeals++;remainingMealSlots=Math.max(0,remainingMealSlots-1);state.warnings.push(`${slot.label}: не удалось найти подходящее кафе в выбранном окне.`);}}
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
 summary:{total_cost_cny:round(spent),budget_cny:x.budget,visit_count:visits.length,meal_count:food.length,planned_travel_minutes:sum(legs.map(e=>e.planned_minutes)),estimated_travel_minutes:sum(legs.map(e=>e.base_minutes)),transport_cost_cny:round(sum(legs.map(e=>e.transport_cost_cny))),covered_interests:[...covered],days_count:days.length,group:x.group,transport_mode:x.transport,synthetic_data:true,first_last_poi:true,skipped_filters:skipped,daily_visits:days.map(d=>d.visit_ids.length),osm_legs:sourceLegs,stage1_selected_ids:selection.shortlist.map(z=>z.poi.id),stage1_count:selection.shortlist.length,clusters:clusters.map(d=>({date:d.date,ids:d.pois.map(p=>p.id),geographic_optimization:d.geographic_optimization})),required_total:x.required.length,required_placed:x.required.length-unplaced.length,meal_repair_count:mealRepairs}};
}
/* Bounded insertion planner. All accepted moves are scheduled with exactly the
   transport legs later emitted to the UI. No DB mutation and no network calls. */
function planTrip(dataset,options){
 const x=normalize(dataset,options),required=new Set(x.required),slots=mealSlots(x),cost=new Map(),available=new Map(),durations=new Map();
 const MAX_EVALUATIONS=6000,metrics={evaluations:0,evaluation_limit:MAX_EVALUATIONS,search_limited:false,transport_calls:0,transport_cache_hits:0,food_candidates:0};
 for(const p of x.pois){cost.set(p.id,groupPrice(p,x.group));durations.set(p.id,p.kind==='restaurant'?Math.max(50,Math.min(60,p.duration_minutes||50)):duration(p,x.pace));available.set(p.id,x.days.map(date=>flags(p,date)));}
 const allowed=p=>suitable(p,x.group)&&Number.isFinite(cost.get(p.id))&&cost.get(p.id)>=0;
 const food=x.pois.filter(p=>p.kind==='restaurant'&&allowed(p));
 const minFood=food.length?Math.min(...food.map(p=>cost.get(p.id))):0;
 const mandatoryMinutes=sum(x.required.map(id=>durations.get(id))),availableMinutes=x.days.length*(x.end-x.start);
 if(mandatoryMinutes>availableMinutes&&x.required.length>12){
  const days=x.days.map(date=>({date,events:[],visit_ids:[],meal_ids:[],total_cost_cny:0,active_minutes:0,active_limit_minutes:x.end-x.start,rest_minutes:0,starts_at:null,ends_at:null,warnings:[]}));
  return {schema_version:'shanghai-demo-v1',model_version:'bounded-joint-insertion-v5',status:'infeasible',days,selected_ids:[],warnings:['На обязательные места требуется не менее '+mandatoryMinutes+' мин посещений; доступно '+availableMinutes+' мин за '+x.days.length+' дн. Даже без транспорта и дополнительного питания все обязательные места не помещаются.','Длительности, цены, часы и рейтинги каталога демонстрационные; вывод относится к этой модели.'],ranking:rankPois(dataset,options),checks:{time:'passed',budget:'passed',opening_hours:'passed',meals:slots.length?'failed':'passed',required_places:'failed',interest_coverage:'failed',primary_interests:'failed',no_gaps:'passed',no_duplicates:'passed'},unplaced_required_ids:x.required.slice(),uncovered_interests:x.interests.slice(),summary:{total_cost_cny:0,budget_cny:x.budget,visit_count:0,meal_count:0,planned_travel_minutes:0,estimated_travel_minutes:0,transport_cost_cny:0,covered_interests:[],days_count:days.length,group:x.group,transport_mode:x.transport,synthetic_data:true,first_last_poi:true,skipped_filters:{},daily_visits:days.map(()=>0),osm_legs:0,stage1_selected_ids:[],stage1_count:0,clusters:days.map(d=>({date:d.date,ids:[]})),required_total:x.required.length,required_placed:0,meal_repair_count:0,optimizer:metrics}};
 }
 const eligible=x.pois.filter(p=>p.kind==='attraction'&&allowed(p)&&available.get(p.id).some(f=>f.available));
 const utility=p=>x.primary.filter(c=>p.categories.includes(c)).length*1000+x.secondary.filter(c=>p.categories.includes(c)).length*180+Math.max(0,p.rating||0)*3+Math.min(20,Math.log2(1+(p.review_count||0)))-cost.get(p.id)*.03;
 const matching=eligible.filter(p=>required.has(p.id)||p.categories.some(c=>x.interests.includes(c)));
 matching.sort((a,b)=>utility(b)-utility(a)||a.id.localeCompare(b.id));
 const candidates=[],chosen=new Set();const include=p=>{if(p&&!chosen.has(p.id)){chosen.add(p.id);candidates.push(p);}};
 x.required.forEach(id=>include(x.byId.get(id)));
 // Reserve alternatives per category and district; quality alone cannot fill the
 // whole shortlist with one distant district, nor replace interest relevance.
 const cells=new Map();for(const p of matching){const key=Math.floor(p.lat/.025)+':'+Math.floor(p.lon/.025);if(!cells.has(key))cells.set(key,[]);cells.get(key).push(p);}
 for(const c of x.primary.concat(x.secondary).filter(c=>c!=='cafes'))matching.filter(p=>p.categories.includes(c)).slice(0,Math.max(4,x.days.length*3)).forEach(include);
 for(const list of [...cells.values()].sort((a,b)=>utility(b[0])-utility(a[0])).slice(0,20))list.slice(0,1).forEach(include);
 matching.slice(0,Math.max(24,x.days.length*12)).forEach(include);
 const shortlist=candidates.slice(0,Math.max(x.required.length,Math.min(96,24+x.days.length*12)));
 shortlist.sort((a,b)=>(required.has(b.id)?1:0)-(required.has(a.id)?1:0)||utility(b)-utility(a)||a.id.localeCompare(b.id));
 // Grid keeps the 2000 restaurants out of the schedule branch loop. Buckets are
 // queried once per shortlisted attraction, and reused by every insertion.
 const grid=new Map(),cell=.008;const cellKey=(a,b)=>a+':'+b;
 for(const p of food){const key=cellKey(Math.floor(p.lat/cell),Math.floor(p.lon/cell));if(!grid.has(key))grid.set(key,[]);grid.get(key).push(p);}
 const nearCache=new Map();function nearby(anchor){if(!anchor)return [];if(nearCache.has(anchor.id))return nearCache.get(anchor.id);const a=Math.floor(anchor.lat/cell),b=Math.floor(anchor.lon/cell),found=[];
  for(let ring=0;ring<=5;ring++){for(let i=-ring;i<=ring;i++)for(let j=-ring;j<=ring;j++){if(ring&&Math.max(Math.abs(i),Math.abs(j))!==ring)continue;for(const p of grid.get(cellKey(a+i,b+j))||[])found.push(p);}if(found.length>=24&&ring>=1)break;}
  found.sort((p,q)=>distance(anchor,p)-distance(anchor,q)||cost.get(p.id)-cost.get(q.id)||p.id.localeCompare(q.id));
  const out=unique(found.slice(0,18).concat(found.slice().sort((a,b)=>cost.get(a.id)-cost.get(b.id)).slice(0,6)));nearCache.set(anchor.id,out);return out;
 }
 const legCache=new Map();function leg(a,b,day,at,allowance=Infinity){if(allowance<-.00001)return null;const key=(a?.id||'@')+'>'+b.id+'|'+day+'|'+at;let values;if(legCache.has(key)){metrics.transport_cache_hits++;values=legCache.get(key);}else{metrics.transport_calls++;
  values=(x.transport==='mixed'?['walk','transit','taxi']:[x.transport]).map(mode=>transportLeg(a,b,mode,x.group,x.days[day],x.osm_tables,x.transit_router,at,Infinity)).filter(Boolean);legCache.set(key,values);}
  const possible=values.filter(v=>v.transport_cost_cny<=Math.max(0,allowance)+1e-7).sort((a,b)=>a.planned_minutes-b.planned_minutes||a.transport_cost_cny-b.transport_cost_cny);return possible[0]||null;
 }
 const sumCost=plans=>sum(plans.map(p=>p.cost));
 const proxyCache=new Map();function proxy(a,b){if(!a||!b||a.id===b.id)return 0;const key=a.id<b.id?a.id+'|'+b.id:b.id+'|'+a.id;if(proxyCache.has(key))return proxyCache.get(key);const metres=distance(a,b)*1.25,cross=a.river_bank&&b.river_bank&&a.river_bank!==b.river_bank;
  const value=x.transport==='walk'?metres/(x.group.senior?2800:x.group.child||x.group.pet?3200:4200)*60+(cross?60:0):x.transport==='transit'?13+metres/370+(cross?12:0):4+metres/285+(cross?7:0);proxyCache.set(key,value);return value;
 }
 const insertionProxy=(seq,p,pos)=>proxy(seq[pos-1],p)+proxy(p,seq[pos])-proxy(seq[pos-1],seq[pos]);
 function scheduleAttempt(sequence,day,otherPlans=[],pendingRequired=[],skipMeals=false){if(metrics.evaluations>=MAX_EVALUATIONS){metrics.search_limited=true;return null;}metrics.evaluations++;let now=x.start,position=null,spent=0,slotIndex=0,missed=0;const events=[],used=new Set(otherPlans.flatMap(p=>p.meal_ids)),otherCost=sumCost(otherPlans),meal_ids=[],visit_ids=[];
  const requiredReserve=sum(pendingRequired.filter(id=>!sequence.some(p=>p.id===id)).map(id=>cost.get(id)||0));
  const futureFood=sequence.some(p=>required.has(p.id))||pendingRequired.length?0:Math.max(0,x.days.length-1-otherPlans.length)*slots.length*minFood;
  const left=()=>x.budget-otherCost-spent-requiredReserve-futureFood;
  function add(p,type,travel,start,kind){const d=durations.get(p.id);const depart=start-travel.planned_minutes;if(travel.planned_minutes)events.push({type:'travel',from_poi_id:position.id,to_poi_id:p.id,start_min:depart,end_min:start,...travel});
   const e={type,poi_id:p.id,start_min:start,end_min:start+d,price_cny:cost.get(p.id),matched_interests:x.interests.filter(c=>p.categories.includes(c)),reason:type==='meal'?'Питание по минимальному дополнительному времени вдоль маршрута.':(required.has(p.id)?'Обязательное место. ':'')+(p.categories.some(c=>x.primary.includes(c))?'Главный интерес. ':'Дополнительный интерес. ')+'Проверены часы, группа, бюджет и переход.'};if(kind)e.meal_kind=kind;events.push(e);now=e.end_min;spent+=cost.get(p.id)+travel.transport_cost_cny;position=p;used.add(p.id);(type==='meal'?meal_ids:visit_ids).push(p.id);
  }
  function meal(slot,next){if(skipMeals)return false;const anchor=position||next||shortlist[0]||food[0];if(!anchor)return false;const pool=unique(nearby(anchor).concat(next?nearby(next):[])),picks=[];const remainingMeals=(slots.length-slotIndex-1)*minFood;
   const remainingTickets=sum(sequence.filter(p=>!visit_ids.includes(p.id)).map(p=>cost.get(p.id)));
   const prospective=pool.filter(p=>!used.has(p.id)&&!required.has(p.id)&&available.get(p.id)[day].available&&cost.get(p.id)+remainingTickets+remainingMeals<=left()+1e-7).sort((a,b)=>{const d=p=>(position?distance(position,p):0)+(next?distance(p,next):0);return d(a)-d(b)||cost.get(a.id)-cost.get(b.id)||a.id.localeCompare(b.id);}).slice(0,24);
   for(let candidateIndex=0;candidateIndex<prospective.length;candidateIndex++){if(candidateIndex>=8&&picks.length)break;const p=prospective[candidateIndex];metrics.food_candidates++;const f=available.get(p.id)[day];const allowance=left()-cost.get(p.id)-remainingTickets-remainingMeals;
    // Delay departure rather than inventing a rest event. Re-route at the exact
    // delayed departure so graph service windows cannot be crossed silently.
    let depart=now,t=leg(position,p,day,depart,allowance);if(!t)continue;let start=Math.max(now+t.planned_minutes,slot.earliest,f.open);
    if(start>now+t.planned_minutes){depart=start-t.planned_minutes;t=leg(position,p,day,depart,allowance);if(!t)continue;start=depart+t.planned_minutes;}
    const finish=start+durations.get(p.id);if(start<slot.earliest||start<f.open||start>slot.latest||finish>f.close||finish>x.end)continue;
    const onward=next?leg(p,next,day,finish,allowance-t.transport_cost_cny):null;if(next&&!onward)continue;
    if(next){const nf=available.get(next.id)[day],ns=Math.max(finish+onward.planned_minutes,nf.open);if(ns+durations.get(next.id)>Math.min(nf.close,x.end))continue;}
    const direct=position&&next?leg(position,next,day,now,allowance):null;
    const detour=t.planned_minutes+(onward?.planned_minutes||0)-(direct?.planned_minutes||0);
    // No hard river ban: a genuine routed bridge may win. An expensive detour
    // cannot win on synthetic stars; food is subordinate to the attractions.
    if(detour>45)continue;
    picks.push({p,t,start,score:detour*12+Math.abs(start-slot.target)*.04+cost.get(p.id)*.08-(p.rating||0)*2-Math.min(2,Math.log2(1+(p.review_count||0))*.1)});
   }
   picks.sort((a,b)=>a.score-b.score||a.p.id.localeCompare(b.p.id));if(!picks.length)return false;const best=picks[0];add(best.p,'meal',best.t,best.start,slot.kind);return true;
  }
  for(let index=0;index<sequence.length;index++){const p=sequence[index],f=available.get(p.id)?.[day];if(!f?.available||!allowed(p))return null;
   while(slotIndex<slots.length){const s=slots[slotIndex],trial=leg(position,p,day,now,left()-cost.get(p.id)),visitStart=trial?Math.max(now+trial.planned_minutes,f.open):Infinity;const visitEnd=visitStart+durations.get(p.id);
    // A required restaurant replaces the matching meal, but must not swallow
    // an earlier meal window merely because it opens much later in the day.
    if(p.kind==='restaurant'&&visitStart>=s.earliest&&visitStart<=s.latest)break;
    if(s.kind==='breakfast'||now>=s.target-25||visitEnd>s.latest){if(!meal(s,p)){if(visitEnd<=s.latest)break;missed++;}slotIndex++;}else break;
   }
   const reserve=sum(sequence.slice(index+1).map(p=>cost.get(p.id)))+(skipMeals||required.has(p.id)?0:(slots.length-slotIndex)*minFood);
   let t=leg(position,p,day,now,left()-cost.get(p.id)-reserve);if(!t)return null;let start=Math.max(now+t.planned_minutes,f.open);
   if(start>now+t.planned_minutes){const depart=start-t.planned_minutes;t=leg(position,p,day,depart,left()-cost.get(p.id)-reserve);if(!t)return null;start=depart+t.planned_minutes;if(start<f.open)return null;}
   if(start+durations.get(p.id)>Math.min(f.close,x.end)||spent+t.transport_cost_cny+cost.get(p.id)+otherCost>x.budget+1e-7)return null;
   if(p.kind==='restaurant'){const slot=slots[slotIndex],kind=slot&&start>=slot.earliest&&start<=slot.latest?slot.kind:'required';add(p,'meal',t,start,kind);if(kind!=='required')slotIndex++;}else add(p,'visit',t,start);
  }
  while(slotIndex<slots.length){if(!meal(slots[slotIndex],null))missed++;slotIndex++;}
  if(spent+otherCost>x.budget+1e-7)return null;
  return {date:x.days[day],sequence,events,visit_ids,meal_ids,cost:round(spent),missed,travel:sum(events.filter(e=>e.type==='travel').map(e=>e.planned_minutes))};
 }
 function schedule(sequence,day,otherPlans=[],pendingRequired=[]){return scheduleAttempt(sequence,day,otherPlans,pendingRequired)|| (sequence.some(p=>required.has(p.id))?scheduleAttempt(sequence,day,otherPlans,pendingRequired,true):null);}
 const blank=day=>({date:x.days[day],sequence:[],events:[],visit_ids:[],meal_ids:[],cost:0,missed:slots.length,travel:0});
 const score=plans=>{const visited=plans.flatMap(p=>p.sequence),covered=new Set(visited.flatMap(p=>p.categories));return visited.filter(p=>required.has(p.id)).length*1e7-plans.reduce((s,p)=>s+p.missed,0)*20000+x.primary.filter(c=>c!=='cafes'&&covered.has(c)).length*12000+x.secondary.filter(c=>c!=='cafes'&&covered.has(c)).length*3000+sum(visited.map(utility))-sum(plans.map(p=>p.travel))*9-sum(plans.map(p=>p.sequence.length*p.sequence.length))*65;};
 function insertions(plans,p,pending){const results=[];let dayOrder=x.days.map((_,i)=>i);if(!required.has(p.id)&&dayOrder.length>3)dayOrder.sort((a,b)=>{const proxy=i=>plans[i].sequence.length*1200+(plans[i].sequence.length?Math.min(...plans[i].sequence.map(z=>distance(z,p))):0);return proxy(a)-proxy(b)||a-b;}).splice(3);
  for(const day of dayOrder){if(!available.get(p.id)?.[day]?.available)continue;const old=plans[day],other=plans.filter((_,i)=>i!==day);let positions=Array.from({length:old.sequence.length+1},(_,i)=>i);
   if(!required.has(p.id)&&positions.length>4)positions.sort((a,b)=>{const detour=i=>(i?distance(old.sequence[i-1],p):0)+(i<old.sequence.length?distance(p,old.sequence[i]):0)-(i&&i<old.sequence.length?distance(old.sequence[i-1],old.sequence[i]):0);return detour(a)-detour(b)||a-b;}).splice(4);
   for(const pos of positions){const seq=old.sequence.slice();seq.splice(pos,0,p);const candidate=schedule(seq,day,other,pending);if(candidate){const moved=plans.slice();moved[day]=candidate;results.push(moved);}}
  }return results;
 }
 function optionalMoves(plans,p,coverage){const out=[];for(let day=0;day<plans.length;day++){if(!available.get(p.id)?.[day]?.available)continue;const seq=plans[day].sequence;for(let pos=0;pos<=seq.length;pos++){const detour=insertionProxy(seq,p,pos),emptyBonus=seq.length?0:plans[day].missed*20000;out.push({p,day,pos,priority:utility(p)+coverage*2000+emptyBonus-detour*9-65*(2*seq.length+1)});}}return out;}
 function applyMove(plans,move){const seq=plans[move.day].sequence.slice();seq.splice(move.pos,0,move.p);const candidate=schedule(seq,move.day,plans.filter((_,i)=>i!==move.day),[]);if(!candidate)return null;const moved=plans.slice();moved[move.day]=candidate;return moved;}
 let beam=[x.days.map((_,i)=>blank(i))];const mandatory=shortlist.filter(p=>required.has(p.id));
 // Protect mandatory objects before any optional visits. Keeping alternative
 // states recovers both date assignments and orders (closed-day traps).
 const beamWidth=eligible.length<=12&&mandatory.length<=5?256:12;
 for(let i=0;i<mandatory.length;i++){const p=mandatory[i],pending=mandatory.slice(i+1).map(p=>p.id),next=[];for(const plans of beam){next.push(...insertions(plans,p,pending));next.push(plans);}const seen=new Set();beam=next.sort((a,b)=>score(b)-score(a)).filter(plans=>{const k=plans.map(p=>p.sequence.map(p=>p.id).join(',')).join('|');if(seen.has(k))return false;seen.add(k);return true;}).slice(0,beamWidth);if(metrics.search_limited)break;}
 let plans=beam[0],value=score(plans);const selected=new Set(plans.flatMap(p=>p.visit_ids));
 const optional=shortlist.filter(p=>!required.has(p.id));
 // Spatial seeds plus category reserve. Each round evaluates bounded alternative
 // insertions and chooses the best whole-trip move, rather than filling day 1.
 for(let roundIndex=0;roundIndex<40&&!metrics.search_limited;roundIndex++){let best=null,bestValue=value;
  const uncovered=x.primary.concat(x.secondary).filter(c=>c!=='cafes'&&!plans.some(d=>d.sequence.some(p=>p.categories.includes(c))));
  const choices=optional.filter(p=>!selected.has(p.id)).map(p=>{const nearest=selected.size?Math.min(...plans.flatMap(d=>d.sequence).map(a=>distance(a,p))):0;return {p,priority:utility(p)+uncovered.filter(c=>p.categories.includes(c)).length*2000-nearest*.025};}).sort((a,b)=>b.priority-a.priority||a.p.id.localeCompare(b.p.id)).slice(0,8);
  const moves=choices.flatMap(({p})=>optionalMoves(plans,p,uncovered.filter(c=>p.categories.includes(c)).length)).sort((a,b)=>b.priority-a.priority||a.p.id.localeCompare(b.p.id)||a.day-b.day||a.pos-b.pos);
  // Cheap proxy ranks proposals, never accepts them. Expand the exact schedule
  // neighborhood only if the first block yields no improving feasible move.
  for(let index=0;index<Math.min(36,moves.length);index++){if(index>=12&&best)break;const candidate=applyMove(plans,moves[index]);if(!candidate)continue;const v=score(candidate);if(v>bestValue+.0001){best=candidate;bestValue=v;}}
  if(!best)break;plans=best;value=bestValue;selected.clear();plans.flatMap(p=>p.visit_ids).forEach(id=>selected.add(id));
 }
 // Bounded relocate/2-opt: accept only fully revalidated schedules and a strict
 // whole-trip objective improvement. Mandatory membership cannot change.
 for(let pass=0;pass<2;pass++){let best=null,bestValue=value;const moves=[];for(let day=0;day<plans.length;day++){const seq=plans[day].sequence;for(let i=0;i<seq.length-1;i++)for(let j=i+1;j<seq.length;j++){const saving=proxy(seq[i-1],seq[i])+proxy(seq[j],seq[j+1])-proxy(seq[i-1],seq[j])-proxy(seq[i],seq[j+1]);moves.push({day,i,j,saving});}}
  moves.sort((a,b)=>b.saving-a.saving||a.day-b.day||a.i-b.i||a.j-b.j);for(const move of moves.slice(0,12)){const {day,i,j}=move,seq=plans[day].sequence,revised=seq.slice(0,i).concat(seq.slice(i,j+1).reverse(),seq.slice(j+1)),candidate=schedule(revised,day,plans.filter((_,k)=>k!==day),[]);if(!candidate)continue;const moved=plans.slice();moved[day]=candidate;const v=score(moved);if(v>bestValue+.0001){best=moved;bestValue=v;}}if(!best)break;plans=best;value=bestValue;
 }
 if(plans.length>1){let best=null,bestValue=value;const moves=[];for(let from=0;from<plans.length;from++){const seq=plans[from].sequence;for(let index=0;index<seq.length;index++){const p=seq[index];if(required.has(p.id))continue;const removed=insertionProxy(seq.filter(z=>z!==p),p,index);for(let day=0;day<plans.length;day++){if(day===from||!available.get(p.id)[day].available)continue;for(let pos=0;pos<=plans[day].sequence.length;pos++){const saving=(removed-insertionProxy(plans[day].sequence,p,pos))*9+130*(seq.length-plans[day].sequence.length-1);moves.push({p,from,day,pos,saving});}}}}
  moves.sort((a,b)=>b.saving-a.saving||a.p.id.localeCompare(b.p.id)||a.day-b.day||a.pos-b.pos);for(const move of moves.slice(0,8)){const reduced=schedule(plans[move.from].sequence.filter(z=>z.id!==move.p.id),move.from,plans.filter((_,i)=>i!==move.from),[]);if(!reduced)continue;const base=plans.slice();base[move.from]=reduced;const candidate=applyMove(base,move);if(!candidate)continue;const v=score(candidate);if(v>bestValue+.0001){best=candidate;bestValue=v;}}if(best){plans=best;value=bestValue;}
 }
 for(let i=0;i<plans.length;i++)if(!plans[i].sequence.length)plans[i]=schedule([],i,plans.filter((_,j)=>j!==i),[])||plans[i];
 const days=plans.map(p=>({date:p.date,events:p.events,visit_ids:p.visit_ids,meal_ids:p.meal_ids,total_cost_cny:p.cost,active_minutes:sum(p.events.map(e=>e.end_min-e.start_min)),active_limit_minutes:x.end-x.start,rest_minutes:0,starts_at:p.events[0]?.start_min??null,ends_at:p.events.at(-1)?.end_min??null,warnings:p.missed?['Не все приёмы пищи удалось разместить рядом с выбранными достопримечательностями в допустимые часы и бюджет.']:[]}));
 const all=days.flatMap(d=>d.events),visits=all.filter(e=>e.type==='visit'),meals=all.filter(e=>e.type==='meal'),legs=all.filter(e=>e.type==='travel'),used=all.filter(e=>e.poi_id).map(e=>e.poi_id),covered=new Set(visits.flatMap(e=>x.byId.get(e.poi_id).categories));if(meals.length)covered.add('cafes');
 const unplaced=x.required.filter(id=>!used.includes(id)),uncovered=x.interests.filter(c=>!covered.has(c)),missingPrimary=x.primary.filter(c=>!covered.has(c)),spent=round(sumCost(plans));
 const checks={time:days.every(d=>d.events.every((e,i)=>e.start_min>=x.start&&e.end_min<=x.end&&e.end_min>e.start_min&&(!i||e.start_min>=d.events[i-1].end_min)))?'passed':'failed',budget:spent<=x.budget+1e-7?'passed':'failed',opening_hours:days.every((d,i)=>d.events.filter(e=>e.poi_id).every(e=>{const f=available.get(e.poi_id)[i];return f.available&&e.start_min>=f.open&&e.end_min<=f.close;}))?'passed':'failed',meals:plans.some(p=>p.missed)?'failed':'passed',required_places:unplaced.length?'failed':'passed',interest_coverage:uncovered.length?'failed':'passed',primary_interests:missingPrimary.length?'failed':'passed',no_gaps:days.every(d=>d.events.every((e,i)=>!i||e.start_min===d.events[i-1].end_min))?'passed':'failed',no_duplicates:new Set(used).size===used.length?'passed':'failed'};
 const reasons=[];for(const id of x.required){const p=x.byId.get(id),fs=available.get(id);if(!suitable(p,x.group))reasons.push('Обязательное место «'+p.name+'» не подходит заданному составу группы по признакам каталога.');else if(!allowed(p))reasons.push('Для обязательного места «'+p.name+'» нет допустимой цены для расчёта бюджета.');if(!fs.some(f=>f.available))reasons.push('Обязательное место «'+p.name+'» закрыто или вне сезона во все выбранные даты: '+x.days.join(', ')+'.');else if(!fs.some(f=>f.available&&Math.max(x.start,f.open)+durations.get(id)<=Math.min(x.end,f.close)))reasons.push('Для обязательного места «'+p.name+'» требуется '+durations.get(id)+' мин; ни в один выбранный день окно работы не пересекается с дневным интервалом на достаточную длительность.');}
 const requiredTickets=sum(x.required.map(id=>cost.get(id)));if(requiredTickets>x.budget)reasons.push('Обязательные билеты и выбранные обязательные заведения для всей группы стоят не менее '+round(requiredTickets)+' CNY, бюджет '+x.budget+' CNY; даже без транспорта и остального питания бюджет недостаточен.');if(mandatoryMinutes>availableMinutes)reasons.push('На обязательные посещения требуется '+mandatoryMinutes+' мин, доступно '+availableMinutes+' мин за все дни; даже без транспорта времени недостаточно.');const impossible=reasons.length>0;
 const warnings=['Демонстрационная модель: цены, часы, рейтинги и сезонность синтетические. Дорожные оценки и модельные времена транспорта не являются подтверждённым расписанием.',...reasons];if(metrics.search_limited)warnings.push('Достигнут детерминированный лимит '+MAX_EVALUATIONS+' проверок расписаний. Сохранён лучший найденный маршрут; полнота поиска не гарантируется.');
 if(unplaced.length)warnings.push('Не размещены обязательные места: '+unplaced.map(id=>x.byId.get(id).name).join(', ')+(impossible?'. Выше указан подтверждённый конфликт в пределах исходной модели данных.':'. Проверены альтернативные дни и порядки; ограниченный поиск не доказывает невозможность.'));if(uncovered.length)warnings.push('Не представлены интересы: '+uncovered.map(c=>LABELS[c]).join(', ')+'.');if(plans.some(p=>p.missed))warnings.push('Завтрак, Обед или Ужин: не все необходимые приёмы пищи найдены в заданных условиях.');
 return {schema_version:'shanghai-demo-v1',model_version:'bounded-joint-insertion-v5',status:impossible?'infeasible':visits.length&&Object.entries(checks).every(([k,v])=>k==='no_gaps'||v==='passed')?'demo':'partial',days,selected_ids:used,warnings:warnings.concat(days.flatMap(d=>d.warnings)),ranking:rankPois(dataset,options),checks,unplaced_required_ids:unplaced,uncovered_interests:uncovered,summary:{total_cost_cny:spent,budget_cny:x.budget,visit_count:visits.length,meal_count:meals.length,planned_travel_minutes:sum(legs.map(e=>e.planned_minutes)),estimated_travel_minutes:sum(legs.map(e=>e.base_minutes)),transport_cost_cny:round(sum(legs.map(e=>e.transport_cost_cny))),covered_interests:[...covered],days_count:days.length,group:x.group,transport_mode:x.transport,synthetic_data:true,first_last_poi:true,skipped_filters:{},daily_visits:days.map(d=>d.visit_ids.length),osm_legs:legs.filter(e=>e.source==='OSM_OSRM').length,stage1_selected_ids:shortlist.map(p=>p.id),stage1_count:shortlist.length,clusters:plans.map(p=>({date:p.date,ids:p.visit_ids,geographic_optimization:{method:'validated_bounded_insertion_2opt',basis:'buffered_transport_time'}})),required_total:x.required.length,required_placed:x.required.length-unplaced.length,meal_repair_count:0,optimizer:metrics}};
}
return {planTrip,rankPois,selectAttractions,assignDays,normalize,groupPrice,transportLeg,FX_TO_CNY,CATEGORIES,LABELS,PACES,distance,duration,flags,suitable};
});
