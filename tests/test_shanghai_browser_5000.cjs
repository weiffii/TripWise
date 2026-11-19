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
  const stale=await page.evaluate(async()=>{const a=calculatePlan({...readOptions(),days:5}).catch(e=>e.code);const b=calculatePlan({...readOptions(),days:1});return [await a,(await b).days.length];});
  assert.deepEqual(stale,['STALE_PLAN',1]);
  assert.deepEqual(errors,[]);
  const report={target,runs,worker_count:workers.length,blocked_external_requests:blocked,external_network_allowed:false,page_errors:errors,checks:['5000 embedded POIs','worker','responsive event loop','1/3/5 days','day tabs','three languages','map zoom/fit/stop','stale result cancellation']};
  fs.mkdirSync(path.resolve(__dirname,'../reports/benchmarks'),{recursive:true});fs.writeFileSync(path.resolve(__dirname,'../reports/benchmarks/shanghai-5000-browser'+(target.startsWith('file:')?'-file':'')+'.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
