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

function events(day, plan = trip.plan) {
  if (day.events) return day.events.filter(showScheduleEvent);
  const meals = new Map(Object.entries(plan.moscow_meals?.[day.date] || {}).map(([kind, slot]) => [slot.poi_id, kind]));
  const diagnostics = new Map((plan.gap_diagnostics?.remaining || []).filter(gap => gap.date === day.date)
    .map(gap => [gap.start_at + '/' + gap.end_at, gap.reason]));
  return [...day.visits.map(item => ({...item, type:meals.has(item.poi_id) ? 'meal' : 'visit', meal_kind:meals.get(item.poi_id)})),
    ...day.legs.map(item => ({...item, type:'travel', mode:item.mode || (item.transport_steps?.length ? 'transit' :
      (trip.options.transport === 'taxi' ? 'taxi' : 'walk'))})),
    ...day.breaks.map(item => ({...item, type:item.kind,
      reason:diagnostics.get(item.start_at + '/' + item.end_at) || item.reason}))].filter(showScheduleEvent)
    .sort((left, right) => left.start_at.localeCompare(right.start_at));
}

function showScheduleEvent(row) {
  if (!['wait', 'waiting'].includes(row.type)) return true;
  const minutes = row.start_at
    ? (Date.parse(row.end_at) - Date.parse(row.start_at)) / 60000
    : row.end_min - row.start_min;
  return minutes > 60;
}

function clock(row, key) {
  if (row[key + '_at']) return row[key + '_at'].slice(11,16);
  const value = row[key + '_min'];
  return Number.isFinite(value) ? String(Math.floor(value / 60)).padStart(2,'0') + ':' + String(value % 60).padStart(2,'0') : '';
}

function eventLabel(row) {
  const name = places.get(row.poi_id)?.name;
  const kind = {visit:'Посещение', meal:'Перерыв на питание', travel:'Переход', rest:'Отдых', wait:'Ожидание', waiting:'Ожидание'};
  const mode = {walk:'Пешком',transit:'Общественный транспорт',taxi:'Такси'}[row.mode];
  const meal = {breakfast:'Завтрак', lunch:'Обед', dinner:'Ужин'}[row.meal_kind];
  const minutes = row.planned_minutes ?? (row.start_at
    ? Math.round((Date.parse(row.end_at) - Date.parse(row.start_at)) / 60000)
    : row.end_min - row.start_min);
  const moscow = trip.city === 'moscow';
  const cost = moscow ? row.price_rub ?? row.transport_cost_rub : row.price_cny ?? row.cost_cny ?? row.transport_cost_cny;
  const estimate = row.basis === 'geometric_estimate' || row.duration_basis === 'expert_estimate';
  return [clock(row,'start') + '–' + clock(row,'end'), row.type === 'travel' ? mode || 'Пеший переход' :
    (meal && name ? meal + ' · ' + name : name || kind[row.type] || 'Перерыв'),
    estimate ? 'ориентировочно' : '',
    Number.isFinite(minutes) ? minutes + ' мин' : '', cost == null ? '' : cost + (moscow ? ' ₽' : ' CNY')].filter(Boolean).join(' · ');
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
    const stations = [];
    const rail = (row.transport_steps || []).flatMap(step => {
      const network = step.kind === 'rail_estimate' ? step.line?.split(':')[0] : null;
      if (step.kind === 'rail_estimate' || step.kind === 'walk_transfer') {
        for (const stop of [step.from_stop_location, step.to_stop_location]) {
          if (stop?.coordinates) stations.push({...stop, transfer:step.kind === 'walk_transfer'});
        }
      }
      if (step.geometry?.type === 'LineString') return [{mode:network ? 'transit' : 'walk', network,
        verified:!network || step.geometry_verified === true, coordinates:step.geometry.coordinates}];
      if (step.kind !== 'rail_estimate' || !step.from_stop_location || !step.to_stop_location) return [];
      return [{mode:'transit', network, verified:false,
        coordinates:[step.from_stop_location.coordinates, step.to_stop_location.coordinates]}];
    });
    return {...row, stations, use_plan_geometry:true, planned_minutes:Math.ceil(row.duration_seconds / 60),
      ...(row.geometry?.type === 'LineString' ? {geometry_segments:[
        {mode:row.mode, coordinates:row.geometry.coordinates, verified:true},
      ]} : rail.length ? {geometry_segments: rail} : {})};
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
  if (day?.start_at && day.end_at) appendText(stops, 'Начало ' + day.start_at.slice(11,16) + ' · окончание ' + day.end_at.slice(11,16));
  let legIndex = 0;
  for (const [rowIndex,row] of rows.entries()) {
    const previous = rows[rowIndex-1];
    const wait = row.wait_before_min ?? (previous && Number.isFinite(row.start_min) ? row.start_min - previous.end_min : 0);
    if (wait > 60 && !['wait', 'waiting'].includes(row.type) && !['wait', 'waiting'].includes(previous?.type)) {
      appendText(stops, 'Ожидание · ' + wait + ' мин' + (row.waiting_reason ? ' · ' + row.waiting_reason : ''));
    }
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
    if (row.reason && ['waiting','wait','meal','rest'].includes(row.type)) {
      const detail = document.createElement('p');
      detail.textContent = row.reason;
      card.append(detail);
    }
    if (row.transport_steps) {
      const sequence = document.createElement('ol');
      for (const step of row.transport_steps) {
        const label = {walk_access:'Подход', walk_egress:'Выход к месту', walk_transfer:'Пересадка',
          wait_estimate:'Ожидание', rail_estimate:'Поездка', alight:'Выход со станции'}[step.kind];
        const stop = step.to_stop_location || step.from_stop_location;
        const [mode, line] = (step.line || '').split(':');
        const service = {metro:'Метро', mcc:'МЦК', mcd:'МЦД', tram:'Трамвай', bus:'Автобус',
          electric_bus:'Электробус', trolleybus:'Троллейбус'}[mode];
        appendText(sequence, [label, service && service + ' ' + line, stop?.name, Math.ceil(step.duration_seconds / 60) + ' мин'].filter(Boolean).join(' · '));
      }
      card.append(sequence);
    }
    item.append(time,card);
    stops.append(item);
  }
  for (const tab of element('route-tabs').children) tab.setAttribute('aria-pressed', String(tab.dataset.day === element('route-day').value));
  if (!rows.length) appendText(stops,'В выбранный день не удалось запланировать посещения.');
  for (const warning of day?.warnings || []) appendText(stops, warning);
  for (const gap of trip.plan.gap_diagnostics?.remaining || []) {
    if (gap.date === day?.date && gap.minutes > 30 && (gap.kind !== 'internal' || gap.minutes <= 60)) {
      appendText(stops, gap.start_at.slice(11,16) + '–' + gap.end_at.slice(11,16) + ' · ' + gap.reason);
    }
  }
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
  element('cost-note').textContent = moscow ? 'Ориентировочная сумма для всей группы с учётом запланированных заведений. Расходы без оценки и свободные перерывы на питание не включены.' :
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
  const railMissing = moscow && ['transit','mixed'].includes(trip.options.transport) &&
    !trip.plan.days.some(day => day.legs.some(leg => leg.transport_steps?.length));
  const interests = {museums:'музеи', history:'история', nature:'природа', cafes:'кафе',
    entertainment:'развлечения', shopping:'шопинг', viewpoints:'смотровые', unusual:'необычные места'};
  const uncovered = (trip.plan.uncovered_interests || []).map(key => interests[key]).filter(Boolean);
  element('route-warnings').textContent = moscow
    ? (trip.poi_snapshot_id.startsWith('moscow-v7:') ? [
      'Уточните часы работы, цены и доступ к входам. Переходы без дорожной геометрии ориентировочны.',
      railMissing ? 'Для выбранных мест подошли только пешие переходы из доступных данных.' : '',
      uncovered.length ? 'Не удалось включить интересы: ' + uncovered.join(', ') + '.' : '',
      ...(trip.plan.unplaced_required_poi_ids?.length ? trip.plan.warnings || [] : []),
    ].join(' ').trim() : 'Сохранённый маршрут прежнего пилота. Уточните часы и цены.')
    : ['Учебные цены и условия посещения.', ...(trip.plan.warnings || [])].join(' ');
  renderDay();
  const suggestions = element('route-suggestions');
  suggestions.replaceChildren();
  for (const id of trip.plan.suggested_poi_ids || []) if (places.has(id)) appendText(suggestions, places.get(id).name);
  suggestions.parentElement.hidden = !suggestions.childElementCount;
}

const chatForm = element('edit-route');
const chatPath = endpoint + '/assistant/';
const chatStatus = element('chat-status');
function chatLine(author, message) {
  const line = document.createElement('p');
  line.className = author === 'Вы' ? 'chat-message chat-user' : 'chat-message chat-assistant';
  line.textContent = author + ': ' + message;
  const log = element('chat-log');
  log.append(line);
  log.scrollTop = log.scrollHeight;
}
const control = busy => {
  for (const button of [chatForm.querySelector('button'),element('confirm-edit'),element('reject-edit')]) button.disabled = busy;
};

async function load() {
  trip = await request(endpoint);
  const legacy = trip.city === 'moscow' && !trip.poi_snapshot_id.startsWith('moscow-v7:');
  const catalog = await request('/api/planned-trips/catalog/' + trip.city + (legacy ? '?include_legacy=true' : ''));
  for (const place of catalog) places.set(place.id,{...place, area:trip.city === 'moscow' ? 'Москва' : 'Шанхай'});
  renderTrip();
  const status = await request(chatPath + 'status');
  for (const item of status.conversation || []) chatLine(item.role === 'user' ? 'Вы' : 'TripWise', item.content);
  chatStatus.textContent = status.reason || (status.mode === 'mock' ? 'Тестовый режим: ответы подставляет проверочный сценарий, а не ИИ.' : '');
  if (!status.available) return;
  csrf = status.csrf_token;
  registration = await request(chatPath + 'register',{});
  if (!status.reason && status.mode !== 'mock') chatStatus.textContent = 'Помощник подключён.';
  control(false);
}

element('route-day').addEventListener('change',renderDay);
chatForm.addEventListener('submit',async event => {
  event.preventDefault();
  const input = element('chat-text');
  if (!registration || !input.value.trim()) return;
  control(true);
  proposal = null;
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
    for (const item of result.diff?.added || []) appendText(preview, '+ ' + item.name + ' · ' + item.day);
    for (const item of result.diff?.removed || []) appendText(preview, '− ' + item.name + ' · ' + item.day);
    if (trip.city === 'moscow' && result.diff?.before_cost !== result.diff?.after_cost) {
      appendText(preview, 'Оценка затрат · ' + result.diff.before_cost + ' → ' + result.diff.after_cost + ' ₽');
    }
    if (result.diff?.transport) appendText(preview, 'Транспорт · ' + {walk:'пешком', transit:'общественный', taxi:'такси'}[result.diff.transport]);
    for (const change of result.diff?.optimization || []) {
      const label = {travel:'Дорога', waiting:'Ожидание между остановками', walking:'Пешие переходы'}[change.goal];
      appendText(preview, label + ' · ' + change.day + ' · ' + change.before + ' → ' + change.after + ' мин');
    }
    if (result.diff?.transit_filters) {
      const names = {metro:'метро', mcc:'МЦК', mcd:'МЦД', tram:'трамвай', bus:'автобус', electric_bus:'электробус'};
      for (const [key, label] of [['require_transit','Включить'], ['avoid_transit','Исключить']]) {
        const modes = result.diff.transit_filters[key] || [];
        if (modes.length) appendText(preview, label + ' · ' + modes.map(mode => names[mode] || mode).join(', '));
      }
    }
    for (const day of result.plan.days) for (const row of events(day, result.plan)) appendText(preview, day.date + ' · ' + eventLabel(row));
    element('chat-preview').hidden = false;
  } catch (error) { chatStatus.textContent = error.message; chatLine('TripWise', error.message); }
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
load().catch(error => { (trip ? chatStatus : element('route-meta')).textContent = error.message; });
