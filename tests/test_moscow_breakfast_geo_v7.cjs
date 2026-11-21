'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const engine=require('../ai_ds/web_demo/shanghai-engine.js');
const DATE='2026-11-21';
const open=(open='07:00',close='22:00')=>({open,close,closed_weekdays:[],by_date:{}});
const attraction=(id,lat,lon)=>({id,name:id,lat,lon,kind:'attraction',categories:['museums'],price_cny:0,duration_minutes:55,opening:open()});
const restaurant=(id,lat,lon,kind='breakfast')=>({id,name:id,lat,lon,kind:'restaurant',categories:['cafes'],price_cny:100,duration_minutes:50,food_type:kind==='breakfast'?'cafe':'restaurant',meal_suitability:{breakfast:'likely',lunch:'likely',dinner:'likely'},opening:open()});
function data(){
 const pois=[attraction('museum',55.751,37.621),attraction('gallery',55.7515,37.6215),
  attraction('park',55.753,37.622),restaurant('breakfast',55.7511,37.6211),
  restaurant('lunch',55.7516,37.6216),restaurant('dinner',55.752,37.622)];
 const profiles={walk:{},taxi:{},transit:{}};
 for(const from of pois){for(const mode of ['walk','taxi','transit'])profiles[mode][from.id]={};
  for(const to of pois){if(from.id===to.id)continue;
   const metres=Math.max(50,Math.round(engine.distance(from,to)));
   for(const mode of ['walk','taxi','transit']){
    const minutes=mode==='walk'?Math.max(3,Math.ceil(metres/70)):mode==='taxi'?12:15;
    profiles[mode][from.id][to.id]={mode,leg_profile:mode,base_minutes:minutes,buffer_minutes:0,planned_minutes:minutes,
     distance_m:metres,transport_cost_cny:mode==='walk'?0:mode==='taxi'?500:75,source:'fixture',basis:'routing_provider'};
   }
  }
 }
 return {pois,currency:'RUB',compact_waits:true,leg_profiles:profiles};
}
function options(change={}){return {start_date:DATE,days:1,day_start:'09:00',day_end:'20:00',
 pace:'balanced',transport:'walk',transport_modes:['walk'],primary_interests:['museums'],secondary_interests:['cafes'],
 required_ids:['museum','gallery'],budget_cny:4000,group:{adult:1,child:0,senior:0,pet:0},...change};}
const stops=day=>day.events.filter(e=>e.poi_id);
const travel=day=>day.events.filter(e=>e.type==='travel');
test('fresh one-day Moscow itinerary begins with breakfast when breakfast is scheduled',()=>{
 const plan=engine.planTrip(data(),options());
 assert.equal(plan.checks.time,'passed');
 const first=stops(plan.days[0])[0],breakfast=stops(plan.days[0]).find(e=>e.meal_kind==='breakfast');
 assert(breakfast,JSON.stringify(plan.days[0]));
 assert.equal(first.poi_id,breakfast.poi_id);
 assert.equal(first.meal_kind,'breakfast');
 assert.equal(plan.checks.budget,'passed');
 assert.equal(plan.checks.required_places,'passed');
});
test('three-day and five-day mornings have breakfast first whenever available',()=>{
 for(const days of [3,5]){
  const input=data();
  // More restaurants and activities allow food slots to be allocated on each day.
  for(let i=0;i<days*3;i++){
   const id='restaurant-'+i,lat=55.749+i*.00004,lon=37.620+i*.00003;
   input.pois.push(restaurant(id,lat,lon));
   for(const mode of ['walk','transit','taxi']){
    input.leg_profiles[mode][id]={};
    for(const p of input.pois.slice(0,-1)){
     const link=(source)=>({mode,leg_profile:mode,base_minutes:source,buffer_minutes:0,planned_minutes:source,distance_m:180,
      transport_cost_cny:mode==='walk'?0:mode==='taxi'?450:75,source:'fixture',basis:'routing_provider'});
     input.leg_profiles[mode][id][p.id]=link(mode==='walk'?4:12);
     input.leg_profiles[mode][p.id][id]=link(mode==='walk'?4:12);
    }
   }
  }
  const plan=engine.planTrip(input,options({days,budget_cny:16000,required_ids:[]}));
  assert.equal(plan.checks.time,'passed');
  for(const day of plan.days){const seq=stops(day),b=seq.find(e=>e.meal_kind==='breakfast');
   if(b)assert.equal(seq[0].poi_id,b.poi_id,JSON.stringify({days,date:day.date,events:seq}));
  }
 }
});
test('a missed breakfast is not moved after a museum to satisfy a late meal window',()=>{
 const input=data();input.pois.filter(p=>p.kind==='restaurant').forEach(p=>p.opening=open('11:00','22:00'));
 const plan=engine.planTrip(input,options({day_start:'08:00',day_end:'19:00'}));
 for(const e of stops(plan.days[0]))assert.notEqual(e.meal_kind,'breakfast');
 assert(plan.days[0].visit_ids.length>0);
});
test('fixed-sequence editing normalizes legacy breakfast after museum to first stop',()=>{
 const input=data(),settings=options(),base=engine.planTrip(input,settings);
 const breakfast=stops(base.days[0]).find(e=>e.meal_kind==='breakfast');
 assert(breakfast);
 const entries=stops(base.days[0]).map(e=>({poi_id:e.poi_id,type:e.type,meal_kind:e.meal_kind}));
 const moved=entries.splice(entries.findIndex(e=>e.meal_kind==='breakfast'),1)[0];entries.push(moved);
 const result=engine.scheduleSequences(input,settings,{base_plan:base,sequences:{[DATE]:entries},changed_days:[DATE]});
 assert.equal(stops(result.plan.days[0])[0].meal_kind,'breakfast');
 assert.equal(result.plan.summary.total_cost_cny,Math.round(stops(result.plan.days[0]).reduce((s,e)=>s+e.price_cny,0)+travel(result.plan.days[0]).reduce((s,e)=>s+e.transport_cost_cny,0)));
});
test('validator rejects a breakfast after a visit rather than claiming the route is valid',()=>{
 const input=data(),settings=options(),plan=engine.planTrip(input,settings);
 const events=plan.days[0].events,st=stops(plan.days[0]);assert(st.some(e=>e.meal_kind==='breakfast'));
 const breakfast=st.find(e=>e.meal_kind==='breakfast');
 const visitor=st.find(e=>e.type==='visit');
 // Modify only the breakfast label to emulate an incorrect saved plan.
 const broken=structuredClone(plan);
 const target=broken.days[0].events.find(e=>e.poi_id===visitor.poi_id);
 const old=broken.days[0].events.find(e=>e.poi_id===breakfast.poi_id);
 old.meal_kind='snack';target.type='meal';target.meal_kind='breakfast';
 const result=engine.validateEditedTrip(input,settings,broken);
 assert.equal(result.valid,false);
 assert(result.errors.some(e=>e.includes('BREAKFAST_NOT_FIRST')),JSON.stringify(result.errors));
});
test('mixed-mode short 13-min walk wins against a 10-min expensive cab loop',()=>{
 const input=data();for(const from of input.pois)for(const to of input.pois){
  if(from.id===to.id)continue;
  input.leg_profiles.walk[from.id][to.id].planned_minutes=13;
  input.leg_profiles.walk[from.id][to.id].base_minutes=13;
  input.leg_profiles.taxi[from.id][to.id].planned_minutes=10;
  input.leg_profiles.taxi[from.id][to.id].base_minutes=10;
 }
 const plan=engine.planTrip(input,options({transport:'mixed',transport_modes:['walk','taxi']}));
 const legs=travel(plan.days[0]);assert(legs.length);
 assert(legs.every(e=>e.mode==='walk'),JSON.stringify(legs));
});
test('long taxi that saves substantial time is still possible when user permits it',()=>{
 const input=data();for(const from of input.pois)for(const to of input.pois){if(from.id===to.id)continue;
  input.leg_profiles.walk[from.id][to.id].planned_minutes=31;
  input.leg_profiles.walk[from.id][to.id].base_minutes=31;
  input.leg_profiles.taxi[from.id][to.id].planned_minutes=12;
  input.leg_profiles.taxi[from.id][to.id].base_minutes=12;
 }
 const plan=engine.planTrip(input,options({transport:'mixed',transport_modes:['walk','taxi']}));
 assert(travel(plan.days[0]).some(e=>e.mode==='taxi'),JSON.stringify(travel(plan.days[0])));
 assert.equal(plan.checks.budget,'passed');
});
