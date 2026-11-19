/* Final Shanghai 2000: actual current engine/API integration, no network calls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ROOT = path.resolve(__dirname,'..');
const enginePath = path.join(ROOT,'ai_ds/web_demo/shanghai-engine.js');
const api = require(enginePath);
const data = require(path.join(ROOT,'data/demo/shanghai/pois.json'));
const {pois} = data;
const museum = pois.find(p => p.synthetic_museum);
assert(museum && museum.opening.closed_weekdays.includes(7));

// Expose the current implementation's private flags ONLY in an in-memory test module.
// The repository's engine source remains completely unchanged.
const source = fs.readFileSync(enginePath,'utf8');
const needle = 'return {planTrip,rankPois';
assert(source.includes(needle),'Engine API changed: review this calendar regression test');
const instrumented = source.replace(needle,'return {flags,planTrip,rankPois');
const temp = new Module(enginePath,module);
temp.filename = enginePath;
temp.paths = Module._nodeModulePaths(path.dirname(enginePath));
temp._compile(instrumented,enginePath);
const flags = temp.exports.flags;
assert.equal(flags(museum,'2026-11-22').closed,true,'Sunday must close a synthetic museum');
assert.equal(flags(museum,'2026-11-22').available,false,'Sunday must be unavailable');
assert.equal(flags(museum,'2026-11-21').closed,false,'Saturday must be available');
assert.equal(flags(museum,'2026-11-21').available,true,'Saturday must not be flagged closed');
const otherClosed=pois.filter(p=>p.opening.closed_weekdays.includes(1));
assert(otherClosed.length>0);
assert.equal(flags(otherClosed[0],'2026-11-23').closed,true,'Monday=1 must be closed when declared');

const common={
 primary_interests:['nature','history','museums','viewpoints','entertainment'],
 secondary_interests:['shopping','unusual'],
 pace:'balanced',start_date:'2026-11-23',day_start:'09:00',day_end:'21:00',
 budget_cny:20000,transport:'mixed',group:{adult:2,child:0,senior:0,pet:0},required_ids:[],
};
let successful=0;
for(let days=1;days<=5;days++){
 const result=api.planTrip(data,{...common,days});
 assert.equal(result.days.length,days,'Days must match requested trip length');
 assert(result.days.every(day=>Array.isArray(day.events)),'Each day must have events');
 assert(result.checks && result.checks.budget!=='failed','Budget validation');
 assert(result.checks.opening_hours!=='failed','Hours must fit calendar');
 const ids=new Set(pois.map(p=>p.id));
 for(const day of result.days){
   for(const event of day.events){
     if(event.poi_id)assert(ids.has(event.poi_id));
   }
 }
 successful++;
 console.log('PASS trip',days,'day(s)','visits',result.days.reduce((n,d)=>n+d.events.filter(e=>e.type==='visit').length,0),
   'meals',result.days.reduce((n,d)=>n+d.events.filter(e=>e.type==='meal').length,0));
}
const groupCases=[
  {adult:2,child:1,senior:0,pet:0},
  {adult:1,child:0,senior:1,pet:0},
  {adult:1,child:0,senior:0,pet:1},
  {adult:1,child:1,senior:1,pet:1},
];
let remainingGaps=0;
for(const group of groupCases){
 const result=api.planTrip(data,{...common,primary_interests:['nature','entertainment','viewpoints'],secondary_interests:['museums','history','shopping'],days:3,transport:'walk',group});
 assert.equal(result.checks.budget,'passed');
 assert.equal(result.checks.opening_hours,'passed');
 assert.equal(result.checks.meals,'passed');
 assert.equal(result.checks.interest_coverage,'passed');
 if(result.checks.no_gaps!=='passed')remainingGaps++;
 console.log('PASS group',JSON.stringify(group),'checks',JSON.stringify(result.checks));
}
console.log(JSON.stringify({passed_trip_lengths:5,passed_group_cases:4,calendar_sunday_checked:true,engine_modified:false,network_requests:0,remaining_walk_pet_gaps:remainingGaps}));
