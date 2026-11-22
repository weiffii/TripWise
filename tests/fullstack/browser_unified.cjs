/* Реальный изолированный браузер: никакие пользовательские профили/БД не используются.
 * NODE_PATH=<installed playwright directory> node tests/fullstack/browser_unified.cjs
 * Внешние tiles/API блокируются: геометрия плана проверяется, живая навигация не заявляется.
 */
const {chromium} = require(process.env.TRIPWISE_PLAYWRIGHT || '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn, spawnSync} = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'tripwise-unified-browser-'));
const database = path.join(work, 'qa.sqlite3');
const artifacts = process.env.TRIPWISE_QA_ARTIFACTS || '/tmp/tripwise-unified-browser';
fs.mkdirSync(artifacts, {recursive:true});
const freePort = () => new Promise((resolve,reject)=>{const server=net.createServer();server.on('error',reject);server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
let server, browser, log;
function counts(){
 const result=spawnSync(path.join(root,'.venv/bin/python'),['-c',"import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps({t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('saved_trips','planned_trips')})); c.close()",database],{encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
async function json(response){const body=await response.json();assert.ok(response.ok(),`${response.status()} ${JSON.stringify(body).slice(0,700)}`);return body;}
async function readyForm(page){await page.waitForURL(/\/trip\/new(?:\?.*)?$/);await page.locator('#trip-fields').waitFor({state:'visible'});await page.waitForFunction(()=>{const field=document.querySelector('#trip-fields');return field&&!field.disabled;});}
async function main(){
 const port=await freePort();const base=`http://127.0.0.1:${port}`;
 const env={...process.env,TRIPWISE_DB_PATH:database,NO_PROXY:'127.0.0.1,localhost',no_proxy:'127.0.0.1,localhost'};
 delete env.OPENROUTER_API_KEY;
 env.PATH='/Applications/ChatGPT.app/Contents/Resources/cua_node/bin:'+env.PATH;
 log=fs.openSync(path.join(work,'server.log'),'w');
 server=spawn(path.join(root,'.venv/bin/python'),['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env,stdio:['ignore',log,log]});
 for(let i=0;i<150;i++){if(server.exitCode!==null)throw Error('QA server exited');try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(i===149)throw Error('QA server start timeout');await sleep(100);}
 browser=await chromium.launch({headless:true,executablePath:process.env.TRIPWISE_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base);await page.getByRole('button',{name:'Создать поездку',exact:true}).first().click();
 await page.locator('#email').fill('unified-browser@example.com');
 await page.locator('#password').fill('TripWise-Browser-2026!');await page.locator('#confirm-password').fill('TripWise-Browser-2026!');
 await page.locator('#submit-button').click();await page.waitForURL(/\/trip\/new(?:\?new=1)?$/);
 await readyForm(page);
 const countries=await page.locator('#country option').evaluateAll(options=>options.filter(o=>o.value).map(o=>({code:o.value,disabled:o.disabled})));
 assert.deepEqual(countries.slice(0,2).map(o=>o.code),['RU','CN']);assert.ok(countries.slice(2).every(o=>o.disabled));
 const generated=[];
 for(const [country,city,currency,budget] of [['RU','Москва','RUB','6000'],['CN','Шанхай','CNY','1000']]){
  if(generated.length){await page.goto(base+'/trip/new?new=1');await readyForm(page);assert.equal(await page.locator('#city').inputValue(),'');}
  await page.locator('#country').selectOption(country);await page.locator('#city').selectOption(city);
  await page.locator('#first-walk').fill('2026-12-01T09:00');await page.locator('#last-walk').fill('2026-12-01T19:00');
  await page.locator('#budget').fill(budget);await page.locator('[name=budget_currency]').selectOption(currency);
  for(const name of ['История','Музеи','Природа'])await page.locator('#primary-interests').getByRole('checkbox',{name,exact:true}).check();
  await page.locator('#finish-questionnaire').click();await page.waitForURL('**/trip/result?preview=*',{timeout:180000});
  await page.waitForFunction(()=>/Моск|Шанх/.test(document.querySelector('#route-title')?.textContent||''),null,{timeout:60000});
  await page.locator('#save-trip').waitFor({state:'visible'});
  const previewId=new URL(page.url()).searchParams.get('preview');const preview=await json(await context.request.get(base+'/api/route-previews/'+previewId));
  assert.equal(preview.is_preview,true);assert.equal(preview.form_preferences.city,city);assert.ok(preview.plan.days.length);
  assert.ok(preview.plan.days.some(day=>(day.visits?.length||day.events?.filter(e=>e.type==='visit').length)),'real engine should provide visits');
  assert.equal(counts().saved_trips,0,'generation must not save an old questionnaire');assert.equal(counts().planned_trips,generated.length,'generation must not save a route');
  assert.ok(await page.locator('#route-stops li').count(),'schedule rendered');assert.ok(await page.locator('#route-map').count(),'map DOM exists');
  await page.screenshot({path:path.join(artifacts,preview.city+'-preview.png'),fullPage:true});
  await page.locator('#save-trip').click();await page.waitForURL('**/trip/result?route=*',{timeout:15000});
  const routeId=new URL(page.url()).searchParams.get('route');const saved=await json(await context.request.get(base+'/api/planned-trips/'+routeId));
  assert.equal(saved.city,preview.city);generated.push({routeId,previewId,owner:preview.owner_context,version:saved.version});assert.equal(counts().planned_trips,generated.length);
  const retry=await json(await context.request.post(base+'/api/route-previews/'+previewId+'/save',{headers:{Origin:base,'X-TripWise-Draft-Owner':preview.owner_context},data:{version:preview.version}}));
  assert.equal(retry.route_id,routeId);assert.equal(counts().planned_trips,generated.length,'save retry cannot duplicate');
 }
 await page.goto(base+'/trips');await page.waitForFunction(()=>document.querySelectorAll('[data-route-id]').length===2);
 await page.screenshot({path:path.join(artifacts,'two-saved-routes.png'),fullPage:true});
 const first=generated[0];await page.locator(`[data-route-id="${first.routeId}"]`).getByRole('button',{name:'Редактировать',exact:true}).click();
 await page.waitForURL('**/trip/result?preview=*');await page.locator('#save-trip').waitFor({state:'visible'});assert.equal(counts().planned_trips,2);
 const editPreview=new URL(page.url()).searchParams.get('preview');const before=await json(await context.request.get(base+'/api/planned-trips/'+first.routeId));
 await page.locator('#change-preferences').click();await readyForm(page);
 await page.locator('#budget').fill('7000');await page.locator('#finish-questionnaire').click();await page.waitForURL('**/trip/result?preview=*',{timeout:180000});
 await page.locator('#save-trip').waitFor({state:'visible'});const still=await json(await context.request.get(base+'/api/planned-trips/'+first.routeId));assert.deepEqual(still,before,'regenerate edit must leave saved route untouched');
 await page.locator('#save-trip').click();await page.waitForURL('**/trip/result?route=*');assert.equal(new URL(page.url()).searchParams.get('route'),first.routeId);
 const updated=await json(await context.request.get(base+'/api/planned-trips/'+first.routeId));assert.equal(updated.version,before.version+1);assert.equal(counts().planned_trips,2);
 await page.goto(base+'/trips');await page.waitForFunction(()=>document.querySelectorAll('[data-route-id]').length===2);
 page.once('dialog',dialog=>dialog.dismiss());await page.locator(`[data-route-id="${generated[1].routeId}"]`).getByRole('button',{name:/Удалить/}).click();assert.equal(counts().planned_trips,2);
 page.once('dialog',dialog=>dialog.accept());await page.locator(`[data-route-id="${generated[1].routeId}"]`).getByRole('button',{name:/Удалить/}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-route-id]').length===1);assert.equal(counts().planned_trips,1);
 await page.getByRole('link',{name:'TripWise — главная',exact:true}).click();await page.waitForURL(base+'/');
 await page.setViewportSize({width:375,height:812});await page.reload();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),375);
 const theme=await page.locator('html').getAttribute('data-theme');await page.locator('[data-theme-toggle]').click();assert.notEqual(await page.locator('html').getAttribute('data-theme'),theme);
 await page.screenshot({path:path.join(artifacts,'mobile-home.png'),fullPage:false});
 assert.deepEqual(errors,[],'no page JS errors');
 const report={status:'passed',cities:generated.map(x=>x.routeId),checks:['real Moscow+Shanghai engines','no DB persistence until explicit Save','idempotent Save','2 independent routes','edit fork + regenerate + guarded Save','delete cancel+confirm','logo home','375px theme+overflow'],externalNetwork:'blocked',screenshots:artifacts};
 fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
main().catch(error=>{console.error(error.stack);console.error('QA temp server log:',path.join(work,'server.log'));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(resolve,5000);});}if(log!==undefined)fs.closeSync(log);});
