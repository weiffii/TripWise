/* Shanghai-500: targeted offline regressions, no network calls. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const api=require('../ai_ds/web_demo/shanghai-engine.js');
const group={adult:1,child:0,senior:0,pet:0};
const options={primary_interests:['museums'],secondary_interests:[],pace:'balanced',days:1,start_date:'2026-11-19',day_start:'09:00',day_end:'19:00',budget_cny:1000,transport:'mixed',group,required_ids:[]};
function poi(id,kind='attraction',extra={}){return {id,name:id,kind,categories:kind==='restaurant'?['cafes']:['museums'],lat:31.23,lon:121.47,river_bank:'west',duration_minutes:105,price_cny:20,rating:4.5,review_count:1000,opening:{open:'08:00',close:'23:00',closed_weekdays:[]},family_friendly:true,senior_friendly:true,pet_friendly:true,...extra};}
function fixedTransit(minutes=10,cost=4){return {route:()=>({base_minutes:minutes-2,planned_minutes:minutes,buffer_minutes:2,transport_cost_cny:cost,distance_m:2000,mode:'transit',source:'SYNTHETIC_FIXTURE',instructions:[],geometry_segments:[]})};}
let passed=0;function test(name,fn){fn();passed++;console.log('PASS '+name);}
test('mixed compares complete planned times, including a fast public route for a family',()=>{
 const a=poi('a'),b=poi('b','attraction',{lon:121.49}),g={adult:4,child:0,senior:0,pet:0};
 const leg=api.transportLeg(a,b,'mixed',g,options.start_date,null,fixedTransit(5,4));assert.equal(leg.mode,'transit');assert.equal(leg.planned_minutes,5);
});
test('mixed selects a short walk when it is faster than all transport',()=>{
 const a=poi('a'),b=poi('b','attraction',{lon:121.4701});const leg=api.transportLeg(a,b,'mixed',group,options.start_date,null,fixedTransit(20,4));assert.equal(leg.mode,'walk');
});
test('mixed chooses the fastest affordable mode, rather than rejecting an expensive taxi later',()=>{
 const a=poi('a'),b=poi('b','attraction',{lon:121.52});const osm={car:{ids:['a','b'],durations:[[0,60],[60,0]],distances:[[0,5000],[5000,0]]}};
 const fastest=api.transportLeg(a,b,'mixed',group,options.start_date,osm,fixedTransit(15,4));assert.equal(fastest.mode,'taxi');
 const affordable=api.transportLeg(a,b,'mixed',group,options.start_date,osm,fixedTransit(15,4),600,5);assert.equal(affordable.mode,'transit');
 const free=api.transportLeg(a,b,'mixed',group,options.start_date,osm,fixedTransit(15,4),600,0);assert.equal(free.mode,'walk');
});
test('mixed respects pets and never invents an affordable walk across the river',()=>{
 const a=poi('a'),b=poi('b','attraction',{river_bank:'east',lon:121.49});assert.equal(api.transportLeg(a,b,'mixed',group,options.start_date,null,null,600,0),null);
 const p=api.transportLeg(a,b,'mixed',{...group,pet:1},options.start_date,null,fixedTransit(1,1));assert.equal(p.mode,'taxi');
});
test('average restaurant check drives group spend and retains legacy fallback',()=>{
 const p=poi('food','restaurant',{price_cny:20,avg_check_cny:80}),g={adult:2,child:1,senior:0,pet:0};assert.equal(api.groupPrice(p,g),212);p.avg_check_cny=null;assert.equal(api.groupPrice(p,g),53);
});
test('group average checks and food reserves exclude an unaffordable premium cafe',()=>{
 const d={pois:[poi('a'),...Array.from({length:4},(_,i)=>poi('f'+i,'restaurant',{duration_minutes:50,avg_check_cny:20})),poi('premium','restaurant',{duration_minutes:50,avg_check_cny:90,rating:5,review_count:100000})]};
 const p=api.planTrip(d,{...options,budget_cny:100,required_ids:['a']});assert.equal(p.days[0].meal_ids.length,3);assert(!p.selected_ids.includes('premium'));assert(p.summary.total_cost_cny<=100);
});
test('optional visit rollback can rescue a lunch without deleting a required attraction',()=>{
 const d={pois:[poi('a'),poi('b','attraction',{rating:4.9}),poi('c'),poi('breakfast','restaurant',{duration_minutes:50,opening:{open:'08:00',close:'10:30',closed_weekdays:[]}}),poi('lunch','restaurant',{duration_minutes:50,opening:{open:'11:30',close:'13:00',closed_weekdays:[]}}),poi('dinner','restaurant',{duration_minutes:50,opening:{open:'16:00',close:'22:00',closed_weekdays:[]}})]};
 const p=api.planTrip(d,{...options,required_ids:['a']});assert.equal(p.checks.meals,'passed');assert.equal(p.checks.required_places,'passed');assert(p.selected_ids.includes('a'));assert(p.summary.meal_repair_count>0);
});
test('bounded geographic improvement keeps the day membership and first anchor',()=>{
 const points=[poi('a','attraction',{lon:121.40}),poi('b','attraction',{lon:121.43}),poi('c','attraction',{lon:121.41}),poi('d','attraction',{lon:121.42})],d={pois:points},x=api.normalize(d,options),day=api.assignDays(x,points.map(p=>({poi:p})))[0];
 const length=a=>a.slice(1).reduce((n,p,i)=>n+api.distance(a[i],p),0);assert.equal(day.pois[0].id,'a');assert.deepEqual(day.pois.map(p=>p.id).sort(),['a','b','c','d']);assert(length(day.pois)<length(points));
});
test('warnings no longer enumerate omitted optional names',()=>{
 const d={pois:[poi('a'),poi('OPTIONAL_SHOULD_NOT_BE_NAMED'),...Array.from({length:5},(_,i)=>poi('f'+i,'restaurant',{duration_minutes:50}))]};
 const p=api.planTrip(d,{...options,required_ids:['a'],budget_cny:80});assert(!p.warnings.some(w=>w.includes('OPTIONAL_SHOULD_NOT_BE_NAMED')));assert(!p.warnings.some(w=>w.includes('Не поместились в этот день')));
});
console.log(JSON.stringify({passed,network_requests:0}));
