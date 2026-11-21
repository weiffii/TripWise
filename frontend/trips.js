const accountEmail = document.querySelector('#account-email');
const logoutButton = document.querySelector('#logout-button');
const tripsMessage = document.querySelector('#trips-message');
const savedTrips = document.querySelector('#saved-trips');
let ownerContext = null;
function showError(text) { tripsMessage.textContent=text;tripsMessage.hidden=false; }
function textElement(tag,className,text){const el=document.createElement(tag);el.className=className;el.textContent=text;return el;}
function renderTrips(trips){
  savedTrips.replaceChildren();
  document.querySelector('#empty-trips').hidden=trips.length>0;
  for(const trip of trips){
    const preferences=trip.preferences;
    const card=document.createElement('article');card.className='saved-draft trip-card';card.dataset.tripId=String(trip.id);
    const image=document.createElement('img');image.className='trip-card-image';image.alt='';image.src=window.destinationImagePath(preferences.city);image.loading='lazy';card.append(image);
    card.append(textElement('span','section-number','СОХРАНЁННАЯ АНКЕТА'),textElement('h2','',preferences.city),textElement('span','draft-status','Маршрут пока не построен'));
    const counts=preferences.traveler_counts||{adults:preferences.travelers,children:0,seniors:0};
    const amount=Number(preferences.budget_amount)*(preferences.budget_basis==='per_person'?preferences.travelers:1);
    const budget=new Intl.NumberFormat('ru-RU',{style:'currency',currency:preferences.budget_currency||'RUB',maximumFractionDigits:2}).format(amount);
    const date=value=>value.slice(0,10).split('-').reverse().join('.');
    const dates=`${date(preferences.first_walk_at)} — ${date(preferences.last_walk_end_at)}`;
    card.append(textElement('p','trip-dates',dates),textElement('p','',`Взрослые: ${counts.adults} · Дети: ${counts.children} · Пожилые: ${counts.seniors}`),textElement('p','trip-budget',`Бюджет компании: ${budget}`));
    const actions=document.createElement('div');actions.className='trip-card-actions';
    const edit=textElement('a','outline-button','Редактировать');edit.href=`/trip/new?id=${trip.id}`;
    const remove=textElement('button','delete-trip','Удалить');remove.type='button';remove.setAttribute('aria-label',`Удалить поездку ${preferences.city}, ${dates}`);
    remove.addEventListener('click',async()=>{
      if(!window.confirm(`Удалить поездку «${preferences.city}», ${dates}? Это действие нельзя отменить.`))return;
      remove.disabled=true;
      try{
        const response=await fetch(`/api/trips/${trip.id}`,{method:'DELETE',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TripWise-Draft-Owner':ownerContext},body:JSON.stringify({revision:trip.revision})});
        if(!response.ok){const result=await response.json();showError(typeof result.detail==='string'?result.detail:'Не удалось удалить поездку. Обновите список и повторите попытку.');return;}
        card.remove();if(!savedTrips.children.length)document.querySelector('#empty-trips').hidden=false;
      }catch{showError('Не удалось подтвердить удаление. Обновите страницу, чтобы проверить список.');}
      finally{remove.disabled=false;}
    });
    actions.append(edit,remove);card.append(actions);savedTrips.append(card);
  }
}
async function loadAccount(){
  try{
    const response=await fetch('/api/auth/me',{cache:'no-store',credentials:'same-origin'});
    if(response.status===401){window.location.replace('/');return;}
    if(!response.ok)throw Error('account');
    const result=await response.json();accountEmail.textContent=result.user.email;
    const listResponse=await fetch('/api/trips',{cache:'no-store',credentials:'same-origin'});
    if(!listResponse.ok)throw Error('trips');
    const list=await listResponse.json();if(typeof list.owner_context!=='string'||!list.owner_context)throw Error('owner');
    ownerContext=list.owner_context;renderTrips(list.trips);tripsMessage.hidden=true;
  }catch{showError('Не удалось загрузить поездки. Проверьте соединение и обновите страницу.');}
}
logoutButton.addEventListener('click',async()=>{
  logoutButton.disabled=true;
  try{const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});if(!response.ok)throw Error('logout');window.location.replace('/');}
  catch{showError('Не удалось выйти. Проверьте соединение и повторите попытку.');logoutButton.disabled=false;}
});
window.addEventListener('pageshow',loadAccount);
