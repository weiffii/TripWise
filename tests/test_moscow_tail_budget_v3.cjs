'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const api=require('../ai_ds/web_demo/shanghai-engine.js');
const dates=['2026-11-21','2026-11-22','2026-11-23','2026-11-24'];
function venue(id,kind,cat,price,d,lon,opens='09:00',closes='21:00'){
 return {id,name:id,kind,categories:[cat],price_cny:price,duration_minutes:d,lat:55.75,lon,
   opening:{open:opens,close:closes,by_date:{},closed_weekdays:[]}};
}
function fixture(dinnerPrice=2700){
 const pois=[];
 for(let k=0;k<4;k++){
  const lon=37.61+k*.02;
  pois.push(venue(`morning${k}`,'attraction','museums',500,30,lon+.0005,'09:00','13:00'));
  pois.push(venue(`park${k}`,'attraction','nature',0,55,lon+.001,'09:00','21:00'));
  for(let j=0;j<5;j++)pois.push(venue(`heritage${k}-${j}`,'attraction','history',j%3?0:100,50,lon+.002+j*.0005,j%2?'14:00':'09:00','21:00'));
  pois.push(venue(`breakfast${k}`,'restaurant','cafes',500,50,lon+.0002,'07:00','11:30'));
  pois.push(venue(`lunch${k}`,'restaurant','cafes',650,50,lon+.0008,'12:00','15:00'));
  pois.push(venue(`dinner${k}`,'restaurant','cafes',dinnerPrice,50,lon+.0012,'17:00','22:00'));
 }
 const walk={};for(const a of pois){walk[a.id]={};for(const b of pois){if(a===b)continue;
  const m=api.distance(a,b),min=Math.max(2,Math.ceil(m/65));walk[a.id][b.id]={mode:'walk',leg_profile:'walk',source:'fixture',basis:'routing_provider',distance_m:Math.ceil(m),base_minutes:min,planned_minutes:min,buffer_minutes:0,transport_cost_cny:0};
 }}
 return {pois,currency:'RUB',compact_waits:true,leg_profiles:{walk},
 day_anchors:Object.fromEntries(dates.map((day,i)=>[day,{lat:55.75,lon:37.61+i*.02,radius_m:2000}]))};
}
function options(budget=9400){return {start_date:dates[0],days:4,day_start:'09:00',day_end:'20:00',
 day_settings:Object.fromEntries(dates.map(date=>[date,{day_start:'09:00',day_end:'20:00'}])),
 pace:'balanced',transport:'walk',transport_modes:['walk'],primary_interests:['museums'],secondary_interests:['cafes'],
 budget_cny:budget,required_ids:[],group:{adult:1,child:0,senior:0,pet:0}};}

test('when expensive dinners are unaffordable, 4 days remain active and cheap meals are distributed',()=>{
 const result=api.planTrip(fixture(),options());
 assert(result.days.every(day=>day.visit_ids.length>=4),JSON.stringify(result.days.map(d=>d.visit_ids)));
 assert(result.days.every(day=>day.events.at(-1)?.end_min>=17*60),JSON.stringify(result.days.map(d=>d.events.at(-1)?.end_min)));
 assert(result.days.every(day=>day.meal_ids.length>=2),JSON.stringify(result.days.map(d=>d.meal_ids)));
 assert(!result.days.flatMap(day=>day.events).some(e=>e.poi_id?.startsWith('dinner')));
 assert(result.summary.total_cost_cny<=options().budget_cny);
 assert.equal(result.checks.budget,'passed');
 assert.equal(result.checks.time,'passed');
 assert.equal(result.checks.no_duplicates,'passed');
 assert.equal(result.checks.meals,'failed'); // never invent a free dinner
 assert(result.summary.optimizer.late_window_insertions>0);
});

test('affordable dinners remain a real option without dropping required meals',()=>{
 const result=api.planTrip(fixture(250),options());
 assert(result.days.every(day=>day.visit_ids.length>=1));
 assert(result.summary.total_cost_cny<=options().budget_cny);
 assert(result.days.every(day=>day.meal_ids.length>=2));
 assert.equal(result.checks.time,'passed');
 assert.equal(result.checks.budget,'passed');
});
