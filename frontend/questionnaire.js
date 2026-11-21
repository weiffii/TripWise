const form = document.querySelector('#trip-form');
const fields = document.querySelector('#trip-fields');
const statusText = document.querySelector('#save-status');
const finishButton = document.querySelector('#finish-questionnaire');
const firstWalk = document.querySelector('#first-walk');
const lastWalk = document.querySelector('#last-walk');
const categories = [
  ['museums', 'Музеи'], ['history', 'История'], ['nature', 'Природа'], ['cafes', 'Кафе'],
  ['entertainment', 'Развлечения'], ['shopping', 'Шопинг'], ['viewpoints', 'Смотровые площадки'], ['unusual', 'Необычные места'],
];
function setStatus(text, state = '') {
  statusText.textContent = text;
  statusText.dataset.state = state;
}

function selected(name) {
  return [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
}

for (const [group, target] of [['primary_interests', '#primary-interests'], ['secondary_interests', '#secondary-interests']]) {
  for (const [value, title] of categories) {
    const label = document.createElement('label');
    label.className = 'choice';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = group;
    input.value = value;
    const text = document.createElement('span');
    text.textContent = title;
    label.append(input, text);
    document.querySelector(target).append(label);
  }
}

function syncInterestChoices() {
  const primary = selected('primary_interests');
  for (const input of form.querySelectorAll('[name="primary_interests"]')) {
    input.disabled = primary.length >= 2 && !input.checked;
  }
  for (const input of form.querySelectorAll('[name="secondary_interests"]')) {
    input.disabled = primary.includes(input.value);
    if (input.disabled) input.checked = false;
  }
}

function travelerCounts() {
  return Object.fromEntries(['adults', 'children', 'seniors'].map(key => [key, form.elements[key].value === '' ? NaN : Number(form.elements[key].value)]));
}

function travelerTotal() {
  return Object.values(travelerCounts()).reduce((sum, value) => sum + value, 0);
}

function validate() {
  const total = travelerTotal();
  form.elements.adults.setCustomValidity(Number.isInteger(total) && total >= 1 ? '' : 'Укажите хотя бы одного путешественника.');
  firstWalk.setCustomValidity('');
  const firstDate = firstWalk.value.slice(0,10);
  const lastDate = lastWalk.value.slice(0,10);
  const days = (Date.parse(lastDate) - Date.parse(firstDate)) / 86400000 + 1;
  const minutes = value => Number(value.slice(11,13))*60 + Number(value.slice(14,16));
  lastWalk.setCustomValidity(!firstWalk.value || !lastWalk.value ? '' : days < 1 || days > 5
    ? 'Выберите от 1 до 5 дней.' : minutes(lastWalk.value) - minutes(firstWalk.value) < 60
      ? 'Ежедневное окно прогулок должно быть не короче часа.' : '');
  const primary = selected('primary_interests');
  form.querySelector('[name="primary_interests"]').setCustomValidity(primary.length < 1 || primary.length > 2 ? 'Выберите один или два главных интереса.' : '');
  form.querySelector('[name="transport_modes"]').setCustomValidity(selected('transport_modes').length ? '' : 'Выберите хотя бы один способ передвижения.');
  return form.checkValidity();
}

function validationText() {
  const invalidInput = form.querySelector('input:invalid, select:invalid');
  return invalidInput?.validationMessage || 'Проверьте заполнение полей.';
}

function updateSummary() {
  const people = travelerTotal();
  const counts = travelerCounts();
  const validPeople = Number.isInteger(people) && people >= 1 && people <= 100 && Object.values(counts).every(value => Number.isInteger(value) && value >= 0);
  document.querySelector('#party-total').textContent = validPeople ? `Всего путешественников: ${people}` : 'Укажите от 1 до 100 путешественников';
  document.querySelector('#summary-people').textContent = validPeople ? `Взрослые: ${counts.adults} · Дети: ${counts.children} · Пожилые: ${counts.seniors} · Всего: ${people}` : 'Укажите количество';
  document.querySelector('#summary-dates').textContent = firstWalk.value && lastWalk.value
    ? `${firstWalk.value.replace('T', ' ')} — ${lastWalk.value.replace('T', ' ')}` : 'Выберите даты и время';
  const amountText = form.elements.budget_amount.value;
  const amount = Number(amountText);
  const total = selected('budget_basis')[0] === 'per_person' ? amount * people : amount;
  document.querySelector('#summary-budget').textContent = amountText !== '' && validPeople && Number.isFinite(total) && total >= 0
    ? new Intl.NumberFormat('ru-RU', { style: 'currency', currency: form.elements.budget_currency.value, maximumFractionDigits: 2 }).format(total) : 'Укажите сумму';
  const primary = selected('primary_interests');
  document.querySelector('#summary-interests').textContent = categories.filter(([key]) => primary.includes(key)).map(([, title]) => title).join(', ') || 'Выберите 1–2 интереса';
}

const placeSelect = document.querySelector('#route-required');
const citySelect = form.elements.city;
const isMoscow = () => citySelect.value === 'Москва';
let catalogRevision = 0;
let catalogPlaces = [];
const poiFilter = document.querySelector('#poi-filter');
function renderPlaces() {
  const picked = new Set(Array.from(placeSelect.selectedOptions, option => option.value));
  const query = poiFilter.value.trim().toLocaleLowerCase('ru');
  const matches = catalogPlaces.filter(place => !picked.has(place.id) && place.name.toLocaleLowerCase('ru').includes(query)).slice(0,100);
  placeSelect.replaceChildren();
  for (const place of [...catalogPlaces.filter(item => picked.has(item.id)), ...matches]) {
    placeSelect.add(new Option(place.name, place.id, false, picked.has(place.id)));
  }
}
poiFilter.addEventListener('input', renderPlaces);
async function loadCatalog() {
  const revision = ++catalogRevision;
  const moscow = isMoscow();
  catalogPlaces = [];
  poiFilter.value = '';
  document.querySelector('#moscow-duration').hidden = !moscow;
  placeSelect.replaceChildren();
  placeSelect.disabled = true;
  form.elements.country.replaceChildren(new Option(moscow ? 'Россия' : 'Китай', moscow ? 'RU' : 'CN'));
  document.querySelector('#summary-title').textContent = citySelect.value;
  document.querySelector('#city-note').textContent = moscow
    ? 'Москва, UTC+3. Для маршрута используются реальные места V7 и ориентировочные часы и цены.'
    : 'Шанхай, UTC+8. Цены и часть данных учебные.';
  form.elements.budget_currency.value = moscow ? 'RUB' : 'CNY';
  for (const option of form.elements.budget_currency.options) option.disabled = moscow && option.value !== 'RUB';
  updateSummary();
  try {
    const response = await fetch('/api/planned-trips/catalog/' + (moscow ? 'moscow' : 'shanghai'));
    if (response.status === 401) { location.replace('/'); return; }
    if (!response.ok) throw Error();
    const places = await response.json();
    if (revision !== catalogRevision) return;
    catalogPlaces = places;
    renderPlaces();
    placeSelect.disabled = false;
    fields.disabled = false;
    updateSummary();
    setStatus('Заполните поля и постройте маршрут.');
  } catch {
    if (revision === catalogRevision) setStatus('Не удалось загрузить каталог выбранного города. Обновите страницу.');
  }
}
citySelect.addEventListener('change', () => void loadCatalog());
form.addEventListener('input', () => { syncInterestChoices(); updateSummary(); validate(); });
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!validate() || !form.reportValidity()) return;
  finishButton.disabled = true;
  try {
    const days = (Date.parse(lastWalk.value.slice(0,10)) - Date.parse(firstWalk.value.slice(0,10))) / 86400000 + 1;
    const transportModes = selected('transport_modes').map(mode => ({walking:'walk', public_transit:'transit', car:'taxi'})[mode]);
    const counts = travelerCounts();
    const transport = transportModes.length > 1 ? 'mixed' : transportModes[0];
    setStatus('Строим маршрут…');
    const response = await fetch('/api/planned-trips', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        city:isMoscow() ? 'moscow' : 'shanghai', timezone:isMoscow() ? 'Europe/Moscow' : 'Asia/Shanghai',
        ...(isMoscow() && document.querySelector('#visit-minutes').value ? {visit_minutes:Number(document.querySelector('#visit-minutes').value)} : {}),
        start_date:firstWalk.value.slice(0, 10), days, day_start:firstWalk.value.slice(11, 16),
        day_end:lastWalk.value.slice(11, 16), pace:selected('pace')[0], transport, transport_modes:transportModes,
        group:{adult:counts.adults, child:counts.children, senior:counts.seniors, pet:0},
        primary_interests:selected('primary_interests'), secondary_interests:selected('secondary_interests'),
        required_ids:Array.from(placeSelect.selectedOptions, option => option.value),
        budget_amount:Number(form.elements.budget_amount.value), budget_currency:form.elements.budget_currency.value,
        budget_basis:selected('budget_basis')[0] === 'group' ? 'per_group' : 'per_person',
      })});
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || 'Маршрут не построен.');
    window.location.assign('/trip/result?route=' + encodeURIComponent(result.route_id));
  } catch (error) {
    setStatus(error.message);
  } finally { finishButton.disabled = false; }
});

loadCatalog();
