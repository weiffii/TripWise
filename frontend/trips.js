const accountEmail = document.querySelector('#account-email');
const logoutButton = document.querySelector('#logout-button');
const tripsMessage = document.querySelector('#trips-message');
const savedTrips = document.querySelector('#saved-trips');
let ownerContext = null;
function showError(text) { tripsMessage.textContent=text;tripsMessage.hidden=false; }
function textElement(tag,className,text){const el=document.createElement(tag);el.className=className;el.textContent=text;return el;}
function cityName(city){return {moscow:'Москва',shanghai:'Шанхай'}[city]||city||'Поездка';}
function confirmDelete(title){return window.confirm(`Удалить поездку «${title}»? Это действие нельзя отменить.`);}
async function removeCard(card,button,path,payload,title){
  if(!confirmDelete(title))return;button.disabled=true;
  try{const response=await fetch(path,{method:'DELETE',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TripWise-Draft-Owner':ownerContext},body:JSON.stringify(payload)});
    if(!response.ok){const result=await response.json();showError(typeof result.detail==='string'?result.detail:'Не удалось удалить поездку. Обновите список.');return;}
    card.remove();document.querySelector('#empty-trips').hidden=savedTrips.children.length>0;
  }catch{showError('Не удалось подтвердить удаление. Обновите страницу, чтобы проверить список.');}finally{button.disabled=false;}
}
function cardBase(city,label,status){
 const card=document.createElement('article');card.className='saved-draft trip-card';const image=document.createElement('img');image.className='trip-card-image';image.alt='';image.src=window.destinationImagePath(city);image.loading='lazy';card.append(image,textElement('span','section-number',label),textElement('h2','',city),textElement('span','draft-status',status));return card;
}
function renderTrips(drafts,routes){
 savedTrips.replaceChildren();document.querySelector('#empty-trips').hidden=drafts.length+routes.length>0;
 for(const trip of routes){
   const city=cityName(trip.city);const card=cardBase(city,'СОХРАНЁННАЯ ПОЕЗДКА','Маршрут создан');card.dataset.routeId=trip.route_id;
   card.append(textElement('p','trip-dates',`${trip.start_date||''} · версия ${trip.version}`));
   const actions=document.createElement('div');actions.className='trip-card-actions';
   const open=textElement('a','outline-button','Открыть маршрут');open.href='/trip/result?route='+encodeURIComponent(trip.route_id);
   const edit=textElement('button','outline-button','Редактировать');edit.type='button';
   edit.addEventListener('click',async()=>{edit.disabled=true;try{const response=await fetch('/api/planned-trips/'+encodeURIComponent(trip.route_id)+'/preview',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TripWise-Draft-Owner':ownerContext},body:JSON.stringify({version:trip.version})});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Не удалось открыть редактирование.');window.location.assign('/trip/result?preview='+encodeURIComponent(result.preview_id));}catch(error){showError(error.message);}finally{edit.disabled=false;}});
   const remove=textElement('button','delete-trip','Удалить');remove.type='button';remove.setAttribute('aria-label','Удалить поездку '+city);remove.addEventListener('click',()=>void removeCard(card,remove,'/api/planned-trips/'+encodeURIComponent(trip.route_id),{version:trip.version},city));
   actions.append(open,edit,remove);card.append(actions);savedTrips.append(card);
 }
 for(const trip of drafts){
   const p=trip.preferences;const card=cardBase(p.city,'ПРЕЖНЯЯ АНКЕТА','Можно создать маршрут');card.dataset.tripId=String(trip.id);
   const date=value=>value.slice(0,10).split('-').reverse().join('.');const dates=`${date(p.first_walk_at)} — ${date(p.last_walk_end_at)}`;
   const counts=p.traveler_counts||{adults:p.travelers,children:0,seniors:0};const amount=Number(p.budget_amount)*(p.budget_basis==='per_person'?p.travelers:1);
   const budget=new Intl.NumberFormat('ru-RU',{style:'currency',currency:p.budget_currency||'RUB',maximumFractionDigits:2}).format(amount);
   card.append(textElement('p','trip-dates',dates),textElement('p','',`Взрослые: ${counts.adults} · Дети: ${counts.children} · Пожилые: ${counts.seniors}`),textElement('p','trip-budget',`Бюджет компании: ${budget}`));
   const actions=document.createElement('div');actions.className='trip-card-actions';const edit=textElement('a','outline-button','Открыть анкету');edit.href=`/trip/new?id=${trip.id}`;
   const remove=textElement('button','delete-trip','Удалить');remove.type='button';remove.setAttribute('aria-label',`Удалить анкету ${p.city}, ${dates}`);remove.addEventListener('click',()=>void removeCard(card,remove,`/api/trips/${trip.id}`,{revision:trip.revision},p.city));
   actions.append(edit,remove);card.append(actions);savedTrips.append(card);
 }
}
async function loadAccount(){
 try{
   const account=await fetch('/api/auth/me',{cache:'no-store',credentials:'same-origin'});if(account.status===401){window.location.replace('/');return;}if(!account.ok)throw Error();const user=await account.json();accountEmail.textContent=user.user.email;
   const [draftResponse,routeResponse]=await Promise.all([fetch('/api/trips',{cache:'no-store',credentials:'same-origin'}),fetch('/api/planned-trips',{cache:'no-store',credentials:'same-origin'})]);
   if(!draftResponse.ok||!routeResponse.ok)throw Error();const drafts=await draftResponse.json();const routes=await routeResponse.json();if(typeof drafts.owner_context!=='string'||!drafts.owner_context||drafts.owner_context!==routes.owner_context)throw Error();ownerContext=drafts.owner_context;renderTrips(drafts.trips,routes.trips);tripsMessage.hidden=true;
 }catch{showError('Не удалось загрузить поездки. Проверьте соединение и обновите страницу.');}
}
logoutButton.addEventListener('click',async()=>{logoutButton.disabled=true;try{const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});if(!response.ok)throw Error();window.location.replace('/');}catch{showError('Не удалось выйти. Повторите попытку.');logoutButton.disabled=false;}});
window.addEventListener('pageshow',loadAccount);
