'use strict';
// Local JSON-only process boundary. Catalog paths are owned by the server.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../..'),api=require('./shanghai-engine.js');
const LIMIT=10*1024*1024;let raw='',bytes=0;
function reply(value){process.stdout.write(JSON.stringify(value)+'\n');}
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{bytes+=Buffer.byteLength(chunk);if(bytes>LIMIT){reply({error:{code:'REQUEST_TOO_LARGE'}});process.exit(0);}raw+=chunk;});
process.stdin.on('end',()=>{
 try{
  const request=JSON.parse(raw);if(!request||typeof request!=='object'||Array.isArray(request))throw Object.assign(Error(),{code:'INVALID_REQUEST'});
  const allowed=new Set(['action','options','base_plan','operations','sequences','changed_days','day_settings','meal_times','edit_spec','plan']);
  if(Object.keys(request).some(k=>!allowed.has(k))||!['edit','schedule','validate'].includes(request.action))throw Object.assign(Error(),{code:'INVALID_REQUEST'});
  const data=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/pois.json'),'utf8').replace(/^\uFEFF/,''));
  data.transport=JSON.parse(fs.readFileSync(path.join(root,'data/demo/shanghai/transport.json'),'utf8').replace(/^\uFEFF/,''));
  let result;
  if(request.action==='edit')result=api.editTrip(data,request.options,request.base_plan,request.operations);
  if(request.action==='schedule')result=api.scheduleSequences(data,request.options,request.edit_spec||{base_plan:request.base_plan,sequences:request.sequences,changed_days:request.changed_days,day_settings:request.day_settings,meal_times:request.meal_times});
  if(request.action==='validate')result=api.validateEditedTrip(data,request.options,request.plan||request.base_plan);
  reply(result);
 }catch(error){reply({error:{code:error.code||'INVALID_ROUTE_REQUEST',message:error.code?String(error.message).slice(0,400):'Не удалось проверить маршрут. Проверьте параметры запроса.',...(error.reason?{reason:error.reason}:{})}});}
});
