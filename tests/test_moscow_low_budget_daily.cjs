'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const engine=require('../ai_ds/web_demo/shanghai-engine.js');
const days=['2026-11-21','2026-11-22','2026-11-23'];
function venue(id, day, kind, price, categories, extra={}){
 const base=[37.60,37.635,37.67][day];
 const offset=(id.charCodeAt(0)%11)*.0001;
 return {id,name:id,kind,lat:55.75+day*.003,lon:base+offset,
  categories,price_cny:price,duration_minutes:kind==='restaurant'?50:45,
  opening:{open:'07:00',close:'22:00',closed_weekdays:[],by_date:{}}, ...extra};
}
function fixture(){
 const pois=[];
 for(let day=0;day<3;day++){
  // The requested entertainment category has no remotely affordable tickets.
  pois.push(venue(`exp-${day}-a`,day,'attraction',1600,['entertainment']));
  pois.push(venue(`exp-${day}-b`,day,'attraction',1400,['entertainment']));
  // Verified fee=no free park and a cheap (but not free) local alternative.
  pois.push(venue(`park-${day}-a`,day,'attraction',0,['nature']));
  pois.push(venue(`park-${day}-b`,day,'attraction',0,['nature']));
  pois.push(venue(`history-${day}`,day,'attraction',80,['history']));
  pois.push(venue(`bakery-${day}`,day,'restaurant',110,['cafes'],{food_type:'bakery',meal_suitability:{breakfast:'likely',lunch:'possible',dinner:'unlikely'}}));
  pois.push(venue(`canteen-${day}`,day,'restaurant',120,['cafes'],{food_type:'canteen',meal_suitability:{breakfast:'possible',lunch:'likely',dinner:'possible'}}));
  pois.push(venue(`cafe-${day}`,day,'restaurant',140,['cafes'],{food_type:'cafe',meal_suitability:{breakfast:'possible',lunch:'possible',dinner:'likely'}}));
  pois.push(venue(`upscale-${day}`,day,'restaurant',1600,['cafes'],{food_type:'restaurant',meal_suitability:{breakfast:'possible',lunch:'likely',dinner:'likely'}}));
 }
 const walking={};
 for(const a of pois){
  walking[a.id]={};
  for(const b of pois){if(a.id===b.id)continue;
   const dist=Math.round(engine.distance(a,b));
   const minutes=Math.max(2,Math.ceil(dist/65));
   walking[a.id][b.id]={mode:'walk',source:'fixture',basis:'routing_provider',leg_profile:'walk',
    distance_m:dist,base_minutes:minutes,planned_minutes:minutes,buffer_minutes:0,transport_cost_cny:0};
  }
 }
 return {pois,currency:'RUB',compact_waits:true,leg_profiles:{walk:walking},day_anchors:Object.fromEntries(days.map((d,i)=>
   [d,{lat:55.75+i*.003,lon:[37.60,37.635,37.67][i],radius_m:2200}]))};
}
function options(budget=1250){return {start_date:days[0],days:3,day_start:'09:00',day_end:'20:00',
 day_settings:{[days[0]]:{day_start:'12:07',day_end:'20:00'},[days[1]]:{day_start:'09:00',day_end:'20:00'},[days[2]]:{day_start:'12:07',day_end:'19:00'}},
 pace:'balanced',transport:'walk',transport_modes:['walk'],primary_interests:['entertainment'],secondary_interests:['cafes'],
 budget_cny:budget,required_ids:[],group:{adult:1,child:0,senior:0,pet:0}};}
test('budget below expensive entertainment tickets still fills all three days with free parks',()=>{
 const p=engine.planTrip(fixture(),options());
 assert(p.days.every(d=>d.visit_ids.length>=1),JSON.stringify(p.days.map(d=>({date:d.date,ids:d.visit_ids,meals:d.meal_ids,cost:d.total_cost_cny}))));
 assert(p.days.every(d=>d.events.some(e=>e.type==='visit'&&e.price_cny===0)));
 assert(!p.days.flatMap(d=>d.events).some(e=>e.poi_id?.startsWith('exp-')||e.poi_id?.startsWith('upscale-')));
 assert(p.summary.total_cost_cny<=1250,JSON.stringify(p.summary));
 assert.equal(p.checks.time,'passed');
 assert.equal(p.checks.budget,'passed');
 assert.equal(p.checks.no_duplicates,'passed');
});
test('zero budget still schedules genuinely free attractions in every day, but does not invent free meals',()=>{
 const p=engine.planTrip(fixture(),options(0));
 assert.equal(p.summary.total_cost_cny,0);
 assert(p.days.every(d=>d.visit_ids.length>0),'All three days should include free attractions');
 assert(p.days.flatMap(d=>d.events).filter(e=>e.type==='meal').every(e=>e.price_cny>0));
 assert.equal(p.checks.budget,'passed');
});
test('budget is never spent on an overpriced restaurant when cheaper canteens are available',()=>{
 const p=engine.planTrip(fixture(),options(2100));
 assert(p.days.every(d=>d.visit_ids.length>0));
 assert(p.days.flatMap(d=>d.events).filter(e=>e.type==='meal').every(e=>e.price_cny<=140));
 assert(p.summary.total_cost_cny<=2100);
});
test('cheap POI near meals can fill the former 177-minute lunch-to-dinner hole',()=>{
 const data=fixture(),day=0;
 // Extra accessible stops in the very same neighbourhood as the meals.
 for(let i=0;i<6;i++)data.pois.push(venue(`extra-${i}`,day,'attraction',0,['nature'],
   {duration_minutes:45}));
 const profiles=data.leg_profiles.walk;
 for(const a of data.pois){profiles[a.id] ||= {};
  for(const b of data.pois){if(a.id===b.id || profiles[a.id][b.id])continue;
   const dist=Math.round(engine.distance(a,b)),minutes=Math.max(2,Math.ceil(dist/65));
   profiles[a.id][b.id]={mode:'walk',source:'fixture',basis:'routing_provider',leg_profile:'walk',distance_m:dist,
    base_minutes:minutes,planned_minutes:minutes,buffer_minutes:0,transport_cost_cny:0};
  }
 }
 const o=options(600);o.days=1;o.day_settings={[days[0]]:{day_start:'12:07',day_end:'19:00'}};
 const p=engine.planTrip(data,o),ev=p.days[0].events;
 const lunch=ev.findIndex(e=>e.meal_kind==='lunch');
 const dinner=ev.findIndex(e=>e.meal_kind==='dinner');
 assert(lunch>=0&&dinner>lunch,JSON.stringify(ev));
 const middle=ev.slice(lunch+1,dinner).filter(e=>e.type==='visit');
 assert(middle.length>0,'A nearby free attraction must fit between lunch and dinner, rather than 177 minutes waiting: '+JSON.stringify(ev));
 const longestInternalGap=Math.max(0,...ev.slice(1).map((e,i)=>e.start_min-ev[i].end_min));
 assert(longestInternalGap<=60,`An avoidable gap of ${longestInternalGap} minutes remains despite free nearby POIs`);
 assert(p.summary.total_cost_cny<=600);
});
test('five affordable days use nearby free alternatives rather than leaving late days blank',()=>{
 const dates=Array.from({length:5},(_,i)=>`2026-11-${String(21+i).padStart(2,'0')}`),pois=[];
 for(let day=0;day<5;day++){
  const lon=37.59+day*.017;
  for(let j=0;j<3;j++)pois.push({id:`park${day}-${j}`,name:`park${day}-${j}`,kind:'attraction',
   lat:55.75+day*.002+j*.0005,lon:lon+j*.0005,categories:['nature'],price_cny:0,duration_minutes:45,
   opening:{open:'08:00',close:'21:00',closed_weekdays:[],by_date:{}}});
  pois.push({id:`theatre${day}`,name:'theatre',kind:'attraction',lat:55.75+day*.002,lon,
   categories:['entertainment'],price_cny:1900,duration_minutes:70,
   opening:{open:'09:00',close:'21:00',closed_weekdays:[],by_date:{}}});
  for(let j=0;j<3;j++)pois.push({id:`meal${day}-${j}`,name:'canteen',kind:'restaurant',
   lat:55.75+day*.002+j*.0003,lon:lon+j*.0004,categories:['cafes'],price_cny:100+j*20,
   duration_minutes:50,food_type:'canteen',meal_suitability:{breakfast:'possible',lunch:'likely',dinner:'possible'},
   opening:{open:'07:00',close:'22:00',closed_weekdays:[],by_date:{}}});
 }
 const walk=Object.fromEntries(pois.map(a=>[a.id,Object.fromEntries(pois.filter(b=>b.id!==a.id).map(b=>{
  const meters=Math.round(engine.distance(a,b)),min=Math.max(2,Math.ceil(meters/70));
  return [b.id,{mode:'walk',leg_profile:'walk',source:'fixture',basis:'routing_provider',
   distance_m:meters,base_minutes:min,planned_minutes:min,buffer_minutes:0,transport_cost_cny:0}];
 }))]));
 const data={pois,currency:'RUB',compact_waits:true,leg_profiles:{walk},
 day_anchors:Object.fromEntries(dates.map((date,i)=>[date,{lat:55.75+i*.002,lon:37.59+i*.017,radius_m:2000}]))};
 const o={...options(5000),days:5,start_date:dates[0],day_start:'09:00',day_end:'20:00',
 day_settings:Object.fromEntries(dates.map(date=>[date,{day_start:'09:00',day_end:'20:00'}]))};
 const p=engine.planTrip(data,o);
 assert.equal(p.days.length,5);
 assert(p.days.every(day=>day.visit_ids.length>=1),JSON.stringify(p.days.map(d=>d.visit_ids)));
 assert(p.summary.total_cost_cny<=5000);
 assert(!p.days.flatMap(d=>d.events).some(e=>e.poi_id?.startsWith('theatre')));
});
test('an unaffordable mandatory attraction is never silently swapped for a park',()=>{
 const p=engine.planTrip(fixture(),options(1250));
 const o=options(1250);o.required_ids=['exp-0-a'];
 const constrained=engine.planTrip(fixture(),o);
 assert(constrained.unplaced_required_ids.includes('exp-0-a'));
 assert(constrained.warnings.some(w=>w.includes('Обязательные билеты')||w.includes('Не размещены обязательные места')));
 assert(!constrained.days.flatMap(d=>d.events).some(e=>e.poi_id==='exp-0-a'));
 assert(p.days.every(d=>d.visit_ids.length>0));
});
