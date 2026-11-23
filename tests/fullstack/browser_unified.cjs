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
 // Проверяем команды плеера и сохранение очереди при переключении вкладок.
 // Наличие и качество системного русского голоса в headless Chrome не оценивается.
 await context.addInitScript(() => {
  const speech = {calls:[],paused:false,getVoices:()=>[],
   speak(utterance){this.calls.push(['speak',utterance.text,utterance.rate,utterance.lang]);},
   cancel(){this.calls.push(['cancel']);},
   pause(){this.paused=true;this.calls.push(['pause']);},
   resume(){this.paused=false;this.calls.push(['resume']);}};
  Object.defineProperty(window,'speechSynthesis',{value:speech});
  window.SpeechSynthesisUtterance = class {constructor(text){this.text=text;}};
 });
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
  const next = page.locator('#questionnaire-next');
  const back = page.locator('#questionnaire-back');
  const step = page.locator('#questionnaire-step-label');
  assert.match(await step.textContent(), /Шаг 1 из 5/);
  assert.equal(await page.locator('#finish-questionnaire').isVisible(), false);
  await next.click();
  assert.match(await step.textContent(), /Шаг 1 из 5/, 'empty dates stop Next');
  await page.locator('#first-walk').fill('2026-12-01T09:00');
  await page.locator('#last-walk').fill(country === 'CN' ? '2026-12-02T19:00' : '2026-12-01T19:00');
  await next.click();
  await page.locator('#budget').fill(budget);
  await page.locator('[name=budget_currency]').selectOption(currency);
  await next.click();
  await next.click();
  assert.match(await step.textContent(), /Шаг 3 из 5/, 'interests are validated on their own step');
  for (const name of ['История','Музеи','Природа']) await page.locator('#primary-interests').getByRole('checkbox',{name,exact:true}).check();
  await back.click();
  assert.equal(await page.locator('#budget').inputValue(), budget);
  await next.click();
  assert.equal(await page.locator('#primary-interests input:checked').count(), 3);
  assert.ok(await page.locator('.interest-image').first().evaluate(img => img.width <= 52 && img.getBoundingClientRect().right < img.parentElement.querySelector('span').getBoundingClientRect().left));
  await next.click();
  const transport = await page.locator('.transport-image').evaluateAll(images => images.map(img => img.getBoundingClientRect().top));
  assert.ok(transport.every((top,index) => !index || top > transport[index-1]));
  assert.ok((await page.locator('html').evaluate(html => html.style.getPropertyValue('--destination-image'))).includes(country === 'RU' ? 'moscow-skyline.svg' : 'shanghai-skyline.svg'));
  assert.ok(await page.locator('.transport-image').first().evaluate(img => img.width <= 52 && img.getBoundingClientRect().right < img.parentElement.querySelector('span').getBoundingClientRect().left));
  await next.click();
  assert.match(await step.textContent(), /Шаг 5 из 5/);
  assert.equal(await next.isVisible(), false);
  await page.waitForFunction(() => document.querySelectorAll('#poi-options input').length === 5);
  const placeRows = page.locator('#poi-options label');
  const names = await placeRows.locator('span').allTextContents();
  await placeRows.nth(0).click();
  await placeRows.nth(1).click();
  assert.equal(await page.locator('#selected-places button').count(), 2);
  const catalog = await json(await context.request.get(base + '/api/planned-trips/catalog/' + (country === 'RU' ? 'moscow' : 'shanghai')));
  const otherPlace = catalog.find(place => !names.some(name => name.toLowerCase().includes(place.name.toLowerCase())));
  assert.ok(otherPlace);
  await page.locator('#required-search').fill(otherPlace.name);
  await placeRows.filter({hasText: otherPlace.name}).first().waitFor();
  assert.equal(await page.locator('#selected-places button').count(), 2);
  while (await page.locator('#selected-places button').count()) await page.locator('#selected-places button').first().click();
  await page.locator('#required-search').fill('');
  await page.locator('#finish-questionnaire').click();await page.waitForURL('**/trip/result?preview=*',{timeout:180000});
  await page.waitForFunction(()=>/Моск|Шанх/.test(document.querySelector('#route-title')?.textContent||''),null,{timeout:60000});
  await page.locator('#save-trip').waitFor({state:'visible'});
  const previewId=new URL(page.url()).searchParams.get('preview');const preview=await json(await context.request.get(base+'/api/route-previews/'+previewId));
  assert.equal(preview.is_preview,true);assert.equal(preview.form_preferences.city,city);assert.ok(preview.plan.days.length);
  assert.ok(preview.plan.days.some(day=>(day.visits?.length||day.events?.filter(e=>e.type==='visit').length)),'real engine should provide visits');
  assert.equal(counts().saved_trips,0,'generation must not save an old questionnaire');assert.equal(counts().planned_trips,generated.length,'generation must not save a route');
  assert.deepEqual(await page.locator('.schedule-block h3').allTextContents(), ['До обеда', 'После обеда', 'После ужина']);
  const scheduleRow = page.locator('.schedule-row').first();
  assert.equal(await scheduleRow.locator('.schedule-cell').count(), 3);
  assert.equal(await scheduleRow.locator('.schedule-action').count(), 1);
  assert.ok(await page.locator('#route-map').count(),'map DOM exists');
  const mapPanel = page.locator('#route-map-panel');
  const mapDialog = page.locator('#route-map-dialog');
  const stop = page.locator('#route-stops button[data-stop-index]').nth(1);
  const scheduleBox = await page.locator('#route-stops').boundingBox();
  assert.ok((await mapPanel.boundingBox()).x > scheduleBox.x + scheduleBox.width);
  const actionRects = await page.locator('.result-actions > :visible').evaluateAll(items => items.map(item => {
    const rect = item.getBoundingClientRect(); return [rect.top,rect.height];
  }));
  assert.ok(actionRects.every(([top,height]) => top === actionRects[0][0] && height === actionRects[0][1]));
  await stop.click();
  assert.equal(await stop.getAttribute('aria-pressed'), 'true');
  assert.equal(await mapPanel.locator('[data-stop][aria-pressed=true]').count(),1);
  await mapPanel.locator('#route-place-details h3').waitFor();
  await page.locator('.route-event-travel .schedule-action button').first().click();
  assert.ok(await mapPanel.locator('polyline[stroke-width="7"]').count(), 'selected transition highlighted');
  await stop.click();
  assert.equal(await mapDialog.isVisible(),false);
  await page.locator('#expand-route-map').click();
  await mapDialog.waitFor({state:'visible'});
  assert.equal((await mapDialog.boundingBox()).width,1440);
  assert.equal(await mapDialog.locator('[data-stop][aria-pressed=true]').count(),1);
  await page.keyboard.press('Escape');
  await page.locator('#route-map-sidebar #route-map-panel').waitFor();
  const editor = page.locator('#route-editor');
  assert.equal(await editor.isVisible(), false);
  for (const button of ['#edit-trip', '#edit-trip-bottom']) {
    assert.equal(await page.locator(button).evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(255, 255, 255)');
    await page.mouse.move(0, 0);
    await page.locator(button).click();
    await editor.waitFor({state:'visible'});
    await page.keyboard.press('Escape');
    await editor.waitFor({state:'hidden'});
  }
  await page.screenshot({path:path.join(artifacts,preview.city+'-preview.png'),fullPage:true});
  if (preview.city === 'moscow') {
    const travel = page.locator('.schedule-row.route-event-travel .schedule-action').first();
    assert.equal(await travel.evaluate(node => getComputedStyle(node).borderTopStyle), 'none');
    await page.locator('[data-theme-toggle]').click();
    await page.locator('.schedule-block').first().scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(artifacts, 'schedule-desktop.png')});
    await page.setViewportSize({width: 375, height: 812});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 375);
    await stop.click();
    await mapDialog.waitFor({state:'visible'});
    assert.equal((await mapDialog.boundingBox()).width,375);
    assert.equal(await mapDialog.locator('[data-stop][aria-pressed=true]').count(),1);
    await page.getByRole('button',{name:'Закрыть карту'}).click();
    await page.locator('#route-map-sidebar #route-map-panel').waitFor();
    assert.equal(await stop.evaluate(node => node === document.activeElement),true);
    await page.setViewportSize({width: 1440, height: 1000});
  }
  const guide = page.getByRole('tab',{name:'Аудиогид'});
  await guide.click();
  assert.equal(await page.locator('#guide-days button').count(),preview.plan.days.length);
  const rendered = await page.locator('#guide-places strong').allTextContents();
  const firstDay = preview.plan.days[0];
  const meals = new Set(Object.values(preview.plan.moscow_meals?.[firstDay.date] || {}).map(meal=>meal.poi_id));
  const metadata = new Map(catalog.map(place=>[place.id,place]));
  const expected = (firstDay.events || firstDay.visits.map(stop=>({...stop,type:'visit'})).sort((a,b)=>a.start_at.localeCompare(b.start_at)))
    .filter(stop=>stop.type==='visit'&&!meals.has(stop.poi_id)&&metadata.has(stop.poi_id)&&metadata.get(stop.poi_id).kind!=='restaurant')
    .map(stop=>metadata.get(stop.poi_id).name);
  assert.deepEqual(rendered,expected,'guide follows the visit order and excludes meals');
  if (preview.plan.days.length > 1) {
    await page.locator('#guide-days button').nth(1).click();
    assert.equal(await page.locator('#guide-days button').nth(1).getAttribute('aria-pressed'),'true');
    await page.locator('#guide-days button').first().click();
  }
  await page.locator('.guide-card').first().click();
  assert.equal(await page.locator('#guide-detail-title').textContent(),rendered[0]);
  await page.locator('#guide-player-slot #guide-player.is-inline').waitFor({state:'visible'});
  assert.equal(await page.locator('#guide-play').textContent(),'Слушать');
  assert.ok((await page.locator('#guide-play').boundingBox()).height >= 52);
  await page.screenshot({path:path.join(artifacts,preview.city+'-inline-player.png')});
  await page.locator('[data-guide-length=long]').click();
  assert.equal(await page.locator('#guide-play').textContent(),'Пауза');
  await page.locator('#guide-show-map').click();
  await page.locator('#guide-player:not(.is-inline)').waitFor({state:'visible'});
  await page.locator('#route-map [data-stop][aria-pressed=true]').waitFor();
  assert.equal(await page.locator('#guide-play').textContent(),'Пауза');
  assert.equal(await page.evaluate(()=>speechSynthesis.calls.filter(call=>call[0]==='cancel').length),0);
  await page.locator('#guide-play').click();
  assert.equal(await page.locator('#guide-play').textContent(),'Продолжить');
  await page.locator('#guide-play').click();
  await page.locator('#guide-speed').selectOption('1.5');
  assert.deepEqual(await page.evaluate(()=>speechSynthesis.calls.at(-1).slice(-2)),[1.5,'ru-RU']);
  if (rendered.length > 1) {
    await page.locator('#guide-next').click();
    assert.equal(await page.locator('#guide-now').textContent(),rendered[1]);
    await page.locator('#guide-previous').click();
    assert.equal(await page.locator('#guide-now').textContent(),rendered[0]);
  }
  await page.locator('#guide-dismiss-player').click();
  assert.doesNotMatch(await page.locator('#cost-note').textContent(), /синтетической планировочной моделью/);
  const download = page.waitForEvent('download');
  await page.locator('#download-travelbook').click();
  const book = await download;
  const localPdf = path.join(artifacts, preview.city+'-TravelBook.pdf');
  await book.saveAs(localPdf);
  const bytes = fs.readFileSync(localPdf).toString('latin1');
  assert.match(bytes,/^%PDF-/);
  assert.equal((bytes.match(/\/Type\s*\/Page\b/g)||[]).length,preview.plan.days.length+1);
  if (preview.city === 'moscow') {
    // Подставляем ответ на подтверждение, чтобы отделить проверку обновления UI
    // от выбора конкретной операции и возможности пересобрать день маршрутизатором.
    const updated = structuredClone(preview);
    const removedId = firstDay.visits.find(visit=>metadata.get(visit.poi_id)?.name===rendered[0]).poi_id;
    updated.plan.days[0].visits = updated.plan.days[0].visits.filter(visit=>visit.poi_id!==removedId);
    updated.version++;
    const baseUrl = base+'/api/route-previews/'+previewId;
    const messageUrl = baseUrl+'/assistant/message';
    const decideUrl = baseUrl+'/assistant/decide';
    await page.route(messageUrl, route=>route.fulfill({json:{
      type:'proposal',message:'Предложена правка',proposal_id:'test-ui-edit',
      base_version:preview.version,plan:updated.plan,diff:{removed:[{name:rendered[0],day:firstDay.date}]},
    }}));
    await page.route(decideUrl, route=>route.fulfill({json:{type:'confirmed',version:updated.version}}));
    await page.route(baseUrl, route=>route.fulfill({json:updated}));
    await page.locator('#edit-trip').click();
    await page.locator('#chat-text').fill('Убери первую остановку');
    await page.locator('#edit-route button').click();
    await page.locator('#confirm-edit').click();
    await page.waitForFunction(()=>document.querySelector('#route-state').textContent.includes('Изменения применены'));
    await page.keyboard.press('Escape');
    await guide.click();
    assert.deepEqual(await page.locator('#guide-places strong').allTextContents(),rendered.filter(name=>name!==rendered[0]));
    await page.getByRole('tab',{name:'Маршрут',exact:true}).click();
    const amendedDownload = page.waitForEvent('download');
    await page.locator('#download-travelbook').click();
    const amendedPdf = path.join(artifacts,'moscow-TravelBook-updated.pdf');
    await (await amendedDownload).saveAs(amendedPdf);
    assert.notDeepEqual(fs.readFileSync(amendedPdf),fs.readFileSync(localPdf),'export reflects updated itinerary');
    for (const url of [messageUrl,decideUrl,baseUrl]) await page.unroute(url);
    await page.reload();
    await page.locator('#save-trip').waitFor({state:'visible'});
  }

  await page.locator('#save-trip').click();await page.waitForURL('**/trip/result?route=*',{timeout:15000});
  const routeId=new URL(page.url()).searchParams.get('route');const saved=await json(await context.request.get(base+'/api/planned-trips/'+routeId));
  assert.equal(saved.city,preview.city);generated.push({routeId,previewId,owner:preview.owner_context,version:saved.version});assert.equal(counts().planned_trips,generated.length);
  const retry=await json(await context.request.post(base+'/api/route-previews/'+previewId+'/save',{headers:{Origin:base,'X-TripWise-Draft-Owner':preview.owner_context},data:{version:preview.version}}));
  assert.equal(retry.route_id,routeId);assert.equal(counts().planned_trips,generated.length,'save retry cannot duplicate');
 }
 await page.locator('#edit-trip-bottom').click();
 await page.waitForURL(url => url.searchParams.has('preview') && url.searchParams.get('edit') === '1');
 await page.locator('#route-editor').waitFor({state:'visible'});
 await page.getByRole('button',{name:'Закрыть чат'}).click();
 assert.equal(counts().planned_trips,2);
 await page.goto(base+'/trips');await page.waitForFunction(()=>document.querySelectorAll('[data-route-id]').length===2);
 await page.screenshot({path:path.join(artifacts,'two-saved-routes.png'),fullPage:true});
 const first=generated[0];await page.locator(`[data-route-id="${first.routeId}"]`).getByRole('button',{name:'Редактировать',exact:true}).click();
 await page.waitForURL('**/trip/result?preview=*');await page.locator('#save-trip').waitFor({state:'visible'});assert.equal(counts().planned_trips,2);
 const editPreview=new URL(page.url()).searchParams.get('preview');const before=await json(await context.request.get(base+'/api/planned-trips/'+first.routeId));
 await page.locator('#change-preferences').click();await readyForm(page);
 await page.locator('#questionnaire-next').click();await page.locator('#budget').fill('7000');
 for(let step=0;step<3;step++)await page.locator('#questionnaire-next').click();
await page.locator('#finish-questionnaire').click();await page.waitForURL('**/trip/result?preview=*',{timeout:180000});
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
 const report={status:'passed',cities:generated.map(x=>x.routeId),checks:['real Moscow+Shanghai engines','no DB persistence until explicit Save','idempotent Save','2 independent routes','edit fork + regenerate + guarded Save','delete cancel+confirm','logo home','375px theme+overflow','five results with full-catalog search and persistent selections','two edit buttons, dialog close, saved route fork','five wizard steps with validation and Back','aligned actions, side map and full-screen selection on mobile','day guide order, food excluded, player persistent on map (speech mock)','real TravelBook download (cover + page per day)','guide and PDF refresh after confirmed assistant response'],externalNetwork:'blocked',screenshots:artifacts};
 fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
main().catch(error=>{console.error(error.stack);console.error('QA temp server log:',path.join(work,'server.log'));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(resolve,5000);});}if(log!==undefined)fs.closeSync(log);});
