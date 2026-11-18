/* Offline integration: published rail chains and explicitly synthetic buses. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {createRouter}=require('../ai_ds/web_demo/shanghai-transit.js');
const data=JSON.parse(fs.readFileSync('data/demo/shanghai/transport.json','utf8').replace(/^\uFEFF/,''));
let count=0;function test(name,f){f();count++;console.log('PASS '+name);}
const router=createRouter(data),station=id=>{const s=data.metro.stations.find(s=>s.id===id);return {...s,name:s.name_en};};
test('source topology and synthetic fleet are structurally distinct',()=>{
 assert.equal(data.crs,'EPSG:4326');assert.equal(data.metro.stations.length,418);assert.equal(data.buses.routes.length,30);
 assert(data.buses.routes.every(r=>r.synthetic&&/^DEMO-BUS-\d\d$/.test(r.id)));
 const stops=new Set(data.buses.stops.map(s=>s.id));assert(data.buses.routes.every(r=>r.stop_ids.every(id=>stops.has(id))));
});
test('Line 2 crosses the river using two published station segments',()=>{
 const route=router.route(station('peoples-square'),station('lujiazui'),{allow_synthetic_bus:false});
 assert(route);assert.equal(route.source,'REAL_METRO_TOPOLOGY_ESTIMATED_TIMES');
 const rides=route.instructions.filter(i=>i.mode==='metro');assert.equal(rides.length,1);assert.equal(rides[0].line_id,'2');assert.equal(rides[0].stop_count,2);
 assert(route.planned_minutes>route.base_minutes);assert.equal(route.planned_minutes,route.base_minutes+route.buffer_minutes);
 assert.equal(route.service_verified,false);assert.equal(route.time_basis,'planning_estimate');
 assert(route.geometry_segments.filter(s=>s.mode==='metro').every(s=>s.tracks_schematic&&s.source==='real_topology_schematic'));
});
test('cache results are isolated from callers',()=>{
 const a=station('peoples-square'),b=station('lujiazui'),first=router.route(a,b,{});first.instructions.length=0;
 assert(router.route(a,b,{}).instructions.length>0);
});
test('inaccessible and unsupported transit is not fabricated',()=>{
 assert.equal(router.route(station('peoples-square'),station('lujiazui'),{group:{adult:1,pet:1}}),null);
 assert.equal(router.route({id:'outside',lat:0,lon:0},station('lujiazui'),{}),null);
});
function fixture(blocked=false){return {crs:'EPSG:4326',metro:{stations:[{id:'a',name_en:'A',lat:31.2,lon:121.4},{id:'x',name_en:'X',lat:31.2,lon:121.45},{id:'b',name_en:'B',lat:31.2,lon:121.5}],lines:[{id:'1',name:'Line 1',kind:'metro',branches:[{station_ids:['a','x']}]},{id:'2',name:'Line 2',kind:'metro',branches:[{station_ids:['x','b']}]}],transfers:[{station_id:'x',line_ids:['1','2'],blocked,estimated_minutes:5}]},buses:{stops:[],routes:[]}};}
test('explicitly blocked interchange cannot be traversed',()=>{
 const d=fixture(true),r=createRouter(d);assert.equal(r.route(d.metro.stations[0],d.metro.stations[2],{}),null);
 const open=fixture(false),route=createRouter(open).route(open.metro.stations[0],open.metro.stations[2],{});
 assert(route.instructions.some(i=>i.mode==='transfer'));
});
test('fictional bus path is labelled and never presented as real metro',()=>{
 const stops=[{id:'a',name:'Demo A',lat:31.2,lon:121.4},{id:'b',name:'Demo B',lat:31.2,lon:121.45}];
 const d={crs:'EPSG:4326',metro:{stations:[],lines:[]},buses:{stops,routes:[{id:'DEMO-BUS-01',name:'Demo bus',stop_ids:['a','b'],headway_minutes:10,speed_kmh:18,fare_cny:2,bidirectional:true,synthetic:true}]}};
 const r=createRouter(d),route=r.route(stops[0],stops[1],{});assert(route);assert.equal(route.source,'SYNTHETIC_BUS');assert(route.instructions.some(i=>i.mode==='bus'&&i.synthetic));
 assert.equal(r.route(stops[0],stops[1],{allow_synthetic_bus:false}),null);
});
test('active v4 planner carries real transit instructions into selected events',()=>{
 const api=require('../ai_ds/web_demo/shanghai-engine.js'),pois=JSON.parse(fs.readFileSync('data/demo/shanghai/pois.json','utf8').replace(/^\uFEFF/,''));
 pois.transport=data;const r=api.planTrip(pois,{primary_interests:['museums','viewpoints'],secondary_interests:['history'],pace:'balanced',days:2,start_date:'2026-11-19',day_start:'09:00',day_end:'19:00',budget_cny:3000,transport:'transit',group:{adult:1,child:0,senior:0,pet:0},required_ids:[]});
 const legs=r.days.flatMap(d=>d.events).filter(e=>e.type==='travel');assert(legs.length);assert(legs.every(e=>Array.isArray(e.instructions)&&e.instructions.length&&Array.isArray(e.geometry_segments)));
 assert(legs.every(e=>e.planned_minutes===e.base_minutes+e.buffer_minutes));
 for(const day of r.days)for(let i=0;i<day.events.length;i++){const e=day.events[i];assert(e.end_min>=e.start_min);if(i)assert(e.start_min>=day.events[i-1].end_min);}
 assert(r.summary.total_cost_cny<=3000);
});
test('synthetic bus service hours are respected when departure is known',()=>{
 const stops=[{id:'a',name:'Demo A',lat:31.2,lon:121.4},{id:'b',name:'Demo B',lat:31.2,lon:121.45}];
 const d={crs:'EPSG:4326',metro:{stations:[],lines:[]},buses:{stops,routes:[{id:'DEMO-BUS-01',name:'Demo',stop_ids:['a','b'],headway_minutes:10,speed_kmh:18,bidirectional:true,service_start:'06:00',service_end:'22:00'}]}};
 const r=createRouter(d);assert(r.route(stops[0],stops[1],{departure_min:720}));assert.equal(r.route(stops[0],stops[1],{departure_min:1380}),null);
});
test('a ring has a direction via the next station, not an invented terminus',()=>{
 const stations=[{id:'a',name_en:'A',lat:31.2,lon:121.3},{id:'b',name_en:'B',lat:31.2,lon:121.35},{id:'c',name_en:'C',lat:31.25,lon:121.35},{id:'d',name_en:'D',lat:31.25,lon:121.3}];
 const d={crs:'EPSG:4326',metro:{stations,lines:[{id:'4',name:'Line 4',kind:'metro',branches:[{station_ids:['a','b','c','d'],loop:true}]}]},buses:{stops:[],routes:[]}};
 const ride=createRouter(d).route(stations[0],stations[2],{}).instructions.filter(i=>i.mode==='metro');assert.equal(ride.length,1);assert.equal(ride[0].stop_count,2);assert(/^loop_/.test(ride[0].direction_key));
});
console.log('Passed '+count+' transit tests');
