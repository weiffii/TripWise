/* Регрессия ручного сохранения: настоящий questionnaire.js, контролируемые DOM/fetch.
 * Это не браузерная проверка native validity, focus, layout или сети.
 * Запуск: node tests/fullstack/test_questionnaire_manual.cjs
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'frontend/questionnaire.js'), 'utf8');
const visualSource = fs.readFileSync(path.join(root, 'frontend/destination-visuals.js'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'frontend/assets/catalog.json'), 'utf8'));
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
class Element {
  constructor(tag='div') { this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.value='';this.name='';this.checked=false;this.disabled=false;this.hidden=false;this.classList={add(){},remove(){},toggle(){}}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async fire(name, event={}) { for(const fn of this.listeners[name]||[]) await fn({target:this,preventDefault(){},...event}); }
  append(...items) { this.children.push(...items);for(const item of items)item.parent=this; }
  remove() {if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);}
  replaceChildren(...items) {this.children=[...items];if(this.tag==='select')this.value=this.flatten().find(item=>item.tag==='option')?.value||'';}
  flatten() {return this.children.flatMap(item=>[item,...item.flatten()]);}
  setAttribute(name,value) {this[name]=value;}
  setCustomValidity(message) {this.validationMessage=message;}
  querySelectorAll(selector) {
    const name=selector.match(/name="([^"]+)"/)?.[1];
    return this.flatten().filter(item=>item.tag==='input'&&(!name||item.name===name)&&(!selector.includes(':checked')||item.checked));
  }
  querySelector(selector) {return this.querySelectorAll(selector)[0]||null;}
  checkValidity() {return true;}
  reportValidity() {return true;}
}
function basePreferences() {return {
  schema_version:'fullstack-draft-0.2',country_code:'FR',city:'Париж',timezone:'Europe/Paris',
  first_walk_at:'2026-11-24T10:00:00',last_walk_end_at:'2026-11-26T18:00:00',
  travelers:4,traveler_counts:{adults:2,children:1,seniors:1},budget_amount:'100.25',budget_currency:'GBP',
  budget_basis:'per_person',budget_scope:'trip_food_and_entertainment',primary_interests:['history'],secondary_interests:[],pace:'balanced',transport_modes:['walking'],required_poi_ids:['local:paris:eiffel-tower','local:paris:louvre'],
};}
async function boot(preferences=basePreferences()) {
  const ids={}; const get=id=>ids[id] ||= new Element();
  const form=get('#trip-form');form.elements={};
  const fields=get('#trip-fields');fields.disabled=true;form.append(fields);
  const field=(id,name,value,tag='input')=>{const el=get(id);el.tag=tag;el.name=name;el.value=value;form.elements[name]=el;fields.append(el);return el;};
  field('#first-walk','first_walk_at','');field('#last-walk','last_walk_end_at','');field('#country','country_code','','select');field('#city','city','','select');field('#required-search','search','');
  for(const [name,value]of Object.entries({adults:'1',children:'0',seniors:'0',budget_amount:'',budget_currency:'RUB'}))field('#'+name,name,value,name==='budget_currency'?'select':'input');
  for(const [name,values]of Object.entries({budget_basis:['per_person','group'],pace:['relaxed','balanced','intensive'],transport_modes:['walking','public_transit','car']}))for(const value of values){const el=new Element('input');el.name=name;el.value=value;el.checked=['per_person','balanced','walking'].includes(value);fields.append(el);}
  for(const id of ['#primary-interests','#secondary-interests','#poi-options','#selected-places'])fields.append(get(id));
  const win=new Element();win.confirm=()=>true;win.history={replaceState:(_state,_unused,url)=>{win.location.search=url.slice(url.indexOf('?'));}};win.location={search:preferences?'?id=11':'',assign:target=>navigations.push(target),replace:target=>navigations.push(target)};
  const calls=[],navigations=[],timers=new Map();let serial=0;let pending=null;
  const ok=data=>({ok:true,status:200,headers:{get:()=>null},json:async()=>data});
  const fetch=async(url,options={})=>{calls.push({url,...options});if(['PUT','POST'].includes(options.method))return await new Promise(resolve=>{pending=resolve;});return ok(url==='/api/catalog'?catalog:{owner_context:'owner-A',trip:preferences?{id:11,preferences,revision:7}:null,trips:[]});};
  const styleProperties={};
  const context=vm.createContext({document:{documentElement:{style:{setProperty:(key,value)=>{styleProperties[key]=value;}}},querySelector:get,createElement:tag=>new Element(tag)},window:win,fetch,Intl,AbortController,Set,console,URLSearchParams,crypto:require('node:crypto').webcrypto,setTimeout:(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(visualSource,context,{filename:'destination-visuals.js'});
  vm.runInContext(source,context,{filename:'questionnaire.js'});await flush();
  assert.equal(fields.disabled,false,'draft must load');
  return {get,form,fields,win,calls,navigations,timers,context,styleProperties,putCalls:()=>calls.filter(call=>['PUT','POST'].includes(call.method)),resolvePut:(response)=>{assert.ok(pending,'PUT must be pending');pending(response||ok({trip:{id:11,revision:8}}));pending=null;},ok};
}
async function checkTripCards() {
  const ids={};const get=id=>ids[id] ||= new Element();const win=new Element();win.confirm=()=>false;win.location={replace(){}};
  const calls=[],responses=[];const trips=[{id:11,revision:2,preferences:basePreferences()},{id:22,revision:1,preferences:{...basePreferences(),city:'Стамбул'}}];
  const response=(ok,data)=>({ok,json:async()=>data});
  const fetch=async(url,options={})=>{calls.push({url,...options});if(options.method==='DELETE'){assert.ok(responses.length,'delete response supplied');return responses.shift();}return response(true,url==='/api/auth/me'?{user:{email:'qa@example.com'}}:{owner_context:'owner-A',trips});};
  const context=vm.createContext({document:{documentElement:{style:{setProperty(){}}},querySelector:get,createElement:tag=>new Element(tag)},window:win,fetch,Intl,console});
  vm.runInContext(visualSource,context);vm.runInContext(fs.readFileSync(path.join(root,'frontend/trips.js'),'utf8'),context);await win.fire('pageshow');
  const list=get('#saved-trips');assert.equal(list.children.length,2);const first=list.children[0];const remove=first.flatten().find(item=>item.className==='delete-trip');const edit=first.flatten().find(item=>item.tag==='a');assert.equal(edit.href,'/trip/new?id=11');
  await remove.fire('click');assert.equal(calls.filter(call=>call.method==='DELETE').length,0,'cancel delete never sends DELETE');assert.equal(list.children.length,2);
  win.confirm=()=>true;responses.push(response(false,{detail:'Revision conflict'}));await remove.fire('click');assert.equal(list.children.length,2,'failed delete must retain card');assert.match(get('#trips-message').textContent,/Revision conflict/);
  responses.push(response(true));await remove.fire('click');assert.equal(list.children.length,1);assert.equal(list.children[0].dataset.tripId,'22','only requested trip removed');const request=calls.filter(call=>call.method==='DELETE').at(-1);assert.equal(request.url,'/api/trips/11');assert.equal(JSON.parse(request.body).revision,2);assert.equal(request.headers['X-TripWise-Draft-Owner'],'owner-A');
}
async function run() {
  const a=await boot();
  assert.equal(a.putCalls().length,0,'loading must not PUT');
  assert.equal(a.form.elements.country_code.value,'FR');assert.equal(a.form.elements.city.value,'Париж');
  assert.equal(a.form.elements.budget_currency.value,'GBP');assert.match(a.styleProperties['--destination-image'],/paris-sunset/,'selected Paris uses Paris image');assert.equal(a.form.elements.children.value,1);
  assert.equal(vm.runInContext('requiredPlaces.size',a.context),2,'all required places restored');
  assert.match(a.get('#summary-budget').textContent,/401/,'group budget keeps correct amount');
  a.form.elements.budget_amount.value='125.50';await a.form.fire('input',{target:a.form.elements.budget_amount});
  assert.equal(a.putCalls().length,0,'input must not PUT');assert.equal(a.timers.size,0,'input must not schedule hidden autosave');
  let warned=false;await a.win.fire('beforeunload',{preventDefault(){warned=true;}});assert.equal(warned,true);assert.equal(a.putCalls().length,0,'beforeunload must not PUT');
  const saving=a.form.fire('submit');await flush();
  assert.equal(a.putCalls().length,1,'explicit submit issues one PUT');
  const snapshot=JSON.parse(a.putCalls()[0].body);
  assert.equal(snapshot.preferences.budget_amount,'125.50');assert.equal(snapshot.preferences.schema_version,'fullstack-draft-0.2');assert.equal(snapshot.preferences.timezone,'Europe/Paris');
  assert.equal(snapshot.preferences.required_poi_ids.length,2);assert.equal(a.putCalls()[0].headers['X-TripWise-Draft-Owner'],'owner-A');assert.equal(snapshot.revision,7);
  a.form.elements.budget_amount.value='200.00';await a.form.fire('input',{target:a.form.elements.budget_amount});
  await a.form.fire('submit');assert.equal(a.putCalls().length,1,'second click while in-flight must not issue another PUT');
  a.resolvePut();await saving;await flush();
  assert.equal(a.putCalls().length,1,'post-click changes must not be sent automatically');assert.equal(a.navigations.length,0,'unsaved newer edits stay visible');assert.match(a.get('#save-status').textContent,/Новые правки ещё не сохранены/);
  assert.equal(JSON.parse(a.putCalls()[0].body).preferences.budget_amount,'125.50','request snapshot is immutable');
  const second=a.form.fire('submit');await flush();assert.equal(a.putCalls().length,2);assert.equal(JSON.parse(a.putCalls()[1].body).revision,8);assert.equal(JSON.parse(a.putCalls()[1].body).preferences.budget_amount,'200.00');a.resolvePut(a.ok({trip:{id:11,revision:9}}));await second;assert.deepEqual(a.navigations,['/trips']);
  const fresh=await boot(null);assert.equal(fresh.form.elements.city.value,'','new form does not reuse old saved trip');assert.equal(fresh.putCalls().length,0);fresh.context.fixture=basePreferences();vm.runInContext('fillForm(fixture)',fresh.context);await fresh.form.fire('input',{target:fresh.form.elements.budget_amount});
  for(const input of fresh.form.querySelectorAll('[name="primary_interests"]'))input.checked=true;await fresh.form.fire('input',{target:fresh.form.elements.budget_amount});assert.equal(fresh.form.querySelectorAll('[name="primary_interests"]').filter(input=>input.disabled).length,0,'all eight primary options remain enabled');
  const creating=fresh.form.fire('submit');await flush();const newRequest=JSON.parse(fresh.putCalls()[0].body);assert.equal(fresh.putCalls()[0].method,'POST');assert.equal(fresh.putCalls()[0].url,'/api/trips');assert.match(newRequest.creation_key,/^[0-9a-f-]{36}$/);assert.equal('revision' in newRequest,false);assert.equal(newRequest.preferences.primary_interests.length,8,'create keeps all selected primary interests');fresh.form.elements.budget_amount.value='300';await fresh.form.fire('input',{target:fresh.form.elements.budget_amount});fresh.resolvePut(fresh.ok({trip:{id:23,revision:1}}));await creating;assert.equal(fresh.navigations.length,0);assert.equal(fresh.putCalls().length,1);
  const firstUpdate=fresh.form.fire('submit');await flush();assert.equal(fresh.putCalls()[1].method,'PUT');assert.equal(fresh.putCalls()[1].url,'/api/trips/23');assert.equal(JSON.parse(fresh.putCalls()[1].body).revision,1);fresh.resolvePut(fresh.ok({trip:{id:23,revision:2}}));await firstUpdate;
  const rejected=await boot(null);rejected.context.fixture=basePreferences();vm.runInContext('fillForm(fixture)',rejected.context);await rejected.form.fire('input',{target:rejected.form.elements.budget_amount});const rejectedSave=rejected.form.fire('submit');await flush();rejected.resolvePut({ok:false,status:422,headers:{get:()=>null},json:async()=>({detail:'Validation failed'})});await rejectedSave;rejected.form.elements.budget_amount.value='555';await rejected.form.fire('input',{target:rejected.form.elements.budget_amount});const corrected=rejected.form.fire('submit');await flush();assert.equal(JSON.parse(rejected.putCalls()[1].body).preferences.budget_amount,'555','definite422 must release old snapshot so correction can save');rejected.resolvePut(rejected.ok({trip:{id:30,revision:1}}));await corrected;
  const uncertain=await boot(null);uncertain.context.fixture=basePreferences();vm.runInContext('fillForm(fixture)',uncertain.context);await uncertain.form.fire('input',{target:uncertain.form.elements.budget_amount});const firstAttempt=uncertain.form.fire('submit');await flush();const original=uncertain.putCalls()[0].body;uncertain.resolvePut({ok:true,status:201,headers:{get:()=>null},json:async()=>{throw Error('lost response body');}});await firstAttempt;uncertain.form.elements.budget_amount.value='777';await uncertain.form.fire('input',{target:uncertain.form.elements.budget_amount});const retry=uncertain.form.fire('submit');await flush();assert.equal(uncertain.putCalls()[1].body,original,'uncertain POST retry keeps original UUID and snapshot');uncertain.resolvePut(uncertain.ok({trip:{id:40,revision:1}}));await retry;assert.equal(uncertain.navigations.length,0,'newer777 edit stays unsaved');assert.equal(uncertain.form.elements.budget_amount.value,'777');assert.equal(uncertain.putCalls().length,2,'retry never silently adds PUT');
  const legacy=basePreferences();legacy.schema_version='fullstack-draft-0.1';legacy.country_code='RU';legacy.city='Москва';legacy.timezone='Europe/Moscow';legacy.first_walk_at='2026-11-24T10:00:00+03:00';legacy.last_walk_end_at='2026-11-26T18:00:00+03:00';legacy.required_poi_ids=[];delete legacy.traveler_counts;const b=await boot(legacy);assert.equal(b.form.elements.adults.value,4);assert.equal(b.form.elements.children.value,0);assert.equal(b.get('#first-walk').value,'2026-11-24T10:00');assert.equal(b.putCalls().length,0);assert.match(b.styleProperties['--destination-image'],/moscow-sunset/,'legacy Moscow uses Moscow image');
  const switched=await boot();
  switched.win.confirm=()=>false;switched.form.elements.country_code.value='RU';await switched.get('#country').fire('change');assert.equal(switched.form.elements.country_code.value,'FR');assert.equal(switched.form.elements.city.value,'Париж');assert.equal(vm.runInContext('requiredPlaces.size',switched.context),2,'cancel destination change retains old POIs');
  switched.form.elements.city.value='other';await switched.get('#city').fire('change');assert.equal(switched.form.elements.city.value,'Париж');assert.equal(vm.runInContext('requiredPlaces.size',switched.context),2);
  switched.win.confirm=()=>true;switched.form.elements.country_code.value='RU';await switched.get('#country').fire('change');assert.equal(vm.runInContext('requiredPlaces.size',switched.context),0,'confirmed new destination clears foreign POIs');assert.equal(switched.putCalls().length,0,'destination change still needs explicit save');assert.match(switched.styleProperties['--destination-image'],/travel-landscape/,'no city uses neutral image');
  const failed=await boot();failed.form.elements.budget_amount.value='900';await failed.form.fire('input',{target:failed.form.elements.budget_amount});const failing=failed.form.fire('submit');await flush();failed.resolvePut({ok:false,status:409,headers:{get:()=> 'ACCOUNT_CHANGED'},json:async()=>({detail:'Аккаунт изменился'})});await failing;assert.equal(failed.form.elements.budget_amount.value,'900');assert.equal(failed.get('#save-status').dataset.state,'error');assert.equal(failed.navigations.length,0);assert.equal(failed.putCalls().length,1);
  await checkTripCards();
  return 'PASS: load/input/unload no PUT; one click immutable snapshot; no extra PUT during/after concurrent edits; explicit second save; new POST then id-specific PUT;422 correction and uncertain POST idempotency; legacy restore; destination confirmation/cancel retains or clears POIs; account-change error retains fields; card edit ID and cancel/error/success delete.';
}
module.exports=run;
if(require.main===module)run().then(console.log).catch(error=>{console.error(error);process.exitCode=1;});
