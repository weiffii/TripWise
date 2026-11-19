/* Regression: one offline Shanghai algorithm, 100 synthetic/demo POIs. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const api=require('../ai_ds/web_demo/shanghai-engine.js');
const data=JSON.parse(fs.readFileSync(path.join(__dirname,'../data/demo/shanghai/pois.json'),'utf8'));
const opts={primary_interests:['museums'],secondary_interests:['history','nature'],pace:'balanced',days:2,start_date:'2026-11-18',day_start:'09:00',day_end:'19:00',budget_cny:1600,transport:'mixed',group:{adult:1,child:0,senior:0,pet:0},required_ids:[]};
let passed=0;
function test(name,fn){fn();passed++;console.log('PASS '+name);}
const events=plan=>plan.days.flatMap(d=>d.events);
function invariants(result,x){
 assert.equal(result.checks.time,'passed');
 assert.equal(result.checks.budget,'passed');
 assert.equal(result.checks.opening_hours,'passed');
 assert(['passed','failed'].includes(result.checks.no_gaps));
 assert.equal(result.checks.no_duplicates,'passed');
 assert(result.summary.total_cost_cny<=x.budget_cny+1e-6);
 const used=new Set();
 for(const day of result.days){
  for(let i=0;i<day.events.length;i++){
   const e=day.events[i];assert(e.end_min>e.start_min);assert(e.start_min>=540||x.day_start!=='09:00');
   if(i)assert(e.start_min>=day.events[i-1].end_min);
   if(e.type==='visit'||e.type==='meal'){assert(!used.has(e.poi_id));used.add(e.poi_id);}
   if(e.type==='travel'){assert.equal(e.end_min-e.start_min,e.planned_minutes);assert(e.buffer_minutes>0);}
  }
 }
}
test('expanded 500-POI catalogue preserves unique sourced identities',()=>{
 assert.equal(data.pois.length,500);assert.equal(new Set(data.pois.map(p=>p.id)).size,500);
 assert(data.pois.every(p=>p.source_url&&p.provenance));
 assert(data.pois.some(p=>p.provenance.identity==='real_OSM_POI'));
 assert(data.pois.every(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon)));
});
test('expanded default profile keeps hard constraints and reports missing food coverage',()=>{
 const plan=api.planTrip(data,opts);invariants(plan,opts);
 assert(plan.days.every(d=>d.visit_ids.length>=2));
 assert.deepEqual(plan.uncovered_interests,[]);
 for(const day of plan.days){if(day.meal_ids.length<3){assert.equal(plan.checks.meals,'failed');assert.notEqual(plan.status,'demo');assert(day.warnings.length>0);}}
});
test('oversubscribed budget+required still builds available route',()=>{
 const x={...opts,days:4,budget_cny:1000,group:{adult:2,child:0,senior:0,pet:0},primary_interests:['museums','history','shopping'],secondary_interests:[],required_ids:['shanghai-demo:yu-garden','shanghai-demo:history-museum','shanghai-demo:bund','shanghai-demo:m50']};
 const p=api.planTrip(data,x);invariants(p,x);assert(p.summary.visit_count>=2);assert.notEqual(p.status,'infeasible');
 assert(p.summary.total_cost_cny<=1000);assert(p.checks.required_places==='passed'||p.unplaced_required_ids.every(id=>p.warnings.some(w=>w.includes(data.pois.find(v=>v.id===id).name))));
});
test('zero budget cannot pretend a complete itinerary with required food',()=>{
 const x={...opts,days:1,budget_cny:0};const p=api.planTrip(data,x);invariants(p,x);
 assert.equal(p.summary.total_cost_cny,0);assert.notEqual(p.status,'demo');assert.equal(p.checks.meals,'failed');assert(p.warnings.some(w=>w.includes('приёмов пищи')));
});
test('group ticket prices incorporate children and seniors',()=>{
 const p=data.pois.find(v=>v.price_cny===100&&v.kind==='attraction')||data.pois.find(v=>v.price_cny>0&&v.kind==='attraction');
 const price=api.groupPrice(p,{adult:1,child:1,senior:1,pet:0});assert(Math.abs(price-p.price_cny*(1+.5+.7))<1e-7);
});
test('date filters seasonal objects off season and weekend high crowd',()=>{
 const seasonalData=JSON.parse(JSON.stringify(data));
 const season=seasonalData.pois.find(p=>p.kind==='attraction');season.season_months=[1];
 assert(season);const month=Array.from({length:12},(_,i)=>i+1).find(i=>!season.season_months.includes(i));
 const x={...opts,primary_interests:season.categories.filter(c=>c!=='cafes'),secondary_interests:[],required_ids:[season.id],days:1,day_start:'08:00',day_end:'21:00',budget_cny:5000,start_date:`2026-${String(month).padStart(2,'0')}-11`};
 const p=api.planTrip(seasonalData,x);assert(p.unplaced_required_ids.includes(season.id));
 const crowded=data.pois.find(v=>v.kind==='attraction'&&v.weekend_crowd_level==='high');assert(crowded);
 const weekend=api.planTrip(data,{...x,start_date:'2026-11-21',primary_interests:crowded.categories,required_ids:[crowded.id]});assert(weekend.selected_ids.includes(crowded.id), 'Обязательное место нельзя выбросить только из-за наплыва людей');
});
test('primary interests prioritized but secondary categories still covered',()=>{
 const p=api.planTrip(data,{...opts,primary_interests:['museums'],secondary_interests:['shopping','nature','unusual'],budget_cny:2500,days:3});
 assert.deepEqual(p.uncovered_interests,[]);invariants(p,{...opts,budget_cny:2500,days:3});
});
test('pace changes number and time of visits',()=>{
 const plans=['relaxed','balanced','intensive'].map(pace=>api.planTrip(data,{...opts,pace,budget_cny:5000,days:1}));
 const avg=p=>events(p).filter(e=>e.type==='visit').reduce((n,e)=>n+e.end_min-e.start_min,0)/Math.max(1,p.summary.visit_count);
 assert(avg(plans[0])>avg(plans[2]));
 for(const [i,pace] of ['relaxed','balanced','intensive'].entries()){assert(plans[i].days[0].visit_ids.length<=api.PACES[pace].max);invariants(plans[i],{...opts,pace,budget_cny:5000,days:1});}
});
test('pedestrians never cross river via invented walking leg',()=>{
 const p=api.planTrip(data,{...opts,transport:'walk',budget_cny:10000,days:3});
 const byId=new Map(data.pois.map(p=>[p.id,p]));
 for(const e of events(p).filter(e=>e.type==='travel')){
  assert.equal(e.mode,'walk');assert.equal(byId.get(e.from_poi_id).river_bank,byId.get(e.to_poi_id).river_bank);
 }
});
test('different transports produce different durations and costs',()=>{
 const a=data.pois.find(p=>p.id==='shanghai-demo:shanghai-museum'),b=data.pois.find(p=>p.id==='shanghai-demo:shanghai-tower'),g=opts.group;
 const taxi=api.transportLeg(a,b,'taxi',g,'2026-11-18'),transit=api.transportLeg(a,b,'transit',g,'2026-11-18');
 assert(taxi.planned_minutes!==transit.planned_minutes);assert(taxi.transport_cost_cny>transit.transport_cost_cny);
 assert.equal(api.transportLeg(a,b,'walk',g,'2026-11-18'),null);
});
test('family, seniors and pets alter eligibility or mode',()=>{
 const p=api.planTrip(data,{...opts,group:{adult:1,child:1,senior:1,pet:1},transport:'mixed',days:2,budget_cny:5000,primary_interests:['nature'],secondary_interests:['viewpoints']});
 for(const e of events(p).filter(e=>e.type==='visit'||e.type==='meal'))assert.equal(data.pois.find(v=>v.id===e.poi_id).pet_friendly,true);
 for(const e of events(p).filter(e=>e.type==='travel'))assert.notEqual(e.mode,'transit');
});
test('rank responds to priorities; date and data are immutable',()=>{
 const before=JSON.stringify(data),museum=api.rankPois(data,opts),nature=api.rankPois(data,{...opts,primary_interests:['nature'],secondary_interests:[]});
 assert.notDeepEqual(museum.slice(0,10).map(r=>r.poi_id),nature.slice(0,10).map(r=>r.poi_id));api.planTrip(data,opts);assert.equal(JSON.stringify(data),before);
});
test('invalid inputs rejected rather than silent wrong plans',()=>{
 for(const change of [{primary_interests:[],secondary_interests:[]},{budget_cny:-1},{days:0},{group:{adult:0,child:0,senior:0,pet:1}},{day_start:'22:00',day_end:'09:00'},{start_date:'2026-02-30'},{required_ids:['bad']}]){
  assert.throws(()=>api.planTrip(data,{...opts,...change}),Error);
 }
});
test('a cafe interest in a short afternoon window gets a real meal stop in the schedule',()=>{
 const x={...opts,primary_interests:['cafes'],secondary_interests:[],days:1,day_start:'15:00',day_end:'17:00',budget_cny:700};
 const p=api.planTrip(data,x);assert(p.summary.meal_count>=1);assert.deepEqual(p.uncovered_interests,[]);
 assert.equal(p.checks.no_gaps,'passed');assert.equal(p.checks.budget,'passed');
});
test('synthetic currencies are exposed and fixed, not live FX',()=>{
 assert.equal(api.FX_TO_CNY.USD,7.1);assert.equal(api.FX_TO_CNY.RUB,0.087);
});

test('stage 1 selects all mandatory attractions before grouping',()=>{
 const required=['shanghai-demo:yu-garden','shanghai-demo:history-museum','shanghai-demo:m50'];
 const x={...opts,required_ids:required,days:3,budget_cny:5000};
 const selected=api.selectAttractions(api.normalize(data,x));
 for(const id of required)assert(selected.shortlist.some(item=>item.poi.id===id));
 const clustered=api.assignDays(api.normalize(data,x),selected.shortlist);
 assert(clustered.length===3);assert(clustered.every(d=>d.pois.length<=api.PACES.balanced.max));
 const r=api.planTrip(data,x);invariants(r,x);
 for(const id of required)assert(r.selected_ids.includes(id), 'Mandatory missing: '+id);
});
test('restaurants remain near at least one attraction in their day',()=>{
 const r=api.planTrip(data,opts),byId=new Map(data.pois.map(p=>[p.id,p]));
 for(const d of r.days){
  const visits=d.events.filter(e=>e.type==='visit').map(e=>byId.get(e.poi_id));
  for(const e of d.events.filter(e=>e.type==='meal')){
   const meal=byId.get(e.poi_id),nearest=Math.min(...visits.map(p=>api.distance(p,meal)));
   assert(nearest<=2500,meal.name+' too far: '+nearest);
  }
 }
});
test('OSRM road matrix overrides approximated taxi time and distance',()=>{
 const a=data.pois.find(v=>v.id==='shanghai-demo:shanghai-museum');
 const b=data.pois.find(v=>v.id==='shanghai-demo:history-museum');
 const osm={car:{ids:[a.id,b.id],durations:[[0,240],[360,0]],distances:[[0,950],[1200,0]]}};
 const leg=api.transportLeg(a,b,'taxi',opts.group,opts.start_date,osm);
 assert.equal(leg.source,'OSM_OSRM');assert.equal(leg.base_minutes,4);assert.equal(leg.distance_m,950);
 const fallback=api.transportLeg(a,b,'taxi',opts.group,opts.start_date);
 assert.equal(fallback.source,'synthetic_distance_estimate');
});
test('more mandatory sites than available daily capacity yields a warning',()=>{
 const required=data.pois.filter(p=>p.kind==='attraction'&&p.categories.includes('museums')).slice(0,7).map(p=>p.id);
 const x={...opts,days:1,budget_cny:10000,required_ids:required};
 const r=api.planTrip(data,x);assert(r.warnings.some(w=>w.includes('Обязательных мест больше')));
 assert(r.unplaced_required_ids.length>0);
});

console.log(JSON.stringify({passed,network_requests:0,catalog_count:data.pois.length}));
