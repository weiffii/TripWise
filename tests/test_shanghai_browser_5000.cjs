'use strict';
// Real Chrome, offline: UI/worker integration only, not verification of OSM roads.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const runtime=path.resolve(path.dirname(process.execPath),'../node_modules/playwright');
const {chromium}=require(runtime);
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage(),errors=[],workers=[];let blocked=0;
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('worker',w=>workers.push(w.url()));
  await page.route('**/*',r=>{const u=r.request().url();if(/\/tiles\//.test(u)||(/^https?:/.test(u)&&!/^http:\/\/127\.0\.0\.1[:/]/.test(u))){blocked++;return r.abort();}return r.continue();});
  const target=process.argv[2]||process.env.SHANGHAI_TEST_URL||pathToFileURL(path.resolve(__dirname,'../artifacts/tripwise-shanghai.html')).href;
  await page.goto(target,{waitUntil:'domcontentloaded',timeout:30000});
  await page.waitForFunction(()=>typeof plan!=='undefined'&&plan?.days?.length>0,{timeout:60000});
  assert.equal(await page.evaluate(()=>DATA.pois.length),5000);
  assert(workers.length>0,'calculation must run in a Worker in Chrome');
  const runs=[];
  for(const days of [1,3,5]){
   await page.selectOption('#days',String(days));
   const result=await page.evaluate(async()=>{let ticks=0;const interval=setInterval(()=>ticks++,20),t=performance.now();await runPlan();clearInterval(interval);return {days:plan.days.length,status:plan.status,ms:performance.now()-t,ticks,visits:plan.summary.visit_count,checks:plan.checks};});
   console.log(JSON.stringify({probe:days,...result,error:await page.locator('#form-error').textContent()}));
   assert.equal(result.days,days);assert.equal(result.checks.time,'passed');assert.equal(result.checks.budget,'passed');assert.equal(result.checks.opening_hours,'passed');
   if(result.ms>100)assert(result.ticks>0,'UI event loop blocked');runs.push(result);
  }
  await page.click('[data-day="1"]');
  assert.equal(await page.evaluate(()=>selectedDay),1);
  for(const language of ['en','zh','ru'])await page.selectOption('#name-language',language);
  await page.click('[data-action="in"]');await page.click('[data-action="fit"]');
  assert((await page.locator('[data-map-stop]').count())>0);
  await page.locator('[data-map-stop]').first().click();
  const gapReasons=await page.evaluate(()=>plan.days.flatMap(d=>d.events.filter((e,i)=>i&&e.start_min-d.events[i-1].end_min>=90).map(e=>({reason:e.waiting_reason,minutes:e.wait_before_min}))));
  assert(gapReasons.every(g=>typeof g.reason==='string'&&g.reason.length>0),'Long waits need an explicit calculated reason');
  const cachedRoadRead=await page.evaluate(()=>{const map=getRouteMap(),original=map.getOsmTables;const value={foot:{ids:['a','b'],durations:[[0,60],[60,0]],distances:[[0,50],[50,0]]}};map.getOsmTables=()=>value;try{return JSON.stringify(readOptions().osm_tables)===JSON.stringify(value);}finally{map.getOsmTables=original;}});
  assert(cachedRoadRead,'Normal form builds must retain confirmed road times');
  const stale=await page.evaluate(async()=>{const a=calculatePlan({...readOptions(),days:5}).catch(e=>e.code);const b=calculatePlan({...readOptions(),days:1});return [await a,(await b).days.length];});
  assert.deepEqual(stale,['STALE_PLAN',1]);
  const scenarios=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../data/demo/shanghai/qa_scenarios.json'),'utf8')).scenarios;
  const residualInputs=scenarios.filter(s=>['09_low_budget','16_child_pet','17_songjiang_heritage','24_impossible_budget'].includes(s.id));
  residualInputs.push({id:'cafes_only',input:{...scenarios[0].input,primary_interests:['cafes'],secondary_interests:[],days:1,required_ids:[],transport:'mixed',budget_cny:2000}});
  const residual=[];
  for(const s of residualInputs){const r=await page.evaluate(async input=>{const p=await calculatePlan(input);return {status:p.status,visits:p.summary.visit_count,meals:p.summary.meal_count,checks:p.checks,warnings:p.warnings,events:p.days.reduce((n,d)=>n+d.events.length,0)};},s.input);
   assert.equal(r.checks.time,'passed');assert.equal(r.checks.budget,'passed');assert.equal(r.checks.opening_hours,'passed');
   if(s.id==='cafes_only'){assert.equal(r.status,'demo');assert(r.meals>0);}
   if(s.id==='17_songjiang_heritage'){assert.equal(r.checks.required_places,'passed');assert.equal(r.meals,6);}
   if(s.id==='16_child_pet'){assert.equal(r.events,0);assert(r.warnings.some(w=>/питом/i.test(w)&&/транспорт/i.test(w)));}
   if(['09_low_budget','24_impossible_budget'].includes(s.id)){assert.equal(r.status,'partial');if(s.id==='09_low_budget')assert(r.visits>0);assert.equal(r.checks.meals,'failed');assert(r.warnings.some(w=>/бюджет/i.test(w)));}
   residual.push({id:s.id,...r});
  }
  assert.deepEqual(errors,[]);
  const report={target,runs,residual,worker_count:workers.length,blocked_external_requests:blocked,external_network_allowed:false,page_errors:errors,checks:['5000 embedded POIs','worker','responsive event loop','1/3/5 days','day tabs','three languages','map zoom/fit/stop','stale result cancellation','cafes-only','Songjiang food','low-budget partial visits','pet/transit preflight']};
  fs.mkdirSync(path.resolve(__dirname,'../reports/benchmarks'),{recursive:true});fs.writeFileSync(path.resolve(__dirname,'../reports/benchmarks/shanghai-5000-browser'+(target.startsWith('file:')?'-file':'')+'.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
