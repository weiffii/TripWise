'use strict';
// The new editing entry point must not change initial itinerary generation.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),before=path.join(root,'reports/assistant-2026-11-20/before');
const data=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/pois.json'),'utf8'));
data.transport=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/transport.json'),'utf8'));
const baseline={console},current={console};vm.createContext(baseline);vm.createContext(current);
for(const [ctx,dir] of [[baseline,before],[current,path.join(root,'ai_ds/web_demo')]])for(const file of ['shanghai-transit.js','shanghai-engine.js'])vm.runInContext(fs.readFileSync(path.join(dir,file),'utf8'),ctx,{filename:file});
const initial=JSON.stringify(data),results=[];
for(const days of [1,3,5]){
 const options={primary_interests:['museums','history'],secondary_interests:['nature','viewpoints'],days,pace:'balanced',start_date:'2026-11-20',day_start:'09:00',day_end:'20:00',budget_cny:10000,transport:'mixed',group:{adult:2,child:0,senior:0,pet:0},required_ids:[]};
 const t=performance.now(),a=baseline.TripWiseShanghai.planTrip(data,options),beforeMs=performance.now()-t;
 const u=performance.now(),b=current.TripWiseShanghai.planTrip(data,options),afterMs=performance.now()-u;
 assert.deepEqual(JSON.parse(JSON.stringify(b)),JSON.parse(JSON.stringify(a)),'Initial plan changed for '+days+' days');
 results.push({days,before_ms:beforeMs,after_ms:afterMs,status:b.status,visits:b.summary.visit_count,identical:true});
}
assert.equal(JSON.stringify(data),initial,'Dataset mutated');
const manifest=JSON.parse(fs.readFileSync(path.join(before,'manifest.json'),'utf8'));
assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'data/demo/shanghai/pois.json'))).digest('hex'),manifest.poi_sha256);
fs.writeFileSync(path.join(root,'reports/assistant-2026-11-20/initial-planner-regression.json'),JSON.stringify({results,poi_unchanged:true},null,2)+'\n');
console.log(JSON.stringify({passed:3,initial_routes_identical:true,results}));
