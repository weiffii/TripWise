/* Execute the ORIGINAL untouched routing engine extracted from the supplied HTML.
   Does not patch or change the engine, calendar logic or transport estimates.
   Online OSRM/footpaths are NOT used; osm_legs must be reported honestly. */
'use strict';
const fs=require('fs'),path=require('path'),vm=require('vm');
const root=path.resolve(__dirname,'..');
const data=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/pois.json'),'utf8'));
const profiles=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/qa_scenarios.json'),'utf8'));
const html=fs.readFileSync(path.join(root,'artifacts/tripwise-shanghai.html'),'utf8');
const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
if(scripts.length!==4)throw Error('Unexpected HTML scripts count: '+scripts.length);
// UMD engine only, no UI code execution. Every script is read from CURRENT HTML.
const sandbox={console}; vm.createContext(sandbox);
vm.runInContext(scripts[0],sandbox,{filename:'unaltered-shanghai-transit.js'});
vm.runInContext(scripts[1],sandbox,{filename:'unaltered-shanghai-engine.js'});
const engine=sandbox.TripWiseShanghai;
if(!engine||typeof engine.planTrip!=='function')throw Error('Original engine did not load');
const output=[];
for(const s of profiles.scenarios){
 try{
  const now=process.hrtime.bigint();
  const r=engine.planTrip(data,s.input);
  const ms=Number(process.hrtime.bigint()-now)/1e6;
  const core=['time','budget','opening_hours','no_duplicates'];
  const safe=core.every(k=>r.checks[k]==='passed');
  const osm=Number(r.summary?.osm_legs||0);
  output.push({id:s.id,purpose:s.purpose,transport:s.input.transport,expected:s.expectation,
    status:r.status,valid_core_invariants:safe,
    checks:r.checks,required_ids:s.input.required_ids,unplaced_required_ids:r.unplaced_required_ids,
    total_visits:r.summary.visit_count,total_meals:r.summary.meal_count,
    daily_visits:r.summary.daily_visits,days_count:r.summary.days_count,
    cost_cny:r.summary.total_cost_cny,osm_leg_count:osm,
    warnings:r.warnings.slice(0,12),performance_ms:Math.round(ms)});
 }catch(err){output.push({id:s.id,purpose:s.purpose,transport:s.input.transport,status:'exception',error:String(err.stack||err),valid_core_invariants:false});}
}
const rep={data_poi_count:data.pois.length,original_engine_loaded_from_html:true,engine_modified:false,
 scenarios_attempted:output.length,full_routes:output.filter(x=>x.status==='demo').length,
 partial_routes:output.filter(x=>x.status==='partial').length,
 exceptions:output.filter(x=>x.status==='exception').length,
 core_invariants_passed:output.filter(x=>x.valid_core_invariants).length,
 full_road_network_verified_routes:0,osm_routed_legs:output.reduce((acc,x)=>acc+(x.osm_leg_count||0),0),
 transport_note:'The unchanged offline engine computes demo/fallback transport. No real road routing or Huangpu-crossing verification was performed.',
 cases:output};
const dest=path.join(root,'reports/shanghai_26_route_scenarios.json');
fs.writeFileSync(dest,JSON.stringify(rep,null,2)+'\n');
console.log(JSON.stringify({attempted:rep.scenarios_attempted,full:rep.full_routes,partial:rep.partial_routes,
 exceptions:rep.exceptions,valid_core:rep.core_invariants_passed,osm_legs:rep.osm_routed_legs,
 failures:output.filter(x=>!x.valid_core_invariants).map(x=>x.id)},null,2));
