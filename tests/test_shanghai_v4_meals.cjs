/* Синтетические регрессии правил еды v4, без сети. */
'use strict';
const assert=require('node:assert/strict');
const api=require('../ai_ds/web_demo/shanghai-engine.js');
const group={adult:1,child:0,senior:0,pet:0};
const options={primary_interests:['museums'],secondary_interests:['history'],pace:'balanced',days:1,start_date:'2026-11-19',day_start:'09:00',day_end:'19:00',budget_cny:2000,transport:'mixed',group,required_ids:['a']};
function poi(id,kind,extra={}){return {id,name:id,kind,categories:kind==='restaurant'?['cafes']:['museums','history'],lat:31.23,lon:121.47,river_bank:'west',duration_minutes:60,price_cny:20,rating:4.3,review_count:200,opening:{open:'08:00',close:'23:00',closed_weekdays:[]},family_friendly:true,senior_friendly:true,pet_friendly:true,...extra};}
function dataset(){return {pois:[poi('a','attraction'),poi('b','attraction',{lon:121.471}),poi('c','attraction',{lon:121.472}),...Array.from({length:8},(_,i)=>poi('food'+i,'restaurant',{lon:121.4702+i*.0001,duration_minutes:50,rating:4.5-i*.01}))]};}
function meals(p){return p.days.flatMap(d=>d.events).filter(e=>e.type==='meal');}
function validate(p,o){assert.equal(p.checks.time,'passed');assert.equal(p.checks.budget,'passed');assert.equal(p.checks.opening_hours,'passed');assert.equal(p.checks.no_duplicates,'passed');assert(p.summary.total_cost_cny<=o.budget_cny);for(const day of p.days)for(let i=0;i<day.events.length;i++){const e=day.events[i];if(i)assert(e.start_min>=day.events[i-1].end_min);if(e.type==='meal')assert(e.end_min-e.start_min>=50&&e.end_min-e.start_min<=60);assert(!['rest','wait'].includes(e.type));}}
let passed=0;function test(name,fn){fn();passed++;console.log('PASS '+name);}
test('dinner is requested strictly after 17:00 including a 17:01 day end',()=>{
 for(const end of ['17:00','17:01','17:30','19:00']){const o={...options,day_end:end},p=api.planTrip(dataset(),o);validate(p,o);const dinner=meals(p).filter(e=>e.meal_kind==='dinner');assert.equal(dinner.length,end==='17:00'?0:1,end);if(dinner.length){assert(dinner[0].start_min>=960);assert(dinner[0].end_min<=Number(end.slice(0,2))*60+Number(end.slice(3)));}}
});
test('a late one-hour day can contain dinner and honestly lacks museum time',()=>{
 const d=dataset();d.pois.filter(p=>p.kind==='attraction').forEach(p=>p.duration_minutes=90);const o={...options,day_start:'18:00',day_end:'19:00'},p=api.planTrip(d,o);validate(p,o);assert.equal(meals(p).filter(e=>e.meal_kind==='dinner').length,1);assert.equal(p.checks.required_places,'failed');assert(p.unplaced_required_ids.includes('a'));
});
test('breakfast is required at 11:30 but not at noon',()=>{
 for(const start of ['11:30','12:00']){const o={...options,day_start:start},p=api.planTrip(dataset(),o);validate(p,o);assert.equal(meals(p).filter(e=>e.meal_kind==='breakfast').length,start==='11:30'?1:0);}
});
test('a missing dinner is reported rather than pretending meals passed',()=>{
 const d=dataset();d.pois.filter(p=>p.kind==='restaurant').forEach(p=>p.opening.close='15:00');const p=api.planTrip(d,options);assert.equal(p.checks.meals,'failed');assert(p.warnings.some(w=>w.includes('\u0423\u0436\u0438\u043d')));assert.notEqual(p.status,'demo');
});
test('a five-star distant restaurant cannot move attraction selection or visits',()=>{
 const d=dataset(),baseline=api.planTrip(d,options);d.pois.push(poi('remote-food','restaurant',{lat:31.27,lon:121.55,rating:5,review_count:1000000}));const changed=api.planTrip(d,options);assert.deepEqual(changed.summary.stage1_selected_ids,baseline.summary.stage1_selected_ids);assert.deepEqual(changed.days.map(d=>d.visit_ids),baseline.days.map(d=>d.visit_ids));assert(!changed.selected_ids.includes('remote-food'));validate(changed,options);
});
test('restaurants never cross the river merely because they are geometrically close',()=>{
 const d=dataset();d.pois.push(poi('opposite-food','restaurant',{river_bank:'east',rating:5,review_count:1000000}));const p=api.planTrip(d,options);assert(!p.selected_ids.includes('opposite-food'));validate(p,options);
});
test('meal departure can be delayed with an honest gap, without fake rest or visits',()=>{
 const d=dataset();d.pois=d.pois.filter(p=>p.id==='a'||p.kind==='restaurant');const o={...options,day_start:'14:00'},p=api.planTrip(d,o);validate(p,o);assert.equal(meals(p).filter(e=>e.meal_kind==='dinner').length,1);assert.equal(p.checks.no_gaps,'failed');assert.equal(p.checks.time,'passed');assert.equal(p.status,'demo');
});
test('published popularity input affects tie-quality ranking without outranking interests',()=>{
 const d=dataset();d.pois.find(p=>p.id==='b').lon=121.47;d.pois.find(p=>p.id==='b').review_count=100000;const ranks=api.rankPois(d,{...options,required_ids:[]});assert(ranks.findIndex(p=>p.poi_id==='b')<ranks.findIndex(p=>p.poi_id==='a'));
});
test('the supplied transit graph is cached and its exact planned time and instructions reach schedule',()=>{
 const before=globalThis.TripWiseTransit;let builds=0,calls=0;
 const instruction={mode:'metro',line_id:'fixture-line',line_name:'Fixture',from_name:'A',to_name:'B',direction:'B',stop_count:1,base_minutes:25,synthetic:false};
 globalThis.TripWiseTransit={createRouter:()=>{builds++;return {route:(a,b,options)=>{calls++;assert.deepEqual(options.group,group);return {base_minutes:25,planned_minutes:30,buffer_minutes:5,transport_cost_cny:3,distance_m:500,mode:'transit',source:'REAL_METRO_TOPOLOGY_ESTIMATED_TIMES',reason:'Synthetic fixture graph',instructions:[instruction],geometry_segments:[]};}};}};
 try{const d=dataset();d.transport={fixture:true};const o={...options,transport:'transit'},p=api.planTrip(d,o);validate(p,o);api.rankPois(d,o);assert.equal(builds,1);assert(calls>0);const legs=p.days.flatMap(d=>d.events).filter(e=>e.type==='travel');assert(legs.length>0);for(const leg of legs){assert.equal(leg.end_min-leg.start_min,30);assert.equal(leg.source,'REAL_METRO_TOPOLOGY_ESTIMATED_TIMES');assert.deepEqual(leg.instructions,[instruction]);}}
 finally{if(before===undefined)delete globalThis.TripWiseTransit;else globalThis.TripWiseTransit=before;}
});
test('a graph with no transit path does not silently produce fabricated transit',()=>{
 const d=dataset();assert.equal(api.transportLeg(d.pois[0],d.pois[1],'transit',group,'2026-11-19',null,{route:()=>null}),null);
});
test('a deferred meal recomputes transit at actual departure and respects service closure',()=>{
 const before=globalThis.TripWiseTransit;const departures=[];
 globalThis.TripWiseTransit={createRouter:()=>({route:(a,b,o)=>{departures.push(o.departure_min);if(o.departure_min>=15*60)return null;return {base_minutes:10,planned_minutes:15,buffer_minutes:5,transport_cost_cny:3,distance_m:500,mode:'transit',source:'SYNTHETIC_BUS',reason:'Fixture service closes at 15:00',instructions:[],geometry_segments:[]};}})};
 try{const d=dataset();d.pois=d.pois.filter(p=>p.id==='a'||p.kind==='restaurant');d.pois.find(p=>p.id==='a').duration_minutes=50;d.transport={fixture:true};const o={...options,transport:'transit',day_start:'14:00',day_end:'19:00'},p=api.planTrip(d,o);validate(p,o);assert(departures.some(v=>v<900));assert(departures.some(v=>v>=900));assert(!meals(p).some(e=>e.meal_kind==='dinner'));assert.equal(p.checks.meals,'failed');assert(!p.days.flatMap(d=>d.events).some(e=>e.type==='travel'&&e.start_min>=900));}
 finally{if(before===undefined)delete globalThis.TripWiseTransit;else globalThis.TripWiseTransit=before;}
});
test('an expensive high-rated breakfast cannot spend the reserve for cheap lunch and dinner',()=>{
 const d=dataset();d.pois.push(poi('expensive-food','restaurant',{price_cny:70,rating:5,review_count:1000000,duration_minutes:50}));const o={...options,budget_cny:100},p=api.planTrip(d,o);validate(p,o);assert(!p.selected_ids.includes('expensive-food'));assert.equal(meals(p).length,3);assert.equal(p.checks.meals,'passed');assert(p.selected_ids.includes('a'));
});
test('optional tickets cannot consume money reserved for all remaining meals',()=>{
 const o={...options,budget_cny:80},p=api.planTrip(dataset(),o);validate(p,o);assert.equal(meals(p).length,3);assert.equal(p.checks.meals,'passed');assert.deepEqual(p.days[0].visit_ids,['a']);assert.equal(p.summary.total_cost_cny,80);
});
test('failed previous-day meals release their reserve rather than blocking later meals forever',()=>{
 const d=dataset();d.pois.filter(p=>p.kind==='restaurant').forEach(p=>p.opening.closed_weekdays=[4]);const o={...options,days:2,budget_cny:80},p=api.planTrip(d,o);validate(p,o);assert.equal(p.days[0].meal_ids.length,0);assert.equal(p.days[1].meal_ids.length,3);assert.equal(p.checks.meals,'failed');assert(p.warnings.length>0);
});
console.log(JSON.stringify({passed,network_requests:0}));
