'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const engine=require('../ai_ds/web_demo/shanghai-engine.js');
function poi(id, type, base, categories, lon, extra={}){
 return {id,name:id,kind:'attraction',categories,lat:55.75,lon,
  price_cny:0,duration_minutes:base,visit_type:type,duration_source:'synthetic',
  opening:{open:'08:00',close:'22:00',closed_weekdays:[],by_date:{}},...extra};
}
const mall=poi('mall','shopping_mall',160,['shopping'],37.620);
const museum=poi('museum','museum',110,['museums'],37.621);
const shop=poi('shop','shop',45,['shopping'],37.622);
function data(){const points=[mall,museum,shop],walk={};
 for(const a of points){walk[a.id]={};for(const b of points)if(a!==b){
  walk[a.id][b.id]={mode:'walk',planned_minutes:5,base_minutes:5,buffer_minutes:0,
   transport_cost_cny:0,distance_m:400,source:'fixture',basis:'test_matrix',leg_profile:'walk'};
 }}
 return {pois:points,currency:'RUB',compact_waits:true,leg_profiles:{walk}};
}
function options(pace){return {start_date:'2026-11-21',days:1,day_start:'08:00',day_end:'22:00',
  pace,transport:'walk',transport_modes:['walk'],primary_interests:['shopping','museums'],
  secondary_interests:[],required_ids:['mall','museum','shop'],budget_cny:20000,
  group:{adult:1,child:0,senior:0,pet:0}};}
function visitMinutes(plan,id){const entry=plan.days[0].events.find(e=>e.type==='visit'&&e.poi_id===id);
 assert(entry,`${id} was not placed: ${JSON.stringify(plan.warnings)}`);
 return entry.end_min-entry.start_min;
}
test('museum time and mall time follow relaxed > balanced > intensive',()=>{
 const values=['relaxed','balanced','intensive'].map(pace=>{
  const plan=engine.planTrip(data(),options(pace));
  assert.equal(plan.checks.time,'passed');
  return {mall:visitMinutes(plan,'mall'),museum:visitMinutes(plan,'museum'),shop:visitMinutes(plan,'shop')};
 });
 assert(values[0].mall>values[1].mall&&values[1].mall>values[2].mall,JSON.stringify(values));
 assert(values[0].museum>values[1].museum&&values[1].museum>values[2].museum,JSON.stringify(values));
 for(const row of values){assert(row.mall>=120&&row.mall<=180,JSON.stringify(values));assert(row.museum>=90,JSON.stringify(values));}
 assert(values[1].shop<90,'Small store should not be forced to mall duration');
});
test('explicit user duration is exact for every pace',()=>{
 const scenario=data();scenario.pois=scenario.pois.map(p=>p.id==='mall'?{...p,duration_minutes:75,duration_locked:true}:p);
 for(const pace of ['relaxed','balanced','intensive']){
  const plan=engine.planTrip(scenario,options(pace));
  assert.equal(visitMinutes(plan,'mall'),75,pace);
 }
});
test('confirmed source duration is not overwritten by mall default',()=>{
 const scenario=data();scenario.pois=scenario.pois.map(p=>p.id==='mall'?{...p,duration_minutes:65,duration_source:'source'}:p);
 const plan=engine.planTrip(scenario,options('balanced'));
 assert.equal(visitMinutes(plan,'mall'),65);
});
test('recomputing route via agent day-pace edit scales durations consistently',()=>{
 const initial=engine.planTrip(data(),options('balanced'));
 const result=engine.editTrip(data(),options('balanced'),initial,[{op:'set_day_pace',day:'2026-11-21',pace:'relaxed'}]);
 assert(result.plan.days[0].events.some(e=>e.poi_id==='mall'));
 assert(visitMinutes(result.plan,'mall')>visitMinutes(initial,'mall'));
 assert(visitMinutes(result.plan,'mall')<=180);
});
test('Shanghai records without Moscow visit metadata retain their original behavior',()=>{
 const original={duration_minutes:60};
 assert.equal(engine.duration(original,'relaxed'),70);
 assert.equal(engine.duration(original,'balanced'),60);
 assert.equal(engine.duration(original,'intensive'),55);
});
