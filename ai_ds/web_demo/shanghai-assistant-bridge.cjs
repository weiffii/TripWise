'use strict';
// Local JSON-only process boundary. Catalog paths are owned by the server.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../..'),api=require('./shanghai-engine.js');
const LIMIT=10*1024*1024;let raw='',bytes=0;
function reply(value){process.stdout.write(JSON.stringify(value)+'\n');}
function gaps(day){
 return day.events.slice(1).map((event,index)=>({event,minutes:event.start_min-day.events[index].end_min}))
  .filter(gap=>gap.minutes>0).sort((left,right)=>right.minutes-left.minutes);
}
function fillWaiting(data,options,initial){
 if(initial.status==='infeasible')return initial;
 let plan=initial;
 const places=new Map(data.pois.map(place=>[place.id,place]));
 for(const day of initial.days){
  const [largest]=gaps(day);
  if(!largest||largest.minutes<60)continue;
  const target=largest.event.poi_id||largest.event.to_poi_id;
  const stops=day.events.filter(event=>event.poi_id).map(event=>({
   poi_id:event.poi_id,start_min:event.start_min,meal_kind:event.meal_kind,
  }));
  if(day.visit_ids.length>=api.PACES[options.pace||'balanced'].max)continue;
  const index=stops.findIndex(stop=>stop.poi_id===target);
  if(index<1||!places.has(target))continue;
  const before=places.get(stops[index-1].poi_id),after=places.get(target);
  const existing=new Set(plan.selected_ids);
  const candidates=initial.ranking.filter(row=>row.match_count>0&&!existing.has(row.poi_id))
   .map(row=>places.get(row.poi_id)).filter(place=>place.kind==='attraction'&&api.suitable(place,options.group)&&api.flags(place,day.date).available)
   .sort((left,right)=>(api.distance(before,left)+api.distance(left,after))-(api.distance(before,right)+api.distance(right,after))).slice(0,4);
  const totalWaiting=items=>items.reduce((total,gap)=>total+gap.minutes,0);
  for(const place of candidates){
   const sequence=[...stops.slice(0,index),{poi_id:place.id},...stops.slice(index)];
   let trial;
   try{trial=api.scheduleSequences(data,options,{base_plan:plan,sequences:{[day.date]:sequence},changed_days:[day.date]}).plan;}
   catch(error){if(error.name!=='EditError'&& !error.code)throw error;continue;}
   const next=trial.days.find(item=>item.date===day.date),waiting=gaps(next);
   if((waiting[0]?.minutes||0)>=largest.minutes||totalWaiting(waiting)>=totalWaiting(gaps(day)))continue;
   if(Object.entries(plan.checks).some(([check,value])=>value==='passed'&&check!=='no_gaps'&&trial.checks[check]!=='passed'))continue;
   if(!api.validateEditedTrip(data,options,trial).valid)continue;
   plan=trial;
   break;
  }
 }
 return plan;
}
function handle(raw){
 try{
  const request=JSON.parse(raw);if(!request||typeof request!=='object'||Array.isArray(request))throw Object.assign(Error(),{code:'INVALID_REQUEST'});
  const allowed=new Set(['action','options','base_plan','operations','sequences','changed_days','day_settings','meal_times','visit_times','edit_spec','plan','dataset']);
  if(Object.keys(request).some(k=>!allowed.has(k))||!['plan','edit','edit_checked','schedule','validate','extend_visits'].includes(request.action))throw Object.assign(Error(),{code:'INVALID_REQUEST'});
  let data=request.dataset;
  if(!Object.hasOwn(request,'dataset')){
   data=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/pois.json'),'utf8').replace(/^\uFEFF/,''));
   data.transport=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/transport.json'),'utf8').replace(/^\uFEFF/,''));
  }
  let result;
  if(request.action==='plan')result=fillWaiting(data,request.options,api.planTrip(data,request.options));
  if(request.action==='edit')result=api.editTrip(data,request.options,request.base_plan,request.operations);
  if(request.action==='edit_checked'){
   result=api.editTrip(data,request.options,request.base_plan,request.operations);
   result.checked=api.validateEditedTrip(data,result.options,result.plan);
  }
  if(request.action==='schedule')result=api.scheduleSequences(data,request.options,request.edit_spec||{base_plan:request.base_plan,sequences:request.sequences,changed_days:request.changed_days,day_settings:request.day_settings,meal_times:request.meal_times,visit_times:request.visit_times});
  if(request.action==='validate')result=api.validateEditedTrip(data,request.options,request.plan||request.base_plan);
  if(request.action==='extend_visits')result={days:api.stretchVisitGaps(data,request.options,request.plan.days,
   request.changed_days?new Set(request.changed_days):null)};
  reply(result);
 }catch(error){reply({error:{code:error.code||'INVALID_ROUTE_REQUEST',message:error.code?String(error.message).slice(0,400):'Не удалось проверить маршрут. Проверьте параметры запроса.',...(error.reason?{reason:error.reason}:{})}});}
}
function append(chunk){
 bytes+=Buffer.byteLength(chunk);
 if(bytes>LIMIT){reply({error:{code:'REQUEST_TOO_LARGE'}});process.exit(0);}
 raw+=chunk;
}
const lineMode=process.argv.includes('--lines');
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{
 if(!lineMode){append(chunk);return;}
 const parts=chunk.split('\n');
 for(const part of parts.slice(0,-1)){
  append(part);
  handle(raw);
  raw='';bytes=0;
 }
 append(parts[parts.length-1]);
});
process.stdin.on('end',()=>{if(!lineMode||raw)handle(raw);});
