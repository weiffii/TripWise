/* TripWise chat transport and preview. Route changes require an explicit decision. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.TripWiseChat=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const copy=value=>JSON.parse(JSON.stringify(value));
const compactPlan=plan=>{const {ranking,...snapshot}=plan;return copy(snapshot);};
const uid=()=>typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
const errorText=code=>({VERSION_CONFLICT:'Маршрут уже изменился. Постройте новый запрос для текущей версии.',VERSION_OR_DATA_CONFLICT:'Маршрут уже изменился. Повторите запрос.',INPUT_DATA_CHANGED:'Данные маршрута обновились. Повторите запрос.',PROPOSAL_EXPIRED:'Предложение устарело. Напишите пожелание ещё раз.',PROPOSAL_ALREADY_DECIDED:'Это предложение уже обработано.',REQUIRED_PLACE_PROTECTED:'Обязательное место защищено: его нельзя удалить или заменить этой командой.',LLM_KEY_NOT_CONFIGURED:'Помощник пока не подключён. Настройте OpenRouter на локальном сервере.',INTERPRETER_UNAVAILABLE:'ИИ-помощник временно недоступен. Исходный маршрут сохранён.',LLM_RATE_LIMITED:'Бесплатный ИИ сейчас занят. Попробуйте позже; маршрут сохранён.',NO_FEASIBLE_INSERTION:'Не удалось добавить место с соблюдением времени и бюджета. Попробуйте другой день или сократите программу.',ROUTE_CONSTRAINT_FAILED:'Все пожелания не помещаются в заданные ограничения. Исходный маршрут сохранён.'})[code]||'Не удалось обработать запрос. Исходный маршрут сохранён; попробуйте ещё раз.';
function createController(config){
 const fetcher=config.fetch||globalThis.fetch,protocol=config.protocol||(typeof location!=='undefined'?location.protocol:'http:');
 let epoch=0,csrf=null,statusPromise=null,registerPromise=null,current=null,registration=null,processing=false,deciding=false,queue=[];const activeRequests=new Set();
 const state={messages:[],proposal:null,busy:false,queued:0,status:'waiting',statusText:'Сначала постройте маршрут.',available:false,version:null,mode:null};
 const notify=()=>{state.busy=processing||deciding;state.queued=queue.length;config.onState?.(copy(state));};
 function message(role,text){state.messages.push({id:uid(),role,text:String(text)});if(state.messages.length>80)state.messages.splice(0,state.messages.length-80);}
 function safeError(error){return error?.userMessage||errorText(error?.code);}
 async function request(path,body){
  const controller=typeof AbortController!=='undefined'?new AbortController():null;
  if(controller)activeRequests.add(controller);
  const timer=setTimeout(()=>controller?.abort(),config.timeoutMs||45000);
  try{
   const response=await fetcher('/api/assistant/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json','X-TripWise-CSRF':csrf||''},...(body===undefined?{}:{body:JSON.stringify(body)}),...(controller?{signal:controller.signal}:{})});
   let data;try{data=await response.json();}catch(error){throw {code:'INVALID_API_RESPONSE'};}
   if(!response.ok){const code=data?.error?.code||data?.code;throw {code,userMessage:typeof data?.message==='string'?data.message:typeof data?.detail==='string'?data.detail:null};}
   if(!data||typeof data!=='object')throw {code:'INVALID_API_RESPONSE'};
   return data;
  }catch(error){if(error?.name==='AbortError')throw {userMessage:'Ответ задерживается. Исходный маршрут сохранён. Попробуйте ещё раз.'};throw error;}finally{clearTimeout(timer);if(controller)activeRequests.delete(controller);}
 }
 async function connect(){
  if(protocol==='file:'){state.status='offline';state.available=false;state.statusText='Для чата запустите START_SHANGHAI_MAP.bat двойным щелчком в папке проекта. Сам маршрут работает и без сервера.';notify();return false;}
  if(statusPromise)return statusPromise;
  const captured=epoch;state.status='connecting';state.statusText='Подключаем помощника…';notify();
  statusPromise=(async()=>{try{const result=await request('status');if(captured!==epoch)return false;csrf=result.csrf_token||null;state.mode=result.mode||'live';state.available=!!result.available&&!!csrf;state.status=state.available?'ready':'offline';state.statusText=state.available?'Помощник подключён. Изменения применяются только после вашего подтверждения.':(result.message||result.reason||'Помощник пока недоступен. Запустите локальный сервер с настроенным OpenRouter.');notify();return state.available;}catch(error){if(captured===epoch){state.available=false;state.status='offline';state.statusText='Нет связи с помощником. Маршрут работает как обычно. Запустите START_SHANGHAI_MAP.bat и проверьте подключение.';notify();}return false;}finally{if(captured===epoch)statusPromise=null;}})();
  return statusPromise;
 }
 async function register(captured){
  if(!current)return false;
  if(registration)return true;
  if(registerPromise)return registerPromise;
  registerPromise=(async()=>{if(!state.available&&!await connect())return false;if(captured!==epoch)return false;
   const snapshot=current;
   try{const result=await request('register',{...(config.serverSnapshot?{}:{options:snapshot.options,plan:compactPlan(snapshot.plan)}),request_id:uid()});if(captured!==epoch)return false;
    if(typeof result.route_id!=='string'||!Number.isInteger(result.version)||typeof result.poi_snapshot_id!=='string')throw {code:'INVALID_API_RESPONSE'};
    registration={route_id:result.route_id,version:result.version,poi_snapshot_id:result.poi_snapshot_id};state.version=result.version;state.status='ready';state.statusText='Помощник готов. Расскажите, что изменить в маршруте.';notify();return true;
   }catch(error){if(captured===epoch){state.status='offline';state.statusText=safeError(error);notify();}return false;}
  })();
  try{return await registerPromise;}finally{if(captured===epoch)registerPromise=null;}
 }
 function setRoute(plan,options){
  if(config.serverSnapshot){state.available=false;csrf=null;}
  epoch++;for(const request of activeRequests)request.abort();statusPromise=null;const captured=epoch;current={plan:copy(plan),options:copy(options)};registration=null;registerPromise=null;state.proposal=null;state.version=null;
  for(const item of queue)item.resolve(false);queue=[];
  state.messages=[];message('assistant','Опишите пожелание обычными словами. Я предложу изменения и сначала покажу, что поменяется.');notify();
  void register(captured);
 }
 async function drain(){
  if(processing||deciding)return;processing=true;notify();
  try{while(queue.length){const item=queue.shift();notify();if(item.epoch!==epoch){item.resolve(false);continue;}const captured=epoch;
    state.proposal=null;notify();
    try{if(!await register(captured)||captured!==epoch){item.resolve(false);continue;}
     state.status='working';state.statusText='Разбираем пожелание и проверяем маршрут…';notify();
     const result=await request('message',{route_id:registration.route_id,base_version:registration.version,poi_snapshot_id:registration.poi_snapshot_id,text:item.text,request_id:item.id});if(captured!==epoch){item.resolve(false);continue;}
     const type=result.type||result.validation_status;
     if(type==='proposal'||type==='ready_for_confirmation'){
      const native=result.plan||result.candidate_plan||result.candidate_route?.native_route;
      if(!native||!Array.isArray(native.days)||typeof result.proposal_id!=='string')throw {code:'INVALID_API_RESPONSE'};
      state.proposal={...result,plan:native,options:result.options||current.options,base_version:result.base_version??registration.version};
      message('assistant',result.message||'Подготовлены изменения. Проверьте их и выберите, применять ли новый вариант.');
     }else message('assistant',result.message||result.clarification||errorText(result.code));
     state.status='ready';state.statusText=state.proposal?'Предложение готово. Текущий маршрут пока не изменён.':'Исходный маршрут сохранён. Можно продолжить диалог.';notify();item.resolve(true);
    }catch(error){if(captured===epoch){message('assistant',safeError(error));state.status='ready';state.statusText='Исходный маршрут сохранён.';notify();}item.resolve(false);}
   }
  }finally{processing=false;notify();}
 }
 function send(text){
  text=String(text||'').trim();if(!text||!current)return Promise.resolve(false);
  if(text.length>3000){message('assistant','Сократите сообщение до 3000 символов. Несколько пожеланий можно отправить вместе.');notify();return Promise.resolve(false);}
  if(queue.length>=5){message('assistant','Дождитесь обработки отправленных сообщений.');notify();return Promise.resolve(false);}
  message('user',text);const promise=new Promise(resolve=>queue.push({id:uid(),epoch,text,resolve}));notify();void drain();return promise;
 }
 async function decide(decision){
  if(deciding||processing||!state.proposal||!registration)return false;
  const captured=epoch,proposal=state.proposal;deciding=true;state.statusText=decision==='confirm'?'Повторно проверяем и применяем изменения…':'Оставляем прежний маршрут…';notify();
  try{const result=await request('decide',{proposal_id:proposal.proposal_id,base_version:proposal.base_version,decision,request_id:uid()});if(captured!==epoch)return false;
   if(result.decision==='confirmed'||result.decision==='confirm'){
    const native=result.plan||result.route?.native_route,settings=result.options||proposal.options;
    if(!native||!Array.isArray(native.days)||result.version!==registration.version+1||result.route_id&&result.route_id!==registration.route_id)throw {code:'INVALID_API_RESPONSE'};
    current={plan:copy(native),options:copy(settings)};registration.version=result.version;state.version=result.version;
    try{config.onApply(copy(native),copy(settings));message('assistant','Изменения применены. Расписание, карта и стоимость обновлены.');}
    catch(error){message('assistant','Сервер подтвердил изменения, но отображение не удалось обновить. Перестройте маршрут перед дальнейшим редактированием.');state.proposal=null;state.status='offline';state.statusText='Подтверждение сохранено на сервере; требуется обновить отображение.';notify();return false;}
   }else if(result.decision==='rejected'||result.decision==='reject')message('assistant','Оставили прежний маршрут. Можно предложить другое изменение.');
   else throw {code:result.code||'INVALID_API_RESPONSE',userMessage:result.message};
   state.proposal=null;state.status='ready';state.statusText='Можно продолжить редактирование текущей версии маршрута.';notify();return true;
  }catch(error){if(captured===epoch){message('assistant',safeError(error));state.proposal=null;state.statusText='Изменения не применены в этом окне. При конфликте версии постройте маршрут заново.';notify();}return false;}
  finally{deciding=false;notify();void drain();}
 }
 return {setRoute,send,decide,connect,getState:()=>copy(state),getRoute:()=>current?copy(current):null};
}
const clock=value=>Number.isFinite(value)?String(Math.floor(value/60)).padStart(2,'0')+':'+String(value%60).padStart(2,'0'):'—';
function previewModel(base,next,getName,formatMoney){
 const name=id=>String(getName(id)||'Место из маршрута'),rows=[];
 for(const day of next.days){const previous=base.days.find(d=>d.date===day.date);if(previous&&JSON.stringify(previous)===JSON.stringify(day))continue;
  const before=(previous?.events||[]).filter(e=>e.poi_id),after=day.events.filter(e=>e.poi_id),beforeIds=new Set(before.map(e=>e.poi_id)),afterIds=new Set(after.map(e=>e.poi_id));
  const notes=[];for(const e of before)if(!afterIds.has(e.poi_id))notes.push('Убираем: '+name(e.poi_id)+' · '+clock(e.start_min)+'–'+clock(e.end_min));for(const e of after)if(!beforeIds.has(e.poi_id))notes.push('Добавляем: '+name(e.poi_id)+' · '+clock(e.start_min)+'–'+clock(e.end_min));
  for(const e of after){const old=before.find(v=>v.poi_id===e.poi_id);if(old&&(old.start_min!==e.start_min||old.end_min!==e.end_min))notes.push(name(e.poi_id)+': '+clock(old.start_min)+'–'+clock(old.end_min)+' → '+clock(e.start_min)+'–'+clock(e.end_min));}
  notes.push('Порядок: '+after.map(e=>name(e.poi_id)).join(' → '));
  const range=events=>events?.length?clock(events[0].start_min)+'–'+clock(events[events.length-1].end_min):'нет событий';
  rows.push({date:day.date,notes,time:range(previous?.events)+' → '+range(day.events),cost:formatMoney(previous?.total_cost_cny||0)+' → '+formatMoney(day.total_cost_cny),travel:(previous?.events||[]).filter(e=>e.type==='travel').reduce((n,e)=>n+e.planned_minutes,0)+' → '+day.events.filter(e=>e.type==='travel').reduce((n,e)=>n+e.planned_minutes,0)+' мин'});
 }
 return {rows,total:formatMoney(base.summary.total_cost_cny)+' → '+formatMoney(next.summary.total_cost_cny),travel:base.summary.planned_travel_minutes+' → '+next.summary.planned_travel_minutes+' мин',warnings:next.warnings||[],partial:next.status!=='demo'};
}
function mount(config){
 const doc=config.root.ownerDocument,root=config.root,log=root.querySelector('[data-chat-log]'),status=root.querySelector('[data-chat-status]'),input=root.querySelector('[data-chat-input]'),form=root.querySelector('[data-chat-form]'),sendButton=root.querySelector('[data-chat-send]'),preview=root.querySelector('[data-chat-preview]'),reconnect=root.querySelector('[data-chat-reconnect]');
 let renderedMessages='';
 const node=(tag,text,className)=>{const el=doc.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
 const controller=createController({...config,onState:state=>{
  status.textContent=(state.mode==='mock'?'Тестовый режим: ответы ИИ заменены проверочными данными. ':'')+state.statusText+(state.queued?' В очереди: '+state.queued+'.':'');root.setAttribute('aria-busy',String(state.busy));
  const signature=state.messages.map(m=>m.id).join('|');if(signature!==renderedMessages){renderedMessages=signature;log.replaceChildren();for(const m of state.messages){const item=node('div',undefined,'chat-message '+(m.role==='user'?'chat-user':'chat-agent'));item.append(node('strong',m.role==='user'?'Вы':'TripWise'),node('p',m.text));log.append(item);}log.scrollTop=log.scrollHeight;}
  sendButton.disabled=!controller.getRoute()||config.protocol==='file:';sendButton.textContent=state.busy?'Отправить следом':'Отправить';input.disabled=!controller.getRoute()||config.protocol==='file:';
  reconnect.hidden=state.available;preview.replaceChildren();preview.hidden=!state.proposal;
  if(state.proposal){const original=controller.getRoute().plan,model=previewModel(original,state.proposal.plan,config.getName,config.formatMoney);preview.append(node('h3','Предложенные изменения'),node('p','До подтверждения ваш маршрут остаётся прежним.'));
   if(model.partial)preview.append(node('p','Это частичный вариант: проверьте предупреждения перед применением.','chat-caution'));
   for(const row of model.rows){const section=node('section',undefined,'chat-day-diff');section.append(node('h4',new Date(row.date+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'long'})));const list=node('ul');for(const text of row.notes)list.append(node('li',text));section.append(list,node('p','Время дня: '+row.time),node('p','Стоимость дня: '+row.cost+' · Дорога: '+row.travel));preview.append(section);}
   preview.append(node('p','Вся поездка: '+model.total+' · Дорога: '+model.travel));
   if(model.warnings.length){const details=node('details'),list=node('ul');details.append(node('summary','Предупреждения ('+model.warnings.length+')'));for(const text of model.warnings)list.append(node('li',String(text)));details.append(list);preview.append(details);}
   const actions=node('div',undefined,'chat-actions'),apply=node('button','Применить изменения','btn-main'),reject=node('button','Оставить прежний маршрут','preset');apply.type=reject.type='button';apply.dataset.chatConfirm='';reject.dataset.chatReject='';apply.disabled=reject.disabled=state.busy;apply.addEventListener('click',()=>void controller.decide('confirm'));reject.addEventListener('click',()=>void controller.decide('reject'));actions.append(apply,reject);preview.append(actions);
  }
 }});
 form.addEventListener('submit',event=>{event.preventDefault();const text=input.value.trim();if(text){input.value='';void controller.send(text);}});
 input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
 reconnect.addEventListener('click',()=>void controller.connect());
 root.querySelectorAll('[data-chat-example]').forEach(button=>button.addEventListener('click',()=>{input.value=button.dataset.chatExample;input.focus();}));
 return controller;
}
return {createController,mount,previewModel,compactPlan};
});
