const element = id => document.getElementById(id);
const routeId = new URLSearchParams(location.search).get('route');
const endpoint = '/api/planned-trips/' + encodeURIComponent(routeId);
const places = new Map();
let trip, registration, csrf, proposal;

async function request(path, body) {
  const response = await fetch(path, body ? {
    method: 'POST', headers: {'Content-Type':'application/json', 'X-TripWise-CSRF':csrf || ''},
    body: JSON.stringify({...body, request_id:crypto.randomUUID()}),
  } : undefined);
  if (response.status === 401) location.assign('/');
  const result = await response.json();
  if (!response.ok || ['error','unavailable'].includes(result.type)) throw Error(result.detail || result.message || 'Запрос не выполнен.');
  return result;
}

function events(day) {
  if (day.events) return day.events;
  return [...day.visits.map(item => ({...item, type:'visit'})),
    ...day.legs.map(item => ({...item, type:'travel'})),
    ...day.breaks.map(item => ({...item, type:item.kind}))].sort((left, right) => left.start_at.localeCompare(right.start_at));
}

function clock(row, key) {
  if (row[key + '_at']) return row[key + '_at'].slice(11,16);
  const value = row[key + '_min'];
  return Number.isFinite(value) ? String(Math.floor(value / 60)).padStart(2,'0') + ':' + String(value % 60).padStart(2,'0') : '';
}

function eventLabel(row) {
  const name = places.get(row.poi_id)?.name;
  const kind = {visit:'Посещение', meal:'Питание', travel:'Переход', rest:'Отдых', wait:'Ожидание'};
  const mode = {walk:'Пешком',transit:'Общественный транспорт',taxi:'Такси'}[row.mode];
  const minutes = row.planned_minutes ?? (row.duration_seconds == null ? null : Math.ceil(row.duration_seconds / 60));
  const cost = row.price_cny ?? row.cost_cny ?? row.transport_cost_cny;
  return [clock(row,'start') + '–' + clock(row,'end'), row.type === 'travel' ? mode || 'Пеший переход' : name || kind[row.type] || 'Перерыв',
    minutes == null ? '' : minutes + ' мин', cost == null ? '' : cost + ' CNY'].filter(Boolean).join(' · ');
}

function appendText(parent, text) {
  const item = document.createElement('li');
  item.textContent = text;
  parent.append(item);
}

const routeMap = window.TripWiseMap.create({
  container:element('street-map'), svg:element('route-map'), tiles:element('map-tiles'),
  background:element('map-background'), directTiles:true,
  onSelect:html => { element('route-directions').innerHTML = html; },
  onNetworkStatus:text => { element('road-status').textContent = text; },
  onStatus:text => { element('map-status').textContent = text; },
});

function drawMap(rows) {
  element('road-status').textContent = '';
  const stops = rows.filter(row => ['visit','meal'].includes(row.type));
  const mapPlaces = new Map([...places].filter(([,place]) => Number.isFinite(place.lat) && Number.isFinite(place.lon)));
  const mappedRows = rows.map(row => {
    if (row.type !== 'travel' || trip.city !== 'moscow') return row;
    return {...row, mode:'walk', planned_minutes:Math.ceil(row.duration_seconds / 60),
      ...(row.geometry?.type === 'LineString' ? {geometry_segments:[
        {mode:'walk', coordinates:row.geometry.coordinates, verified:true},
      ]} : {})};
  });
  routeMap.setDay({events:mappedRows}, mapPlaces);
  const visibleStops = stops.filter(row => mapPlaces.has(row.poi_id));
  for (const button of element('route-stops').querySelectorAll('button[data-poi]')) {
    button.onclick = () => routeMap.selectStop(visibleStops.findIndex(row => row.poi_id === button.dataset.poi));
  }
}

function renderDay() {
  const day = trip.plan.days[Number(element('route-day').value)];
  const rows = day ? events(day) : [];
  const stops = element('route-stops');
  stops.replaceChildren();
  let legIndex = 0;
  for (const [rowIndex,row] of rows.entries()) {
    const previous = rows[rowIndex-1];
    const wait = row.wait_before_min ?? (previous && Number.isFinite(row.start_min) ? row.start_min - previous.end_min : 0);
    if (wait > 0) appendText(stops, 'Ожидание · ' + wait + ' мин' + (row.waiting_reason ? ' · ' + row.waiting_reason : ''));
    const item = document.createElement('li');
    item.className = 'route-event route-event-' + row.type;
    const time = document.createElement('span');
    time.className = 'route-event-time';
    time.textContent = clock(row,'start') + '–' + clock(row,'end');
    const card = document.createElement('div');
    const place = places.get(row.poi_id);
    const canMap = place && Number.isFinite(place.lat) && Number.isFinite(place.lon);
    const title = document.createElement(canMap || row.type === 'travel' ? 'button' : 'strong');
    title.textContent = eventLabel(row).split(' · ').slice(1).join(' · ');
    if (canMap) { title.type = 'button'; title.dataset.poi = row.poi_id; }
    if (row.type === 'travel') {
      const index = legIndex++;
      title.type = 'button';
      title.onclick = () => {
        routeMap.selectLeg(index);
        element('street-map').scrollIntoView({block:'nearest'});
      };
    }
    card.append(title);
    item.append(time,card);
    stops.append(item);
  }
  for (const tab of element('route-tabs').children) tab.setAttribute('aria-pressed', String(tab.dataset.day === element('route-day').value));
  if (!rows.length) appendText(stops,'В выбранный день не удалось запланировать посещения.');
  drawMap(rows);
}

function renderTrip() {
  routeMap.beginPlan();
  const moscow = trip.city === 'moscow';
  element('route-title').textContent = moscow ? 'Москва' : 'Шанхай';
  const budget = trip.plan.budget_summary;
  const total = moscow ? budget?.known_total : trip.plan.summary?.total_cost_cny;
  const currency = moscow ? 'RUB' : 'CNY';
  element('route-meta').textContent = 'Версия ' + trip.version + ' · ' + trip.plan.days.length + ' дн.';
  element('route-cost').textContent = total == null ? 'Оценка недоступна' :
    (moscow && budget.status !== 'passed' ? 'От ' : '≈ ') + new Intl.NumberFormat('ru-RU',{style:'currency',currency}).format(Number(total));
  element('cost-note').textContent = moscow ? 'Известные расходы на посещения для всей группы. Питание и неподтверждённые расходы не включены.' :
    'Учебная оценка на всю поездку: посещения, питание и переходы.';
  const select = element('route-day');
  const selected = select.value || '0';
  select.replaceChildren();
  const tabs = element('route-tabs');
  tabs.replaceChildren();
  trip.plan.days.forEach((day,index) => {
    const label = 'День ' + (index+1) + ' · ' + new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',weekday:'short',timeZone:'UTC'}).format(new Date(day.date));
    select.add(new Option(label,index));
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.dataset.day = index;
    tab.textContent = label;
    tab.onclick = () => { select.value = index; renderDay(); };
    tabs.append(tab);
  });
  select.value = Number(selected) < trip.plan.days.length ? selected : '0';
  element('route-warnings').textContent = moscow
    ? 'Ограниченный пеший пилот по трём музеям. Уточните часы работы и актуальную стоимость перед поездкой.'
    : ['Учебные цены и условия посещения.', ...(trip.plan.warnings || [])].join(' ');
  renderDay();
}

const chatForm = element('edit-route');
const chatPath = endpoint + '/assistant/';
const chatStatus = element('chat-status');
function chatLine(author, message) {
  const line = document.createElement('p');
  line.textContent = author + ': ' + message;
  element('chat-log').append(line);
  if (element('chat-log').children.length > 16) element('chat-log').firstElementChild.remove();
}
const control = busy => {
  for (const button of [chatForm.querySelector('button'),element('confirm-edit'),element('reject-edit')]) button.disabled = busy;
};

async function load() {
  trip = await request(endpoint);
  const catalog = await request('/api/planned-trips/catalog/' + trip.city);
  for (const place of catalog) places.set(place.id,place);
  renderTrip();
  const status = await request(chatPath + 'status');
  chatStatus.textContent = status.reason || '';
  if (!status.available) return;
  csrf = status.csrf_token;
  registration = await request(chatPath + 'register',{});
  control(false);
}

element('route-day').addEventListener('change',renderDay);
chatForm.addEventListener('submit',async event => {
  event.preventDefault();
  const input = element('chat-text');
  if (!registration || !input.value.trim()) return;
  control(true);
  element('chat-preview').hidden = true;
  chatStatus.textContent = 'Готовим предложение…';
  try {
    chatLine('Вы', input.value.trim());
    const result = await request(chatPath + 'message', {route_id:registration.route_id,base_version:registration.version,
      poi_snapshot_id:registration.poi_snapshot_id,text:input.value.trim()});
    input.value = '';
    if (result.message) chatLine('TripWise', result.message);
    chatStatus.textContent = result.message || '';
    if (result.type !== 'proposal') return;
    proposal = result;
    const preview = element('preview-stops');
    preview.replaceChildren();
    for (const day of result.plan.days) for (const row of events(day)) appendText(preview, day.date + ' · ' + eventLabel(row));
    element('chat-preview').hidden = false;
  } catch (error) { chatStatus.textContent = error.message; }
  finally { control(false); }
});

async function decide(decision) {
  if (!proposal) return;
  control(true);
  try {
    const result = await request(chatPath + 'decide', {proposal_id:proposal.proposal_id,
      base_version:proposal.base_version,decision});
    if (result.type === 'confirmed') {
      registration.version = result.version;
      trip = await request(endpoint);
      renderTrip();
    }
    chatStatus.textContent = result.message;
    proposal = null;
    element('chat-preview').hidden = true;
  } catch (error) { chatStatus.textContent = error.message; }
  finally { control(false); }
}
element('confirm-edit').addEventListener('click',() => void decide('confirm'));
element('reject-edit').addEventListener('click',() => void decide('reject'));
load().catch(error => { element('route-meta').textContent = error.message; });
