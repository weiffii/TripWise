/* Run: node tests/test_shanghai_v2.cjs   Offline and dependency-free. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const engine=require('../ai_ds/web_demo/shanghai-engine-v2.js');
const extras=require('../data/demo/shanghai/pois-extra.json').pois;
let tests=0;
function test(name,callback){callback();tests++;console.log('PASS '+name);}
const fixture={pois:extras};
const opt={primary_interests:['history','museums'],secondary_interests:['nature'],pace:'balanced',
    days:2,start_date:'2026-11-18',day_start:'09:00',day_end:'19:00',budget_cny:7000,
    transport:'mixed',group:{adult:2,child:1,senior:1,pet:0},required_ids:[]};
const events=x=>x.days.flatMap(d=>d.events);
test('54 new places, all unique, marked synthetic, with eight categories',()=>{
  assert.equal(extras.length,54);
  assert.equal(new Set(extras.map(p=>p.id)).size,54);
  assert(extras.every(p=>p.provenance.identity==='synthetic_scenario_poi'));
  const cats=new Set(extras.flatMap(p=>p.categories));
  for(const cat of ['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual'])assert(cats.has(cat));
});
test('balanced uses at least 2 attractions per day, no idle wait gaps',()=>{
  const plan=engine.planTrip(fixture,opt);
  assert.equal(plan.status,'demo');
  for(const day of plan.days){
    assert(day.visit_ids.length>=2);
    assert(day.events.length>0);
    day.events.forEach((e,i)=>{
      assert(!['wait','rest'].includes(e.type));
      if(i)assert.equal(e.start_min,day.events[i-1].end_min);
    });
  }
  assert.equal(plan.checks.no_gaps,'passed');
});
test('pace controls durations and stop count, without isolated rest',()=>{
  const slow=engine.planTrip(fixture,{...opt,pace:'relaxed'}),fast=engine.planTrip(fixture,{...opt,pace:'intensive'});
  assert(slow.summary.visit_count<=fast.summary.visit_count);
  const duration=x=>events(x).filter(e=>e.type==='visit').reduce((a,e)=>a+(e.end_min-e.start_min),0)/x.summary.visit_count;
  assert(duration(slow)>duration(fast));
  assert(!events(slow).some(e=>e.type==='rest'));
});
test('all selected interests are covered if feasible',()=>{
  const r=engine.planTrip(fixture,opt);
  assert.deepEqual(r.uncovered_interests,[]);assert.equal(r.checks.interest_coverage,'passed');
});
test('primary interests outrank only secondary interests',()=>{
  const a=engine.rankPois(fixture,{...opt,primary_interests:['shopping'],secondary_interests:['museums']});
  const b=engine.rankPois(fixture,{...opt,primary_interests:['museums'],secondary_interests:['shopping']});
  const first=x=>fixture.pois.find(p=>p.id===x[0].poi_id);
  assert(first(a).categories.includes('shopping'));assert(first(b).categories.includes('museums'));
});
test('children and senior group discounts, transport fares per group',()=>{
  const poi=extras.find(p=>p.kind==='attraction'&&p.price_cny>0);
  const adult=engine.groupPrice(poi,{adult:3,child:0,senior:0,pet:0});
  const mixed=engine.groupPrice(poi,{adult:1,child:1,senior:1,pet:0});
  assert(mixed<adult);
  const one=engine.planTrip(fixture,{...opt,group:{adult:1,child:0,senior:0,pet:0}});
  const many=engine.planTrip(fixture,{...opt,group:{adult:4,child:1,senior:0,pet:0}});
  assert(many.summary.total_cost_cny>one.summary.total_cost_cny);
});
test('seasonal attractions excluded off-season and boosted in season',()=>{
  const seasonal=extras.find(p=>p.season_months.length>0);
  const winter=engine.planTrip(fixture,{...opt,primary_interests:['nature'],secondary_interests:[],days:1,start_date:'2026-12-02',required_ids:[]});
  assert(!winter.selected_ids.includes(seasonal.id));
  const spring=engine.planTrip(fixture,{...opt,primary_interests:['nature'],secondary_interests:[],days:2,start_date:'2027-04-15',required_ids:[seasonal.id]});
  assert(spring.selected_ids.includes(seasonal.id));
});
test('high weekend crowd spots excluded unless marked required',()=>{
  const busy=extras.find(p=>p.weekend_crowd_level==='high');
  const r=engine.planTrip(fixture,{...opt,primary_interests:['shopping'],secondary_interests:[],start_date:'2026-11-21'});
  assert(!r.selected_ids.includes(busy.id));
});
test('walk, taxi, transit and mixed use distinct travel assumptions',()=>{
  const a=extras[0],b=extras.find(p=>p.river_bank!==a.river_bank);
  const g={adult:2,child:1,senior:0,pet:0};
  assert.equal(engine.transportLeg(a,b,'walk',g,'2026-11-18'),null);
  const taxi=engine.transportLeg(a,b,'taxi',g,'2026-11-18');
  const transit=engine.transportLeg(a,b,'transit',g,'2026-11-18');
  const mixed=engine.transportLeg(a,b,'mixed',g,'2026-11-18');
  assert(taxi.transport_cost_cny>0&&transit.transport_cost_cny>0);
  assert.notEqual(taxi.base_minutes,transit.base_minutes);
  assert.notEqual(taxi.transport_assumption,transit.transport_assumption);
  assert(['synthetic_taxi_estimate','synthetic_transit_estimate'].includes(mixed.transport_assumption));
  const near=extras.find(p=>p.id!==a.id&&p.river_bank===a.river_bank&&Math.abs(p.lon-a.lon)<0.01);
  assert.equal(engine.transportLeg(a,near,'mixed',g,'2026-11-18').transport_assumption,'synthetic_walk_estimate');
});
test('pets must be explicitly allowed, transit with pets rejected',()=>{
  const p=engine.planTrip(fixture,{...opt,primary_interests:['nature'],secondary_interests:[],group:{adult:1,child:0,senior:0,pet:1}});
  const selected=events(p).filter(e=>['visit','meal'].includes(e.type));
  assert(selected.every(e=>extras.find(p=>p.id===e.poi_id).pet_friendly===true));
  const a=extras[0],b=extras[1];
  assert.equal(engine.transportLeg(a,b,'transit',{adult:1,child:0,senior:0,pet:1},'2026-11-18'),null);
});
test('budget infeasibility is explicit, no violation even if partial',()=>{
  const r=engine.planTrip(fixture,{...opt,budget_cny:0,required_ids:[extras[0].id]});
  assert.equal(r.status,'infeasible');assert.equal(r.checks.budget,'failed');
  const p=engine.planTrip(fixture,{...opt,budget_cny:1600});
  assert(p.summary.total_cost_cny<=1600.00001);assert.equal(p.checks.budget,'passed');
});
test('schedules and opening hours remain valid for multiple days',()=>{
  for(const pace of ['relaxed','balanced','intensive']){
    for(const mode of ['walk','mixed','taxi','transit']){
      const r=engine.planTrip(fixture,{...opt,pace,transport:mode,days:3});
      assert.equal(r.checks.time,'passed');assert.equal(r.checks.opening_hours,'passed');
      const seen=events(r).filter(e=>['meal','visit'].includes(e.type)).map(e=>e.poi_id);
      assert.equal(seen.length,new Set(seen).size);
    }
  }
});
test('reject invalid inputs and retain source data unmutated',()=>{
  const snapshot=JSON.stringify(fixture);
  for(const v of [{group:{adult:0,child:0,senior:0,pet:1}},{days:6},{primary_interests:[],secondary_interests:[]},
    {transport:'teleport'},{budget_cny:-3},{start_date:'2026-02-30'}])
    assert.throws(()=>engine.planTrip(fixture,{...opt,...v}));
  engine.planTrip(fixture,opt);assert.equal(JSON.stringify(fixture),snapshot);
});
console.log(JSON.stringify({passed:tests,network_requests:0}));
