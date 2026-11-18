/* Узкие offline-тесты нового экспериментального движка. */
const assert = require('node:assert/strict');
const {planTrip,rankPois} = require('../ai_ds/web_demo/shanghai-engine.js');
function poi(id,kind,categories,lat=31.23,lon=121.47,extra={}) {
  return {id,name:id,kind,categories,lat,lon,duration_minutes:kind==='restaurant'?55:75,price_cny:kind==='restaurant'?40:25,rating:4.4,review_count:100,
    river_bank:'west',opening:{open:'08:00',close:'22:00',closed_weekdays:[]},...extra};
}
function fixture() {
  return {pois:[poi('museum','attraction',['museums','history']),poi('park','attraction',['nature'],31.232,121.471),
    poi('shop','attraction',['shopping'],31.233,121.472),poi('museum2','attraction',['museums'],31.234,121.472),
    ...Array.from({length:10},(_,i)=>poi('food'+i,'restaurant',['cafes'],31.231+i*.0001,121.471,{rating:4.9-i*.05,review_count:1000-i*10}))]};
}
const options={interests:['museums'],pace:'balanced',days:1,start_date:'2026-11-18',day_start:'09:00',day_end:'19:00',budget_cny:500,required_ids:[]};
let count=0;
function test(name,body){body();count++;console.log('PASS '+name);}
function events(result){return result.days.flatMap(d=>d.events);}
test('rank responds to interests and multiple matches',()=>{
  const f=fixture();assert.equal(rankPois(f,options)[0].poi_id,'museum');
  assert.equal(rankPois(f,{...options,interests:['nature']})[0].poi_id,'park');
  assert.equal(rankPois(f,{...options,interests:['museums','history']})[0].match_count,2);
});
test('actual route changes with interests; no irrelevant optional attractions',()=>{
  const f=fixture(),museum=planTrip(f,options),nature=planTrip(f,{...options,interests:['nature']});
  assert(museum.selected_ids.includes('museum'));assert(!nature.selected_ids.includes('museum'));
  assert(nature.selected_ids.includes('park'));
});
test('multiple interests get coverage, cafes covered by meals',()=>{
  const r=planTrip(fixture(),{...options,interests:['museums','nature','cafes']});
  assert.deepEqual(r.uncovered_interests,[]);assert(r.selected_ids.includes('park'));
  assert.equal(r.checks.interest_coverage,'passed');assert(r.summary.meal_count>=2);
});
test('meals are 50-60 minutes and synthetic travel has planned buffer',()=>{
  const r=planTrip(fixture(),options),list=events(r),meals=list.filter(e=>e.type==='meal'),legs=list.filter(e=>e.type==='travel');
  assert.equal(meals.length,3);assert(meals.every(e=>e.end_min-e.start_min>=50&&e.end_min-e.start_min<=60));
  assert(legs.length>0);assert(legs.every(e=>e.buffer_minutes>0 && e.end_min-e.start_min===e.base_minutes+e.buffer_minutes));
});
test('no overlaps, window and pace cap respected',()=>{
  for(const pace of ['relaxed','balanced','intensive']){
    const r=planTrip(fixture(),{...options,pace});
    for(const d of r.days){assert(d.active_minutes<=d.active_limit_minutes);d.events.forEach((e,i)=>{assert(e.start_min>=540&&e.end_min<=1140);if(i)assert(e.start_min>=d.events[i-1].end_min);});}
    assert.equal(r.checks.time,'passed');
  }
});
test('all-trip budget and required places are protected',()=>{
  const r=planTrip(fixture(),{...options,required_ids:['park'],budget_cny:160,interests:['museums']});
  assert(r.selected_ids.includes('park'));assert(r.summary.total_cost_cny<=160);assert.equal(r.checks.required_places,'passed');
  const low=planTrip(fixture(),{...options,required_ids:['museum'],budget_cny:10});
  assert.equal(low.status,'infeasible');assert.equal(low.checks.budget,'failed');assert(low.unplaced_required_ids.includes('museum'));
});
test('closed required place is not falsely marked visited',()=>{
  const f=fixture();f.pois[0].opening.closed_weekdays=[3];
  const r=planTrip(f,{...options,required_ids:['museum']});
  assert(r.unplaced_required_ids.includes('museum'));assert.equal(r.status,'partial');assert.equal(r.checks.required_places,'failed');
});
test('multiple days no repeated POIs and data unchanged',()=>{
  const f=fixture(),before=JSON.stringify(f),r=planTrip(f,{...options,days:3,interests:['museums','nature','shopping'],budget_cny:1000});
  const ids=events(r).filter(e=>e.poi_id).map(e=>e.poi_id);assert.equal(ids.length,new Set(ids).size);assert.equal(JSON.stringify(f),before);
});
test('missing interest explained rather than unrelated optional filler',()=>{
  const r=planTrip(fixture(),{...options,interests:['unusual']});
  assert.deepEqual(r.uncovered_interests,['unusual']);assert.equal(r.summary.visit_count,0);assert.equal(r.status,'partial');
});
test('food does not silently cross river and transfers are explicit estimates',()=>{
  const f=fixture();f.pois.push(poi('east','attraction',['viewpoints'],31.235,121.505,{river_bank:'east'}));
  f.pois.push(poi('east-food','restaurant',['cafes'],31.235,121.506,{river_bank:'east',rating:5}));
  const r=planTrip(f,{...options,interests:['museums','viewpoints'],required_ids:['museum','east'],budget_cny:800});
  const fById=new Map(f.pois.map(p=>[p.id,p]));
  const transfers=events(r).filter(e=>e.type==='travel');
  for(const e of transfers){if(fById.get(e.to_poi_id).kind==='restaurant')assert.equal(fById.get(e.from_poi_id).river_bank,fById.get(e.to_poi_id).river_bank);}
  if(r.selected_ids.includes('east'))assert(transfers.some(e=>e.transport_assumption==='synthetic_urban_transfer'));
});
test('invalid inputs fail with bounded Russian validation messages',()=>{
  for(const change of [{interests:[]},{days:0},{budget_cny:-1},{required_ids:['unknown']},{start_date:'2026-02-30'},{day_start:'20:00',day_end:'09:00'}])assert.throws(()=>planTrip(fixture(),{...options,...change}),Error);
});
test('Shanghai catalogue changes actual visits and covers multiple interests',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const file=path.join(__dirname,'../data/demo/shanghai/pois.json');
  assert(fs.existsSync(file),'Shanghai demo dataset must exist');
  const data=JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));
  const preferences={...options,days:2,budget_cny:1200};
  const museum=planTrip(data,{...preferences,interests:['museums']}),nature=planTrip(data,{...preferences,interests:['nature']});
  assert.notDeepEqual(museum.days.flatMap(d=>d.visit_ids),nature.days.flatMap(d=>d.visit_ids));
  const multiple=planTrip(data,{...preferences,interests:['viewpoints','nature']});
  assert.deepEqual(multiple.uncovered_interests,[]);assert.equal(multiple.checks.meals,'passed');
  assert(multiple.summary.total_cost_cny<=1200);
  for(const result of [museum,nature,multiple]){
    assert.equal(result.checks.time,'passed');assert.equal(result.checks.opening_hours,'passed');
    const byId=new Map(data.pois.map(p=>[p.id,p]));
    for(const d of result.days){
      for(const event of d.events){
        if(event.type==='travel' && byId.get(event.to_poi_id).kind==='restaurant')assert.equal(byId.get(event.from_poi_id).river_bank,byId.get(event.to_poi_id).river_bank);
      }
    }
  }
});
test('no artificial rest events at any pace; breakfast starts morning plans',()=>{
  for(const pace of ['relaxed','balanced','intensive']){
    const r=planTrip(fixture(),{...options,pace});
    assert(!events(r).some(e=>e.type==='rest'));
    assert(r.days.every(d=>d.rest_minutes===0));
    assert.equal(events(r)[0].meal_kind,'breakfast');
  }
});
test('breakfast exists before noon but not at noon',()=>{
  const before=planTrip(fixture(),{...options,day_start:'11:30'});
  assert(events(before).some(e=>e.meal_kind==='breakfast'));
  const noon=planTrip(fixture(),{...options,day_start:'12:00'});
  assert(!events(noon).some(e=>e.meal_kind==='breakfast'));
});
test('unavailable breakfast explicitly warned without inventing opening times',()=>{
  const f=fixture();f.pois.filter(p=>p.kind==='restaurant').forEach(p=>p.opening.open='12:00');
  const r=planTrip(f,options);
  assert(!events(r).some(e=>e.meal_kind==='breakfast'));
  assert(r.warnings.some(w=>w.includes('завтрак')));
  assert.equal(r.checks.meals,'failed');
});
console.log(JSON.stringify({passed:count,network_requests:0}));
