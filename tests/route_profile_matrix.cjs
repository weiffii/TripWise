'use strict';
// Synthetic shared-engine scenarios. Chat cases feed the named intent's operation
// to the route editor, with no LLM request and no claims about model parsing.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const api=require(process.env.TRIPWISE_MATRIX_ENGINE||'../ai_ds/web_demo/shanghai-engine.js');
const rub=process.env.TRIPWISE_MATRIX_CURRENCY==='RUB';

const categories=['museums','history','nature','shopping','unusual','viewpoints'];
const times=(tick)=>String(tick).padStart(2,'0')+':00';
function candidate(id,i,food=false){
 const bank=i%3?'west':'east',mall=i%6===3;
 const foodType=['restaurant','cafe','coffee_shop','bakery'][i%4];
 const base={id,name:food?'Кафе '+i:(mall?'Большой торговый центр ':'Место ')+i,
  kind:food?'restaurant':'attraction',categories:food?['cafes']:[categories[i%6]],
  lat:31.225+(i%13)*.0012,lon:121.45+(i%9)*.0014+(bank==='east'?.027:0),river_bank:bank,
  duration_minutes:food?50:(i%6===3?120:40+i%7*5),price_cny:food?12+i%19:i%11*8,
  avg_check_cny:food?12+i%19:undefined,rating:4.1+i%7*.1,review_count:100+i*6,
  opening:{open:food?'07:00':times(8+i%4),close:food?'23:00':times(17+i%5),closed_weekdays:i%11===0?[1]:[]},
  family_friendly:i%13!==12,senior_friendly:i%11!==10,pet_friendly:i%4!==3};
 if(!rub)return base;
 return {...base,lat:base.lat+24.52,lon:base.lon-83.84,price_cny:base.price_cny*12,
  ...(food?{food_type:foodType,meal_suitability:{breakfast:'likely',lunch:i%4<2?'likely':'unlikely',
   dinner:i%4<2?'likely':'unlikely'}}:mall?{visit_type:'large_shopping_mall',duration_source:'synthetic',
    duration_minutes:165+((i-3)/6)*15}:{})};
}
const dataset={pois:[...Array.from({length:48},(_,i)=>candidate('p'+i,i)),
 ...Array.from({length:36},(_,i)=>candidate('f'+i,i,true))]};
if(rub){
 dataset.currency='RUB';dataset.compact_waits=true;
 dataset.leg_profiles=Object.fromEntries(['walk','transit','taxi'].map(mode=>[
  mode,Object.fromEntries(dataset.pois.map((a,i)=>[a.id,Object.fromEntries(dataset.pois
   .flatMap((b,j)=>{
    if(i===j)return [];
    const n=3+Math.abs(i-j)%14+(a.river_bank===b.river_bank?0:11);
    const minutes=mode==='walk'?n:mode==='transit'?7+Math.floor(n*.65):8+Math.floor(n*.55);
    return [[b.id,{mode,base_minutes:minutes-1,planned_minutes:minutes,buffer_minutes:1,
     distance_m:n*100,transport_cost_cny:mode==='walk'?0:mode==='transit'?65:260,
     walking_minutes:mode==='walk'?minutes:mode==='transit'?4:0,transfers:mode==='transit'&&n>20?1:0,
     source:'synthetic-matrix'}]];
   }))]))
 ]));
}
const byId=new Map(dataset.pois.map(row=>[row.id,row]));
function profile(i){
 const days=[1,2,3,5,7,14][i%6],month=i<84?11:12;
 const start_date='2026-'+month+'-'+String(1+i%23).padStart(2,'0');
 const group=[{adult:1,child:0,senior:0,pet:0},{adult:2,child:0,senior:0,pet:0},{adult:1,child:1,senior:0,pet:0},
  {adult:2,child:0,senior:1,pet:0},{adult:1,child:0,senior:0,pet:1}][(i+i%3)%5];
 const modeSets=[['walk'],['taxi'],['transit'],['walk','taxi'],['walk','transit'],['taxi','transit'],['walk','taxi','transit']];
 let modes=modeSets[Math.floor(i/6)%modeSets.length];
 if(group.pet&&modes.length===1&&modes[0]==='transit')modes=['walk','transit'];
 const key=categories[(i+Math.floor(i/5))%6];
 const end=[16,19,21,22][Math.floor(i/3)%4],start=[8,9,10,12][i%4];
 const out={days,start_date,day_start:times(start),day_end:times(end),group,
  primary_interests:[key],secondary_interests:[categories[(categories.indexOf(key)+1)%6]],
  pace:['balanced','relaxed','intensive'][Math.floor(i/4)%3],budget_cny:[65,400,1600,4800,9000][Math.floor(i/3)%5],
  transport:modes.length===1?modes[0]:'mixed',transport_modes:modes,
  required_ids:i%4===0?['p5','p13']:i%4===1?['p3']:[]};
 if(days>=3&&i%2===0){
  const middle=new Date(Date.parse(start_date)+86400000).toISOString().slice(0,10);
  out.day_settings={[middle]:{day_start:'12:00',day_end:'17:00'}};
 }
 return out;
}
function visitIds(plan){return plan.days.flatMap(day=>day.events.filter(e=>e.type==='visit').map(e=>e.poi_id));}
function inspect(plan,options){
 const used=new Set(),mallDurations=[];let travel=0,waiting=0,maxGap=0,maxInternalGap=0,internalOver60=0,meals=0,visits=0;
 const hm=t=>Number(t.slice(0,2))*60+Number(t.slice(3));
 for(const day of plan.days){
  const settings=options.day_settings?.[day.date]||{};
  const opens=hm(settings.day_start||options.day_start),closes=hm(settings.day_end||options.day_end);
  let previous=opens;
  const slots={};
  for(const [index,event] of day.events.entries()){
   assert(event.start_min>=previous&&event.end_min>event.start_min&&event.end_min<=closes,
    day.date+' timing or overlap '+JSON.stringify(event));
   maxGap=Math.max(maxGap,event.start_min-previous);
   if(index>0){
    maxInternalGap=Math.max(maxInternalGap,event.start_min-previous);
    if(event.start_min-previous>60)internalOver60++;
   }
   waiting+=event.start_min-previous;
   previous=event.end_min;
   if(event.type==='travel'){
    travel+=event.planned_minutes;
    assert.equal(event.end_min-event.start_min,event.planned_minutes);
    assert(options.transport_modes.includes(event.mode)||
     (event.mode==='walk'&&event.selection_reason==='short_local_walking_connector'), 'unexpected mode '+event.mode);
   }
   if(['visit','meal'].includes(event.type)){
    assert(byId.has(event.poi_id),'unknown POI');
    assert(!used.has(event.poi_id),'duplicate POI '+event.poi_id);used.add(event.poi_id);
    if(byId.get(event.poi_id).visit_type==='large_shopping_mall'){
     const minutes=event.end_min-event.start_min;
     assert(minutes>=180&&minutes<=240,'large mall duration outside 180–240 min');
     mallDurations.push(minutes);
    }
    if(event.type==='visit')visits++;else{meals++;slots[event.meal_kind]=event;}
    const opening=byId.get(event.poi_id).opening;
    assert(event.start_min>=hm(opening.open)&&event.end_min<=hm(opening.close),'POI opening hours');
    assert(!opening.closed_weekdays.includes(new Date(day.date).getUTCDay()||7),'closed weekday');
   }
  }
  if(slots.breakfast&&slots.lunch)assert(slots.lunch.start_min-slots.breakfast.end_min>=150,'breakfast-lunch spacing');
  if(slots.lunch&&slots.dinner)assert(slots.dinner.start_min-slots.lunch.end_min>=180,'lunch-dinner spacing');
 }
 assert.equal(plan.days.length,options.days);
 assert(plan.summary.total_cost_cny<=options.budget_cny+.01,'budget exceeded');
 for(const id of options.required_ids)assert(used.has(id)||plan.unplaced_required_ids.includes(id),'mandatory missing without diagnostic');
 return {status:plan.status,visits,meals,travel_minutes:travel,waiting_minutes:waiting,max_gap:maxGap,
  max_internal_gap:maxInternalGap,internal_gaps_over_60:internalOver60,cost:plan.summary.total_cost_cny,
  mandatory_missing:plan.unplaced_required_ids.length,
  ...(rub?{mall_minutes:mallDurations,mall_visits_180_240:mallDurations.filter(n=>n>=180&&n<=240).length}:{})};
}
const results=[],profiles=new Set();
for(let i=0;i<130;i++){
 const chat=i>=110;
 const options=chat?{...profile(i),days:1+i%2,day_settings:undefined,transport:'walk',transport_modes:['walk'],
  group:{adult:1,child:0,senior:0,pet:0},budget_cny:5000+i,day_start:'09:00',day_end:'21:00',
  primary_interests:['museums','history','viewpoints'],secondary_interests:['nature'],required_ids:['p5']}:
  profile(i);
 if(rub){options.budget_cny*=12;options.route_priority=['balanced','walking','transfers','cost'][i%4];}
 const profileId=JSON.stringify(options);assert(!profiles.has(profileId));profiles.add(profileId);
 const row={id:i+1,kind:chat?'assistant_operation':'plan',options};
 try{
  const base=api.planTrip(dataset,options);row.before=inspect(base,options);
  if(chat){
   const day=base.days.find(d=>d.visit_ids.some(id=>!options.required_ids.includes(id)));
   assert(day,'fixture needs a removable visit');
   const poi_id=day.visit_ids.find(id=>!options.required_ids.includes(id)),name=byId.get(poi_id).name;
   const event=day.events.find(e=>e.poi_id===poi_id);
   if(i%3===0){
    row.command='Убери «'+name+'»';
    const out=api.editTrip(dataset,options,base,[{op:'remove_poi',poi_id}]);
    row.after=inspect(out.plan,options);assert(!visitIds(out.plan).includes(poi_id));
   }else if(i%3===1){
    const hour=String(Math.floor(event.start_min/60)).padStart(2,'0'),min=String(event.start_min%60).padStart(2,'0');
    row.command='Закрепи «'+name+'» на '+hour+':'+min;
    const out=api.editTrip(dataset,options,base,[{op:'pin_visit_time',day_date:day.date,poi_id,start_time:hour+':'+min}]);
    row.after=inspect(out.plan,options);
    assert.equal(out.plan.days.find(d=>d.date===day.date).events.find(e=>e.poi_id===poi_id).start_min,event.start_min);
   }else{
    row.command='Убери обязательное место «'+byId.get('p5').name+'»';
    assert.throws(()=>api.editTrip(dataset,options,base,[{op:'remove_poi',poi_id:'p5'}]),e=>e.code==='REQUIRED_PLACE_PROTECTED'||e.reason==='REQUIRED_PLACE_PROTECTED');
    row.expected_rejection='REQUIRED_PLACE_PROTECTED';
   }
  }
  row.pass=true;
 }catch(error){row.pass=false;row.error=error.code==='ERR_ASSERTION'?error.message:error.code||error.message;row.reason=error.reason;}
 results.push(row);
}
const counts=Object.fromEntries(['plan','assistant_operation'].map(kind=>{
 const cases=results.filter(row=>row.kind===kind);
 return [kind,{pass:cases.filter(row=>row.pass).length,fail:cases.filter(row=>!row.pass).length}];
}));
const output={data:'deterministic synthetic shared-engine fixtures; no LLM calls or live transport',
 currency:rub?'RUB':'CNY',unique_profiles:profiles.size,counts,results};
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(output)+'\n');
console.log(JSON.stringify({counts,failures:results.filter(r=>!r.pass).map(({id,error,reason})=>({id,error,reason}))}));
if(results.some(row=>!row.pass))process.exitCode=1;

