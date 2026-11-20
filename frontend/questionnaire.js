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
let revision = 0;
// Удерживаем владельца именно загруженной формы, даже если другая вкладка сменит вход.
let ownerContext = null;
let editVersion = 0;
let savedVersion = 0;
let loaded = false;
let blocked = false;
let pendingSave = null;
let saveTimer;

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
  form.elements.adults.setCustomValidity(Number.isInteger(total) && total >= 1 && total <= 100 ? '' : 'Укажите от 1 до 100 путешественников в сумме.');
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

function isoMoscow(value) {
  return `${value.length === 16 ? `${value}:00` : value}+03:00`;
}

function preferences() {
  return {
    schema_version: 'fullstack-draft-0.1', country_code: 'RU', city: 'Москва', timezone: 'Europe/Moscow',
    first_walk_at: isoMoscow(firstWalk.value), last_walk_end_at: isoMoscow(lastWalk.value),
    travelers: travelerTotal(), traveler_counts: travelerCounts(),
    budget_amount: form.elements.budget_amount.value,
    budget_currency: form.elements.budget_currency.value, budget_basis: selected('budget_basis')[0], budget_scope: 'trip_food_and_entertainment',
    primary_interests: selected('primary_interests'), secondary_interests: selected('secondary_interests'),
    pace: selected('pace')[0], transport_modes: selected('transport_modes'), required_poi_ids: [],
  };
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

function fillForm(data) {
  firstWalk.value = data.first_walk_at.slice(0, 16);
  lastWalk.value = data.last_walk_end_at.slice(0, 16);
  const counts = data.traveler_counts || { adults: data.travelers, children: 0, seniors: 0 };
  for (const key of ['adults', 'children', 'seniors']) form.elements[key].value = counts[key];
  form.elements.budget_currency.value = data.budget_currency || 'RUB';
  form.elements.budget_amount.value = data.budget_amount;
  for (const name of ['budget_basis', 'pace', 'primary_interests', 'secondary_interests', 'transport_modes']) {
    const values = Array.isArray(data[name]) ? data[name] : [data[name]];
    for (const input of form.querySelectorAll(`[name="${name}"]`)) input.checked = values.includes(input.value);
  }
}

async function loadDraft() {
  try {
    const response = await fetch('/api/trip-draft', { cache: 'no-store', credentials: 'same-origin' });
    if (response.status === 401) { window.location.replace('/'); return; }
    if (!response.ok) throw new Error('load');
    const { draft, owner_context } = await response.json();
    if (typeof owner_context !== 'string' || !owner_context.trim()) throw new Error('owner context');
    ownerContext = owner_context;
    if (draft) { fillForm(draft.preferences); revision = draft.revision; }
    fields.disabled = false;
    loaded = true;
    syncCityControls();
    syncInterestChoices();
    updateSummary();
    validate();
    setStatus(draft ? 'Сохранённая анкета загружена' : 'Заполните поля — затем анкета сохранится автоматически', draft ? 'saved' : '');
  } catch {
    setStatus('Не удалось загрузить анкету. Проверьте сервер и обновите страницу.', 'error');
  }
}

// Сохранения идут последовательно: медленный ответ не перезапишет более новую правку.
async function saveLatest(manualRetry = false) {
  if (cityKey() !== 'moscow') return false;
  if (pendingSave) return pendingSave;
  pendingSave = (async () => {
    while (savedVersion < editVersion) {
      const retrySameOwner = manualRetry && ['account_changed', 'reauth_required'].includes(blocked);
      if ((blocked && !retrySameOwner) || !validate()) return false;
      const version = editVersion;
      const body = JSON.stringify({ revision, preferences: preferences() });
      setStatus('Сохраняем анкету…', 'saving');
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch('/api/trip-draft', {
          method: 'PUT', credentials: 'same-origin', headers: {
            'Content-Type': 'application/json',
            'X-TripWise-Draft-Owner': ownerContext,
          },
          body, signal: controller.signal,
        });
        if (response.status === 401) {
          blocked = 'reauth_required';
          clearTimeout(saveTimer);
          setStatus('Сеанс закончился. Войдите в тот же аккаунт в другой вкладке и нажмите «Готово». Поля остаются в этой вкладке.', 'error');
          return false;
        }
        const result = await response.json();
        if (!response.ok) {
          const draftError = response.headers.get('X-TripWise-Draft-Error');
          if (draftError === 'ACCOUNT_CHANGED') blocked = 'account_changed';
          else if (draftError === 'CONTEXT_REQUIRED') blocked = 'context_required';
          else if (response.status === 409) blocked = 'revision_conflict';
          if (blocked) clearTimeout(saveTimer);
          setStatus(typeof result.detail === 'string' ? result.detail : 'Не удалось сохранить анкету.', 'error');
          return false;
        }
        revision = result.draft.revision;
        savedVersion = version;
        blocked = false;
        if (!validate()) {
          setStatus(`Изменения не сохранены: ${validationText()}`, 'error');
          return false;
        }
      } catch {
        setStatus('Не удалось подтвердить сохранение. Проверьте сервер. При конфликте версий обновите страницу.', 'error');
        return false;
      } finally {
        clearTimeout(timeout);
      }
    }
    setStatus('Анкета сохранена в аккаунте', 'saved');
    return true;
  })();
  try { return await pendingSave; } finally { pendingSave = null; }
}

form.addEventListener('input', () => {
  if (!loaded) return;
  if (cityKey() === 'moscow') editVersion += 1;
  syncInterestChoices();
  updateSummary();
  clearTimeout(saveTimer);
  if (blocked) return;
  if (!validate()) {
    setStatus(`Изменения не сохранены: ${validationText()}`);
    return;
  }
  setStatus('Есть изменения — скоро сохраним…');
  if (cityKey() === 'moscow') saveTimer = setTimeout(saveLatest, 800);
  else setStatus('Параметры сохранятся вместе с маршрутом.');
});

const citySelect = form.elements.city;
const cityKey = () => citySelect.value === 'Шанхай' ? 'shanghai' : 'moscow';
const placeSelect = document.querySelector('#route-required');
function syncCityControls() {
  const moscow = cityKey() === 'moscow';
  for (const option of form.elements.budget_currency.options) option.disabled = moscow && option.value !== 'RUB';
  if (moscow) form.elements.budget_currency.value = 'RUB';
  for (const mode of form.querySelectorAll('[name="transport_modes"]')) {
    mode.disabled = moscow && mode.value !== 'walking';
    if (moscow) mode.checked = mode.value === 'walking';
  }
  updateSummary();
}
async function updateCity() {
  const city = cityKey();
  form.elements.country.replaceChildren(new Option(city === 'moscow' ? 'Россия' : 'Китай', city === 'moscow' ? 'RU' : 'CN'));
  form.elements.budget_currency.value = city === 'moscow' ? 'RUB' : 'CNY';
  syncCityControls();
  document.querySelector('#city-note').textContent = city === 'moscow'
    ? 'Московское время. Пилот: три музея, пешком, бюджет в рублях.'
    : 'Время Шанхая. Для расчёта используются учебные цены и оценочные переходы.';
  document.querySelector('#summary-title').textContent = citySelect.value;
  placeSelect.replaceChildren();
  try {
    const response = await fetch('/api/planned-trips/catalog/' + city);
    if (!response.ok) throw Error();
    const places = await response.json();
    if (city !== cityKey()) return;
    for (const place of places) placeSelect.add(new Option(place.name, place.id));
  } catch { setStatus('Не удалось загрузить список мест.'); }
}
citySelect.addEventListener('change', () => void updateCity());
if (new URLSearchParams(location.search).get('city') === 'shanghai') citySelect.value = 'Шанхай';
void updateCity();

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!validate() || !form.reportValidity()) return;
  clearTimeout(saveTimer);
  finishButton.disabled = true;
  try {
    const city = cityKey();
    if (city === 'moscow' && !await saveLatest(true)) return;
    const data = preferences();
    const days = Math.round((Date.parse(lastWalk.value.slice(0, 10)) - Date.parse(firstWalk.value.slice(0, 10))) / 86400000) + 1;
    const modes = data.transport_modes;
    if (city === 'moscow' && (modes.length !== 1 || modes[0] !== 'walking')) throw Error('Для Москвы выберите только пеший режим.');
    if (city === 'shanghai' && modes.length !== 1 && modes.length !== 3) throw Error('Для Шанхая выберите один вид транспорта или все три для смешанного режима.');
    const transport = modes.length === 3 ? 'mixed' : {walking:'walk', public_transit:'transit', car:'taxi'}[modes[0]];
    setStatus('Строим маршрут…');
    const response = await fetch('/api/planned-trips', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        city, timezone:city === 'moscow' ? 'Europe/Moscow' : 'Asia/Shanghai',
        start_date:firstWalk.value.slice(0, 10), days, day_start:firstWalk.value.slice(11, 16),
        day_end:lastWalk.value.slice(11, 16), pace:data.pace, transport,
        group:{adult:data.traveler_counts.adults, child:data.traveler_counts.children, senior:data.traveler_counts.seniors, pet:0},
        primary_interests:data.primary_interests, secondary_interests:data.secondary_interests,
        required_ids:Array.from(placeSelect.selectedOptions, option => option.value),
        budget_amount:Number(data.budget_amount), budget_currency:data.budget_currency,
        budget_basis:data.budget_basis === 'group' ? 'per_group' : 'per_person',
      })});
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || 'Маршрут не построен.');
    savedVersion = editVersion;
    window.location.assign('/trip/result?route=' + encodeURIComponent(result.route_id));
  } catch (error) {
    setStatus(error.message);
  } finally { finishButton.disabled = false; }
});

window.addEventListener('beforeunload', event => {
  if (loaded && editVersion > savedVersion) { event.preventDefault(); event.returnValue = ''; }
});

loadDraft();
