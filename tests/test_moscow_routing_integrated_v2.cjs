'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const engine=require('../ai_ds/web_demo/shanghai-engine.js');
const dates=['2026-11-21','2026-11-22','2026-11-23'];
function point(id,lat,lon,kind='attraction',price=100){return {
  id,name:id,kind,lat,lon,categories:kind==='restaurant'?['cafes']:['museums'],
  price_cny:price,duration_minutes:kind==='restaurant'?50:50,
  opening:{open:'08:00',close:'22:00',closed_weekdays:[],by_date:{}},
};}
function fixture(){
 const pois=[
  point('a',55.751,37.62),point('b',55.752,37.621),point('c',55.753,37.622),
  point('morning',55.750,37.618),point('d',55.752,37.619),point('far',55.758,37.67),
  point('coffee',55.7511,37.6201,'restaurant',250),
  point('canteen',55.7512,37.6202,'restaurant',500),
  point('restaurant',55.7513,37.6203,'restaurant',650),
 ];
 pois.find(p=>p.id==='coffee').food_type='coffee_shop';
 pois.find(p=>p.id==='coffee').meal_suitability={breakfast:'possible',lunch:'unlikely',dinner:'unlikely'};
 pois.find(p=>p.id==='canteen').food_type='canteen';
 pois.find(p=>p.id==='canteen').meal_suitability={breakfast:'possible',lunch:'likely',dinner:'likely'};
 pois.find(p=>p.id==='restaurant').food_type='restaurant';
 pois.find(p=>p.id==='restaurant').meal_suitability={breakfast:'possible',lunch:'likely',dinner:'likely'};
 pois.find(p=>p.id==='morning').opening.by_date={
  [dates[0]]:{open:'08:00',close:'11:00'},
  [dates[1]]:{open:'08:00',close:'11:00'},
  [dates[2]]:{open:'08:00',close:'11:00',closed:true},
 };
 const coords=Object.fromEntries(pois.map(p=>[p.id,p]));
 const km=(a,b)=>engine.distance(coords[a],coords[b]);
 const profiles={walk:{},transit:{},taxi:{}};
 for(const from of pois){
  for(const mode of Object.keys(profiles)){
   profiles[mode][from.id]={};
   for(const to of pois)if(to!==from){
    const metres=Math.round(km(from.id,to.id));
    const time=mode==='walk'?Math.max(2,Math.ceil(metres/70)):mode==='transit'?Math.max(17,Math.ceil(metres/400)):Math.max(14,Math.ceil(metres/350)+8);
    const price=mode==='walk'?0:mode==='transit'?75:Math.max(420,Math.ceil((280+metres/1000*35+time*11)/10)*10);
    profiles[mode][from.id][to.id]={mode,leg_profile:mode,source:'fixture',basis:'routing_provider',
      distance_m:metres,transport_cost_cny:price,transport_cost_basis:mode==='walk'?'no_fare':'synthetic_fare_estimate',
      base_minutes:time-(mode==='taxi'?8:0),buffer_minutes:mode==='taxi'?8:0,planned_minutes:time};
   }
  }
 }
 return {pois,leg_profiles:profiles,compact_waits:true,currency:'RUB'};
}
function opts(override={}){return {
 start_date:dates[0],days:3,day_start:'09:00',day_end:'20:00',
 day_settings:{[dates[0]]:{day_start:'14:10',day_end:'20:00'},[dates[1]]:{day_start:'09:00',day_end:'20:00'},
 [dates[2]]:{day_start:'09:00',day_end:'19:00'}},
 transport:'walk',transport_modes:['walk'],primary_interests:['museums'],secondary_interests:['cafes'],
 pace:'balanced',budget_cny:15000,required_ids:['morning'],
 group:{adult:1,child:0,senior:0,pet:0}, ...override,
};}
const visitRows=plan=>plan.days.flatMap(d=>d.events.filter(e=>e.type==='visit'||e.type==='meal'));
const transitRows=plan=>plan.days.flatMap(d=>d.events.filter(e=>e.type==='travel'));
test('three days have separate, enforced windows and full middle day',()=>{
 const data=fixture(),options=opts(),plan=engine.planTrip(data,options);
 assert.equal(plan.checks.time,'passed',JSON.stringify(plan.warnings));
 for(let i=0;i<3;i++)for(const e of plan.days[i].events){
  const start=i===0?850:540,end=i===2?1140:1200;
  assert(e.start_min>=start&&e.end_min<=end,`${i} ${JSON.stringify(e)}`);
 }
 const morning=plan.days[1].events.find(e=>e.poi_id==='morning');
 assert(morning&&morning.start_min<850,'The morning-only required POI must fit into full second day');
});
test('no available walk/transit/taxi outside chosen walking-only mode',()=>{
 const p=engine.planTrip(fixture(),opts({days:1,day_settings:{[dates[0]]:{day_start:'09:00',day_end:'20:00'}},required_ids:['a','b'],secondary_interests:[]}));
 assert(p.days[0].visit_ids.includes('a')&&p.days[0].visit_ids.includes('b'));
 assert(transitRows(p).every(e=>e.mode==='walk'));
});
test('short auxiliary walk is possible in taxi-only itinerary',()=>{
 const p=engine.planTrip(fixture(),opts({days:1,day_settings:{[dates[0]]:{day_start:'09:00',day_end:'20:00'}},required_ids:['a','b'],transport:'taxi',transport_modes:['taxi'],secondary_interests:[]}));
 assert(p.days[0].visit_ids.includes('a')&&p.days[0].visit_ids.includes('b'));
 assert(transitRows(p).some(e=>e.mode==='walk'),'A very short segment should not summon a taxi');
 assert(transitRows(p).every(e=>e.mode==='taxi'||e.mode==='walk'));
});
test('dinner never uses coffee-only restaurant with unsuitable menu',()=>{
 const plan=engine.planTrip(fixture(),opts({days:1,day_settings:{[dates[0]]:{day_start:'15:20',day_end:'21:00'}},required_ids:['a','b'],secondary_interests:['cafes']}));
 for(const event of plan.days[0].events.filter(e=>e.meal_kind==='dinner'))assert.notEqual(event.poi_id,'coffee');
});
test('known model fares contribute to budget exactly once',()=>{
 const plan=engine.planTrip(fixture(),opts({days:1,day_settings:{[dates[0]]:{day_start:'09:00',day_end:'21:00'}},required_ids:['a','far'],transport:'taxi',transport_modes:['taxi'],budget_cny:4000}));
 const legs=transitRows(plan),all=visitRows(plan);
 assert(legs.some(e=>e.mode==='taxi'&&e.transport_cost_cny>=420),JSON.stringify(legs));
 const cost=all.reduce((s,e)=>s+e.price_cny,0)+legs.reduce((s,e)=>s+e.transport_cost_cny,0);
 assert.equal(Math.round(cost*100)/100,plan.summary.total_cost_cny);
 assert(plan.summary.total_cost_cny<=4000);
});
test('transit-only can use short walk connectors, but uses transit for long stages',()=>{
 const data=fixture(),p=engine.planTrip(data,opts({days:1,day_settings:{[dates[0]]:{day_start:'09:00',day_end:'21:00'}},
   required_ids:['a','b','far'],transport:'transit',transport_modes:['transit'],budget_cny:6000}));
 const modes=transitRows(p).map(e=>e.mode);
 assert(modes.includes('walk'),JSON.stringify(modes));
 assert(modes.includes('transit'),JSON.stringify(modes));
 assert(modes.every(mode=>['walk','transit'].includes(mode)));
});
test('mixed mode chooses per leg, without forcing one mode all day',()=>{
 const data=fixture(),p=engine.planTrip(data,opts({days:1,day_settings:{[dates[0]]:{day_start:'09:00',day_end:'21:00'}},
   required_ids:['a','b','far'],transport:'mixed',transport_modes:['walk','transit','taxi'],budget_cny:6000}));
 const modes=new Set(transitRows(p).map(e=>e.mode));
 assert(modes.has('walk'),JSON.stringify([...modes]));
 assert(modes.has('transit')||modes.has('taxi'),JSON.stringify([...modes]));
});
test('tight budget selects a viable cheap dinner over expensive nearby restaurant',()=>{
 const data=fixture();data.pois.find(p=>p.id==='restaurant').price_cny=1800;
 const p=engine.planTrip(data,opts({days:1,day_settings:{[dates[0]]:{day_start:'15:20',day_end:'21:00'}},
   required_ids:['a','b'],secondary_interests:['cafes'],budget_cny:1200}));
 assert(!visitRows(p).some(e=>e.poi_id==='restaurant'));
 assert(p.summary.total_cost_cny<=1200);
});
test('five-day request preserves its first/last and full middle windows',()=>{
 const windows={
  [dates[0]]:{day_start:'14:10',day_end:'20:00'},
  [dates[1]]:{day_start:'09:00',day_end:'20:00'},
  [dates[2]]:{day_start:'09:00',day_end:'20:00'},
  '2026-11-24':{day_start:'09:00',day_end:'20:00'},
  '2026-11-25':{day_start:'09:00',day_end:'18:30'},
 };
 const p=engine.planTrip(fixture(),opts({days:5,day_settings:windows,budget_cny:18000}));
 assert.equal(p.days.length,5);
 assert.equal(p.checks.time,'passed');
 for(const day of p.days){const start=Number(windows[day.date].day_start.slice(0,2))*60+Number(windows[day.date].day_start.slice(3));
  const end=Number(windows[day.date].day_end.slice(0,2))*60+Number(windows[day.date].day_end.slice(3));
  for(const e of day.events){assert(e.start_min>=start);assert(e.end_min<=end);}
 }
});
test('unknown prices are never treated as zero-cost free tickets',()=>{
 const poi=point('unknown',55.75,37.62,'attraction',null);
 assert(Number.isNaN(engine.groupPrice(poi,{adult:1,child:0,senior:0,pet:0})));
});
