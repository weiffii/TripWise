// Анкета создаёт временный маршрут. В аккаунт он попадёт только после отдельного сохранения результата.
const form = document.querySelector('#trip-form');
const fields = document.querySelector('#trip-fields');
const statusText = document.querySelector('#save-status');
const finishButton = document.querySelector('#finish-questionnaire');
const firstWalk = document.querySelector('#first-walk');
const lastWalk = document.querySelector('#last-walk');
const countryInput = document.querySelector('#country');
const cityInput = document.querySelector('#city');
const searchInput = document.querySelector('#required-search');
const categories = [['museums','Музеи'],['history','История'],['nature','Природа'],['cafes','Кафе'],['entertainment','Развлечения'],['shopping','Шопинг'],['viewpoints','Смотровые площадки'],['unusual','Необычные места']];
let catalog = null;
const editId = new URLSearchParams(window.location.search).get('id');
let tripId = editId === null ? null : (/^[1-9]\d*$/.test(editId) && Number.isSafeInteger(Number(editId)) ? Number(editId) : NaN);
const sourcePreviewId = new URLSearchParams(window.location.search).get('preview');
let catalogLoad = 0;
let ownerContext = null;
let editVersion = 0;
let savedVersion = 0;
let loaded = false;
let blocked = false;
let saving = false;
let lastCountry = "";
let lastCity = "";
const requiredPlaces = new Set();
function setStatus(text,state='') {statusText.textContent=text;statusText.dataset.state=state;}
function selected(name) {return [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input=>input.value);}
function currentCity() {return catalog?.countries.find(country=>country.code===countryInput.value)?.cities.find(city=>city.name===cityInput.value);}
function option(value,label,disabled=false) {const result=document.createElement('option');result.value=value;result.textContent=label;result.disabled=disabled;return result;}
const availableCity = city => ['Москва','Шанхай'].includes(city.name);
function orderedCountries() { return [...catalog.countries].sort((a,b)=>(['RU','CN'].includes(a.code)?['RU','CN'].indexOf(a.code):99)-(['RU','CN'].includes(b.code)?['RU','CN'].indexOf(b.code):99)); }
function updateCurrencyChoices() { const moscow=cityInput.value==='Москва'; const allowed=moscow?['RUB']:['RUB','USD','EUR','CNY']; for(const item of form.elements.budget_currency.options)item.disabled=!allowed.includes(item.value); document.querySelector('#currency-note').textContent=moscow?'Для маршрутов Москвы бюджет указывается в рублях.':'Для Шанхая доступны CNY, RUB, USD и EUR. Пересчёт маршрутизатора — планировочная оценка, а не текущий банковский курс.'; }
function populateCities(desired) {
  const cities=catalog.countries.find(country=>country.code===countryInput.value)?.cities||[];
  cityInput.replaceChildren(option('','Выберите город'),...cities.map(city=>option(city.name,city.name+(availableCity(city)?'':' · В разработке'),!availableCity(city))));
  if(cities.some(city=>city.name===desired)) cityInput.value=desired;
  requiredPlaces.clear();searchInput.value='';renderPlaces();updateSummary();updateCurrencyChoices();void loadCityPlaces();
}
function renderPlaces() {
  const city=currentCity();
  document.querySelector('#timezone-note').textContent=city?`Местное время города: ${city.timezone}.`:'Выберите город — время указывается по местному часовому поясу.';
  const pois=city?.pois||[];
  const query=searchInput.value.trim().toLocaleLowerCase('ru');
  const matches=pois.filter(poi=>poi.name.toLocaleLowerCase('ru').includes(query));
  const visible=matches.slice(0,60);
  const list=document.querySelector('#poi-options');list.replaceChildren();
  for(const poi of visible) {
    const label=document.createElement('label');label.className='choice poi-choice';
    const input=document.createElement('input');input.type='checkbox';input.name='required_poi_ids';input.value=poi.id;input.checked=requiredPlaces.has(poi.id);
    const name=document.createElement('span');name.textContent=poi.name;
    input.addEventListener('change',()=>{if(input.checked) requiredPlaces.add(poi.id);else requiredPlaces.delete(poi.id);renderPlaces();});
    label.append(input,name);list.append(label);
  }
  const chips=document.querySelector('#selected-places');chips.replaceChildren();
  for(const id of requiredPlaces) {
    const poi=pois.find(item=>item.id===id)||{name:'Место прежнего каталога'};
    const chip=document.createElement('button');chip.type='button';chip.className='place-chip';chip.textContent=`${poi.name} ×`;chip.setAttribute('aria-label',`Убрать ${poi.name}`);
    chip.addEventListener('click',()=>{requiredPlaces.delete(id);renderPlaces();markEdited();});chips.append(chip);
  }
  document.querySelector('#poi-count').textContent=matches.length>60?`Показаны первые 60 из ${matches.length}. Уточните название для поиска.`:pois.length?`В каталоге: ${pois.length}. Можно выбрать несколько мест.`:'';
  document.querySelector('#poi-empty').hidden=visible.length>0;
  document.querySelector('#poi-empty').textContent=!city?'Сначала выберите город.':pois.length?'Места не найдены. Попробуйте другое название.':'Для этого города подборка обязательных мест пока не подготовлена.';
}
async function loadCityPlaces() {
  const city=currentCity();const sequence=++catalogLoad;if(!city||!availableCity(city))return;
  const key=city.name==='Москва'?'moscow':'shanghai';
  const control=document.querySelector('#places-load-status');control.textContent='Загружаем каталог мест…';
  searchInput.disabled=true;
  try{const response=await fetch('/api/planned-trips/catalog/'+key,{cache:'no-store',credentials:'same-origin'});if(!response.ok)throw Error();const pois=await response.json();if(sequence!==catalogLoad)return;city.pois=pois.map(poi=>({id:poi.id,name:poi.name}));const known=new Set(city.pois.map(poi=>poi.id));const missing=[...requiredPlaces].filter(id=>!known.has(id));if(missing.length){control.textContent='Некоторые места прежней анкеты отсутствуют в текущем каталоге. Выберите их заново.';}else control.textContent='';renderPlaces();}catch{if(sequence===catalogLoad)control.textContent='Не удалось загрузить места. Обновите страницу перед созданием маршрута.';}finally{if(sequence===catalogLoad)searchInput.disabled=false;}
}
for(const [group,target] of [['primary_interests','#primary-interests'],['secondary_interests','#secondary-interests']]) {
  for(const [value,title]of categories){const label=document.createElement('label');label.className='choice';const input=document.createElement('input');input.type='checkbox';input.name=group;input.value=value;const span=document.createElement('span');span.textContent=title;if(group==='primary_interests'){const image=document.createElement('img');image.src=`/assets/card-${value}.svg`;image.alt='';image.loading='lazy';image.className='interest-image';label.append(image);label.classList.add('interest-card');}label.append(input,span);document.querySelector(target).append(label);}
}
function syncInterestChoices() {
  const primary=selected('primary_interests');
  for(const input of form.querySelectorAll('[name="primary_interests"]'))input.disabled=false;
  for(const input of form.querySelectorAll('[name="secondary_interests"]')){input.disabled=primary.includes(input.value);if(input.disabled)input.checked=false;}
}
function travelerCounts(){return Object.fromEntries(['adults','children','seniors'].map(key=>[key,form.elements[key].value===''?NaN:Number(form.elements[key].value)]));}
function travelerTotal(){return Object.values(travelerCounts()).reduce((sum,value)=>sum+value,0);}
const maxTripDays = 14;
const dayMilliseconds = 86400000;
const middleDays = document.querySelector('#middle-days');
const middleDayFields = document.querySelector('#middle-day-fields');
const dayLabel = new Intl.DateTimeFormat('ru-RU', {day:'numeric', month:'long', timeZone:'UTC'});
function tripDays() {
  if (!firstWalk.value || !lastWalk.value) return 0;
  return (Date.parse(lastWalk.value.slice(0,10)) - Date.parse(firstWalk.value.slice(0,10))) / dayMilliseconds + 1;
}
function renderMiddleDays(settings = middleDaySettings()) {
  const days = tripDays();
  middleDays.hidden = days < 3 || days > maxTripDays;
  middleDayFields.replaceChildren();
  if (middleDays.hidden) return;
  const firstDate = Date.parse(firstWalk.value.slice(0,10));
  for (let i = 1; i < days - 1; i++) {
    const date = new Date(firstDate + i * dayMilliseconds);
    const key = date.toISOString().slice(0,10);
    const group = document.createElement('fieldset');
    group.className = 'form-grid';
    group.dataset.date = key;
    const legend = document.createElement('legend');
    legend.textContent = dayLabel.format(date);
    group.append(legend);
    for (const [name,title] of [['day_start','Начало'], ['day_end','Окончание']]) {
      const label = document.createElement('label');
      label.className = 'field';
      const input = document.createElement('input');
      input.type = 'time';
      input.name = name;
      input.value = settings[key]?.[name] || '';
      label.append(title,input);
      group.append(label);
    }
    middleDayFields.append(group);
  }
}
function middleDaySettings() {
  const result = {};
  for (const group of middleDayFields.children) {
    const settings = Object.fromEntries([...group.querySelectorAll('input')].filter(input => input.value)
      .map(input => [input.name,input.value]));
    if (Object.keys(settings).length) result[group.dataset.date] = settings;
  }
  return result;
}
function durationMinutes(start,end) {
  const minutes = value => Number(value.slice(0,2)) * 60 + Number(value.slice(3,5));
  return minutes(end) - minutes(start);
}
function validate() {
  const allowedCurrency=cityInput.value==='Москва'?['RUB']:['RUB','USD','EUR','CNY'];form.elements.budget_currency.setCustomValidity(allowedCurrency.includes(form.elements.budget_currency.value)?'':'Эта валюта пока не поддерживается для выбранного города. Выберите доступную валюту и укажите бюджет в ней. Сумма не пересчитывается автоматически.');
  const total=travelerTotal();form.elements.adults.setCustomValidity(Number.isInteger(total)&&total>=1&&total<=100?'':'Укажите от 1 до 100 путешественников в сумме.');
  const days = tripDays();
  firstWalk.setCustomValidity('');
  lastWalk.setCustomValidity('');
  if (days > maxTripDays) lastWalk.setCustomValidity('Выберите поездку длительностью до 14 дней.');
  else if (firstWalk.value && lastWalk.value && firstWalk.value >= lastWalk.value) {
    lastWalk.setCustomValidity('Окончание последней прогулки должно быть позже начала первой.');
  } else if (days === 1 && durationMinutes(firstWalk.value.slice(11,16),lastWalk.value.slice(11,16)) < 60) {
    lastWalk.setCustomValidity('Оставьте хотя бы час на прогулку.');
  } else if (days > 1) {
    if (durationMinutes(firstWalk.value.slice(11,16),'21:00') < 60) firstWalk.setCustomValidity('В первый день оставьте хотя бы час до 21:00.');
    if (durationMinutes('09:00',lastWalk.value.slice(11,16)) < 60) lastWalk.setCustomValidity('В последний день оставьте хотя бы час после 09:00.');
  }
  form.querySelector('[name="primary_interests"]').setCustomValidity(selected('primary_interests').length?'':'Выберите хотя бы один главный интерес.');
  form.querySelector('[name="transport_modes"]').setCustomValidity(selected('transport_modes').length?'':'Выберите хотя бы один способ передвижения.');
  for (const group of middleDayFields.children) {
    const [start,end] = group.querySelectorAll('input');
    const invalid = durationMinutes(start.value || '09:00',end.value || '21:00') < 60;
    const input = end.value ? end : start;
    start.setCustomValidity('');
    end.setCustomValidity('');
    input.setCustomValidity(invalid ? 'Оставьте хотя бы час на прогулку.' : '');
    if (invalid) middleDays.open = true;
  }
  return form.checkValidity();
}
function preferences(){
  const daySettings = middleDaySettings();
  return {
  schema_version:'fullstack-draft-0.2',country_code:countryInput.value,city:cityInput.value,timezone:currentCity().timezone,
  first_walk_at:firstWalk.value,last_walk_end_at:lastWalk.value,travelers:travelerTotal(),traveler_counts:travelerCounts(),
  ...(Object.keys(daySettings).length ? {day_settings:daySettings} : {}),
  ...(cityInput.value === 'Москва' ? {route_priority:form.elements.route_priority.value} : {}),
  budget_amount:form.elements.budget_amount.value,budget_currency:form.elements.budget_currency.value,budget_basis:selected('budget_basis')[0],budget_scope:'trip_food_visits_and_transport',
  primary_interests:selected('primary_interests'),secondary_interests:selected('secondary_interests'),pace:selected('pace')[0],transport_modes:selected('transport_modes'),required_poi_ids:[...requiredPlaces],
};}
function updateSummary(){
  window.setDestinationVisual(cityInput.value);
  document.querySelector('#route-priority-field').hidden = cityInput.value !== 'Москва';
  document.querySelector('#summary-title').textContent=cityInput.value||'Ваша поездка';
  const counts=travelerCounts();const total=travelerTotal();const valid=Number.isInteger(total)&&total>=1&&total<=100&&Object.values(counts).every(n=>Number.isInteger(n)&&n>=0);
  document.querySelector('#party-total').textContent=valid?`Всего путешественников: ${total}`:'Укажите от 1 до 100 путешественников';
  document.querySelector('#summary-people').textContent=valid?`Взрослые: ${counts.adults} · Дети: ${counts.children} · Пожилые: ${counts.seniors}`:'Укажите количество';
  document.querySelector('#summary-dates').textContent=firstWalk.value&&lastWalk.value?`${firstWalk.value.replace('T',' ')} — ${lastWalk.value.replace('T',' ')}`:'Выберите даты и время';
  const amount=form.elements.budget_amount.value;const sum=Number(amount)*(selected('budget_basis')[0]==='per_person'?total:1);
  document.querySelector('#summary-budget').textContent=amount!==''&&valid&&Number.isFinite(sum)&&sum>=0?new Intl.NumberFormat('ru-RU',{style:'currency',currency:form.elements.budget_currency.value||'RUB',maximumFractionDigits:2}).format(sum):'Укажите сумму';
  document.querySelector('#summary-interests').textContent=categories.filter(([id])=>selected('primary_interests').includes(id)).map(([,title])=>title).join(', ')||'Выберите хотя бы один интерес';
}
function markEdited(){
  if(!loaded)return;editVersion+=1;syncInterestChoices();updateSummary();validate();
  if(!blocked)setStatus(saving?'Есть новые правки. Сохраните их отдельным нажатием после завершения запроса.':'Изменения не сохранены. Нажмите «Создать маршрут». ');
}
function fillForm(data){
  countryInput.value=data.country_code;populateCities(data.city);
  form.elements.route_priority.value = data.route_priority || 'balanced';
  firstWalk.value=data.first_walk_at.slice(0,16);lastWalk.value=data.last_walk_end_at.slice(0,16);
  renderMiddleDays(data.day_settings || {});
  middleDays.open = Object.keys(data.day_settings || {}).length > 0;
  const counts=data.traveler_counts||{adults:data.travelers,children:0,seniors:0};for(const key of ['adults','children','seniors'])form.elements[key].value=counts[key];
  form.elements.budget_amount.value=data.budget_amount;form.elements.budget_currency.value=data.budget_currency||'RUB';
  for(const name of ['budget_basis','pace','primary_interests','secondary_interests','transport_modes']){const values=Array.isArray(data[name])?data[name]:[data[name]];for(const input of form.querySelectorAll(`[name="${name}"]`))input.checked=values.includes(input.value);}
  for(const id of data.required_poi_ids||[])requiredPlaces.add(id);renderPlaces();
}
async function loadDraft(){
  try{
    if(Number.isNaN(tripId))throw Error('invalid trip id');
    const recordPath=sourcePreviewId?`/api/route-previews/${encodeURIComponent(sourcePreviewId)}`:tripId===null?'/api/trips':`/api/trips/${tripId}`;
    const [catalogResponse,response]=await Promise.all([fetch('/api/catalog',{cache:'no-store'}),fetch(recordPath,{cache:'no-store',credentials:'same-origin'})]);
    if(response.status===401){window.location.replace('/');return;}
    if(!catalogResponse.ok||!response.ok)throw Error('load');
    catalog=await catalogResponse.json();
    countryInput.replaceChildren(option('','Выберите страну'),...orderedCountries().map(country=>option(country.code,country.name+(['RU','CN'].includes(country.code)?'':' · В разработке'),!['RU','CN'].includes(country.code))));
    const currency=form.elements.budget_currency;const main=document.createElement('optgroup');main.label='Основные валюты';const other=document.createElement('optgroup');other.label='Другие валюты';
    let names=null;try{names=new Intl.DisplayNames(['ru'],{type:'currency'});}catch{}
    catalog.currencies.forEach((item,index)=>(index<4?main:other).append(option(item.code,`${item.code} · ${names?.of(item.code)||item.name}`)));currency.replaceChildren(main,other);
    const record=await response.json();const {trip,owner_context}=record;if(typeof owner_context!=='string'||!owner_context)throw Error('owner');ownerContext=owner_context;
    if(sourcePreviewId){if(!record.form_preferences)throw Error('missing preview form');fillForm(record.form_preferences);}else if(tripId!==null){if(!trip)throw Error('missing trip');fillForm(trip.preferences);}
    updateCurrencyChoices();if(currentCity())await loadCityPlaces();
    fields.disabled=false;loaded=true;lastCountry=countryInput.value;lastCity=cityInput.value;syncInterestChoices();renderPlaces();updateSummary();validate();
    setStatus(sourcePreviewId?'Параметры маршрута загружены. Измените их и создайте новый вариант.':tripId!==null?'Сохранённая анкета загружена. Создайте маршрут по её параметрам.':'Новая поездка. Заполните анкету и нажмите «Создать маршрут».');
  }catch{setStatus('Не удалось загрузить анкету и направления. Проверьте соединение и обновите страницу.','error');}
}
countryInput.addEventListener('change',()=>{
  if(requiredPlaces.size && !window.confirm('При смене страны выбранные обязательные места будут убраны. Продолжить?')) { countryInput.value=lastCountry; return; }
  populateCities();lastCountry=countryInput.value;lastCity=cityInput.value;markEdited();
});
cityInput.addEventListener('change',()=>{
  if(requiredPlaces.size && !window.confirm('При смене города выбранные обязательные места будут убраны. Продолжить?')) {cityInput.value=lastCity;return;}
  requiredPlaces.clear();searchInput.value='';lastCity=cityInput.value;renderPlaces();updateCurrencyChoices();void loadCityPlaces();markEdited();
});
searchInput.addEventListener('input',()=>renderPlaces());
form.addEventListener('input',event=>{if([searchInput,countryInput,cityInput].includes(event.target))return;if([firstWalk,lastWalk].includes(event.target))renderMiddleDays();markEdited();});
// checkbox input происходит до change: выбранные POI обновляем в change, не отправляя запрос.
form.addEventListener('change',event=>{if(event.target.name==='required_poi_ids'){updateSummary();}});
form.addEventListener('submit',async event=>{
  event.preventDefault();if(saving||!loaded||!validate()||!form.reportValidity())return;
  if(!currentCity()||!availableCity(currentCity())){setStatus('Это направление пока в разработке. Выберите Москву или Шанхай.','error');return;}
  if(blocked&&!['account_changed','reauth_required'].includes(blocked))return;
  const version=editVersion;const snapshot=preferences();saving=true;finishButton.disabled=true;
  setStatus('Создаём маршрут. Подбор мест может занять некоторое время…','saving');
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),180000);
  try{
    const response=await fetch('/api/route-previews',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TripWise-Draft-Owner':ownerContext},body:JSON.stringify({form_preferences:snapshot,...(sourcePreviewId?{source_preview_id:sourcePreviewId}:{})}),signal:controller.signal});
    if(response.status===401){blocked='reauth_required';setStatus('Войдите в прежний аккаунт в другой вкладке и повторите создание маршрута.','error');return;}
    const result=await response.json();
    if(!response.ok){const reason=response.headers.get('X-TripWise-Draft-Error');blocked=reason==='ACCOUNT_CHANGED'?'account_changed':reason==='CONTEXT_REQUIRED'?'context_required':false;setStatus(typeof result.detail==='string'?result.detail:'Не удалось создать маршрут. Проверьте анкету и повторите.','error');return;}
    if(!result.preview_id)throw Error('preview');
    if(editVersion!==version){setStatus('Маршрут создан по прежним параметрам. Новые правки остались в анкете. Нажмите «Создать маршрут» ещё раз, чтобы учесть их.');return;}
    savedVersion=version;blocked=false;window.location.assign('/trip/result?preview='+encodeURIComponent(result.preview_id));
  }catch{setStatus('Не удалось получить маршрут. Поля сохранены в этой вкладке. Повторите создание; в «Мои поездки» ничего не добавлено.','error');}
  finally{clearTimeout(timeout);saving=false;finishButton.disabled=false;}
});
window.addEventListener('beforeunload',event=>{if(loaded&&editVersion>savedVersion){event.preventDefault();event.returnValue='';}});
loadDraft();
