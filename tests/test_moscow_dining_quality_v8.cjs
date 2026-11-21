'use strict';
const {test}=require('node:test'), assert=require('node:assert/strict');
const engine=require('../ai_ds/web_demo/shanghai-engine.js');
function poi(id,kind,price,lon,foodType=null,rating=null){
 const p={id,name:id,kind,categories:[kind==='restaurant'?'cafes':'museums'],
  price_cny:price,lat:55.75,lon,duration_minutes:kind==='restaurant'?50:90,
  opening:{open:'09:00',close:'22:00',closed_weekdays:[],by_date:{}}};
 if(foodType)p.food_type=foodType;
 if(rating!==null)p.rating=rating;
 if(kind==='restaurant')p.meal_suitability={breakfast:'possible',lunch:'likely',dinner:'likely'};
 return p;
}
function data(){return {currency:'RUB',compact_waits:true,pois:[
 poi('museum','attraction',1200,37.620),poi('park','attraction',0,37.621),
 poi('burger-king','restaurant',350,37.6211,'fast_food',4.3),
 poi('restaurant','restaurant',1600,37.62112,'restaurant',4.6),
 poi('canteen','restaurant',250,37.6213,'canteen',4.2),
 ]};}
function options(budget){return {start_date:'2026-11-21',days:1,day_start:'15:30',day_end:'21:30',
  pace:'balanced',transport:'walk',transport_modes:['walk'],primary_interests:['museums'],
  secondary_interests:['cafes'],required_ids:[],budget_cny:budget,
  group:{adult:1,child:0,senior:0,pet:0}};}
function plan(budget,dataset=data()){return engine.planTrip(dataset,options(budget));}
function dinner(result){return result.days[0].events.find(e=>e.meal_kind==='dinner');}

test('large budget prefers suitable sit-down dinner instead of cheap fast food',()=>{
 const p=plan(30000);assert.equal(dinner(p)?.poi_id,'restaurant');
 assert.equal(p.checks.budget,'passed');assert.equal(p.checks.time,'passed');
 assert(p.summary.total_cost_cny<=30000);
});
test('low budget still selects affordable food rather than overspending',()=>{
 const p=plan(1600);assert.equal(dinner(p)?.poi_id,'canteen');
 assert(p.summary.total_cost_cny<=1600);
 assert.equal(p.checks.budget,'passed');
});
test('more expensive venue is not automatically better when its rating is materially lower',()=>{
 const d=data();d.pois.push(poi('pricey-but-bad','restaurant',2600,37.62111,'restaurant',2.5));
 const p=plan(30000,d);assert.equal(dinner(p)?.poi_id,'restaurant');
});
test('if no full-service venue is feasible, high budget retains affordable meal and optional visits',()=>{
 const d=data();d.pois=d.pois.filter(p=>p.id!=='restaurant');
 const p=plan(30000,d);assert(dinner(p)?.poi_id);
 assert.equal(p.checks.time,'passed');assert.equal(p.checks.budget,'passed');
});

test('three-day generous budget reserves dinner quality across all days and uses funds productively',()=>{
 const days=3,pois=[],walk={};
 for(let day=0;day<days;day++){
  const lon=37.61+day*.025;
  for(let i=0;i<4;i++){
   const site=poi(`museum-${day}-${i}`,'attraction',i%2?1200:1800,lon+i*.001);
   pois.push(site);
  }
  for(let i=0;i<3;i++){
   pois.push(poi(`burger-${day}-${i}`,'restaurant',300,lon+i*.0005,'fast_food'));
   pois.push(poi(`sitdown-${day}-${i}`,'restaurant',i===0?1200:1800,
     lon+i*.0005+.0001,'restaurant'));
  }
 }
 for(const a of pois){walk[a.id]={};for(const b of pois){
  if(a.id===b.id)continue;
  const metres=engine.distance(a,b),travel=Math.max(2,Math.ceil(metres/64));
  walk[a.id][b.id]={mode:'walk',source:'fixture',basis:'routing_provider',
    planned_minutes:travel,base_minutes:travel,buffer_minutes:0,
    transport_cost_cny:0,distance_m:metres};
 }}
 const settings={...options(30000),days:3,day_start:'09:00',day_end:'21:00'};
 const result=engine.planTrip({currency:'RUB',compact_waits:true,pois,
   leg_profiles:{walk}},settings);
 assert.equal(result.days.length,3);
 assert.equal(result.summary.visit_count,12);
 for(const day of result.days){
  const dinner=day.events.find(e=>e.meal_kind==='dinner');
  assert(dinner?.poi_id.startsWith('sitdown-'),
    'each day should have a restaurant dinner, not early days only: '+JSON.stringify(day.meal_ids));
 }
 assert(result.summary.total_cost_cny>27000,
   'spend on desirable visits and meals should approach a generous available budget');
 assert(result.summary.total_cost_cny<=30000);
 assert.equal(result.checks.budget,'passed');
 assert.equal(result.checks.time,'passed');
 assert.equal(result.checks.no_duplicates,'passed');
});
