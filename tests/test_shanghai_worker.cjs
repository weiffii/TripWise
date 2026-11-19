'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const template=fs.readFileSync(path.join(__dirname,'../examples/shanghai-demo/template.html'),'utf8');
const mainScript=[...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].at(-1)[1];
new vm.Script(mainScript.replace('__SHANGHAI_DATA__','{}'));
const source=template.slice(template.indexOf('function createPlanRunner('),template.indexOf('const calculatePlan='));
const create=vm.runInNewContext('('+source+')',{setTimeout,clearTimeout,Error});
function environment(){
 const blobs=new Map(),workers=[],messages=[];let revoked=0;
 class Blob{constructor(parts){this.source=parts.join('');}}
 class Worker{
  constructor(url){this.dead=false;this.ctx=vm.createContext({postMessage:m=>setTimeout(()=>{if(!this.dead)this.onmessage({data:m});},0)});vm.runInContext(blobs.get(url).source,this.ctx);workers.push(this);}
  postMessage(m){messages.push(m);setTimeout(()=>{if(!this.dead)try{this.ctx.onmessage({data:m});}catch(e){this.onerror(e);}},0);}
  terminate(){this.dead=true;}
 }
 return {workers,messages,get revoked(){return revoked;},Worker,Blob,URL:{createObjectURL:b=>{const id=String(blobs.size);blobs.set(id,b);return id;},revokeObjectURL:()=>revoked++},document:{getElementById:id=>({textContent:id.includes('engine')?'globalThis.TripWiseShanghai={planTrip:(data,settings)=>{if(settings.fail)throw Error("fixture failure");return {transport:data.transport,answer:settings.answer};}};':''})}};
}
(async()=>{
 const env=environment(),data={pois:[],transport:{fixture:true}},run=create(data,{},env);
 assert.equal((await run({answer:1})).answer,1);
 assert.equal((await run({answer:2})).answer,2);
 assert.equal(env.messages.filter(m=>m.type==='init').length,1,'dataset is sent only once');
 assert.equal(env.messages.filter(m=>m.type==='plan').length,2);
 assert.equal(env.messages[1].data,undefined,'per-request messages contain only settings');
 const first=run({answer:3}).catch(e=>e.code),second=run({answer:4});
 assert.equal(await first,'STALE_PLAN');assert.equal((await second).answer,4);
 assert(env.revoked>0);assert(env.workers[0].dead);
 await assert.rejects(run({fail:true}),/fixture failure/);
 const crash=run({answer:6});env.workers.at(-1).onerror();await assert.rejects(crash,/расчёт/);
 const fallback=create(data,{planTrip:(d,o)=>({answer:o.answer,transport:d.transport})},{});
 assert.equal((await fallback({answer:5})).answer,5);
 assert(template.includes('const res=await calculatePlan(settings)'));
 assert(template.includes('const next=await calculatePlan(settings)'));
 assert(!template.includes('API.planTrip(DATA,settings)'));
 assert(template.includes('list.slice(0,catalogLimit).map'));
 console.log('Shanghai worker: off-thread protocol, reuse, stale cancellation, errors and fallback passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
