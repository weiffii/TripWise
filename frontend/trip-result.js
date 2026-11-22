const element = id => document.getElementById(id);
const resultParams = new URLSearchParams(location.search);
const previewId = resultParams.get('preview');
const routeId = resultParams.get('route');
const isPreview = Boolean(previewId);
const endpoint = isPreview ? '/api/route-previews/' + encodeURIComponent(previewId) : '/api/planned-trips/' + encodeURIComponent(routeId || '');
let ownerContext = null;
const places = new Map();
const refreshedLegs = new Map();
let trip, registration, csrf, proposal;

async function request(path, body) {
  const response = await fetch(path, body ? {
    method: 'POST', credentials:'same-origin', headers: {'Content-Type':'application/json', 'X-TripWise-CSRF':csrf || '', 'X-TripWise-Draft-Owner':ownerContext || ''},
    body: JSON.stringify(path.includes('/assistant/') ? {...body, request_id:crypto.randomUUID()} : body),
  } : {cache:'no-store',credentials:'same-origin'});
  if (response.status === 401) location.assign('/');
  const result = await response.json();
  if (!response.ok || ['error','unavailable'].includes(result.type)) throw Error(result.detail || result.message || 'Запрос не выполнен.');
  return result;
}

function gapExplanation(gap) {
  const labels = {budget:'бюджет', opening:'часы работы', transport:'переходы', time:'дневное окно',
    meals:'интервалы питания', unknown:'недостающие данные', search_limit:'предел проверки'};
  const rejected = Object.entries(gap.rejections || {}).sort((a,b) => b[1]-a[1]).slice(0,2)
    .map(([reason,count]) => (labels[reason] || 'прочие ограничения') + ' (' + count + ')');
  const advice = {budget:'Более высокий бюджет расширит выбор.', opening:'Другая дата может добавить доступные места.',
    time:'Более широкое дневное окно позволит проверить дополнительные варианты.',
    transport:'Другой разрешённый режим транспорта может сократить переход.'}[gap.reason_code];
  return [gap.reason, gap.candidate_count ? 'Кандидатов рассмотрено: ' + gap.candidate_count + '.' : '',
    rejected.length ? 'Основные причины отклонения: ' + rejected.join(', ') + '.' : '', advice].filter(Boolean).join(' ');
}

function events(day, plan = trip.plan) {
  if (day.events) return day.events.filter(showScheduleEvent);
  const meals = new Map(Object.entries(plan.moscow_meals?.[day.date] || {}).map(([kind, slot]) => [slot.poi_id, kind]));
  const diagnostics = new Map((plan.gap_diagnostics?.remaining || []).filter(gap => gap.date === day.date)
    .map(gap => [gap.start_at + '/' + gap.end_at, gapExplanation(gap)]));
  return [...day.visits.map(item => ({...item, type:meals.has(item.poi_id) ? 'meal' : 'visit', meal_kind:meals.get(item.poi_id)})),
    ...day.legs.map((item, index) => {
      const geometry = plan === trip.plan && refreshedLegs.get(day.date + '/' + index);
      return {...item, ...(geometry || {}), leg_index:index, day_date:day.date, type:'travel', mode:item.mode || (item.transport_steps?.length ? 'transit' :
        (trip.options.transport === 'taxi' ? 'taxi' : 'walk'))};
    }),
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

function eventDetails(row) {
  const name = places.get(row.poi_id)?.name;
  const kind = {visit:'Посещение', meal:'Перерыв на питание', travel:'Переход', rest:'Отдых', wait:'Ожидание', waiting:'Ожидание'};
  const mode = {walk:'Пешком',transit:'Общественный транспорт',taxi:'Такси'}[row.mode];
  const meal = {breakfast:'Завтрак', lunch:'Обед', dinner:'Ужин'}[row.meal_kind];
  const minutes = row.planned_minutes ?? (row.start_at
    ? Math.round((Date.parse(row.end_at) - Date.parse(row.start_at)) / 60000)
    : row.end_min - row.start_min);
  const moscow = trip.city === 'moscow';
  const cost = moscow ? row.price_rub ?? row.transport_cost_rub : row.price_cny ?? row.cost_cny ?? row.transport_cost_cny;
  const durationEstimate = row.basis === 'geometric_estimate' || row.duration_basis === 'expert_estimate';
  const costEstimate = row.price_basis === 'expert_estimate' || row.transport_cost_basis === 'synthetic_fare_estimate';
  const action = row.type === 'travel' ? mode || 'Переход' : name || meal || kind[row.type] || 'Перерыв';
  return {action, category: name ? meal || kind[row.type] : '', minutes, cost, durationEstimate, costEstimate,
    currency: moscow ? '₽' : 'CNY'};
}

function eventLabel(row) {
  const info = eventDetails(row);
  return [`${clock(row, 'start')}–${clock(row, 'end')}`, info.category, info.action,
    Number.isFinite(info.minutes) ? `${info.durationEstimate ? '≈' : ''}${info.minutes} мин` : '',
    info.cost == null ? '' : `${info.costEstimate ? '≈' : ''}${info.cost} ${info.currency}`]
    .filter(Boolean).join(' · ');
}

function scheduleMinute(row, edge) {
  const value = clock(row, edge);
  if (!value) return NaN;
  const [hours, minutes] = value.split(':').map(Number);
  return hours * 60 + minutes;
}

function scheduleWithWaiting(rows) {
  return rows.flatMap((row, index) => {
    const previous = rows[index - 1];
    const wait = row.wait_before_min ?? (previous ? row.start_min - previous.end_min : 0);
    if (wait > 60 && !['wait', 'waiting'].includes(previous?.type) && !['wait', 'waiting'].includes(row.type)) {
      const end = scheduleMinute(row, 'start');
      return [{type: 'waiting', start_min: end - wait, end_min: end, reason: row.waiting_reason}, row];
    }
    return [row];
  });
}

function daySections(rows) {
  const mealEnd = kind => {
    const meal = rows.find(row => row.type === 'meal' && row.meal_kind === kind);
    return meal ? scheduleMinute(meal, 'end') : NaN;
  };
  const lunch = mealEnd('lunch');
  const dinner = mealEnd('dinner');
  const afternoon = Number.isFinite(lunch) ? lunch : Math.min(14 * 60, dinner || Infinity);
  const evening = Number.isFinite(dinner) ? Math.max(afternoon, dinner) : Math.max(afternoon, 19 * 60);
  const sections = [
    {title: 'До обеда', tone: 'morning', rows: []},
    {title: 'После обеда', tone: 'afternoon', rows: []},
    {title: 'После ужина', tone: 'evening', rows: []},
  ];
  for (const row of rows) {
    const time = scheduleMinute(row, 'start');
    sections[time >= evening ? 2 : time >= afternoon ? 1 : 0].rows.push(row);
  }
  return {sections, approximate: !Number.isFinite(lunch) || !Number.isFinite(dinner)};
}

function scheduleCell(label, text, extraClass = '') {
  const cell = document.createElement('span');
  cell.className = `schedule-cell ${extraClass}`;
  cell.dataset.label = label;
  cell.textContent = text;
  return cell;
}

function appendText(parent, text) {
  const item = document.createElement('li');
  item.textContent = text;
  parent.append(item);
}

const mapDialog = element('route-map-dialog');
const mapPanel = element('route-map-panel');
const expandMapButton = element('expand-route-map');
const closeMapForm = element('close-route-map');
const sidebarMap = window.matchMedia('(min-width: 1024px)');
let mapOpener;

function openMap(opener) {
  if (mapDialog.open) return;
  mapOpener = opener;
  mapDialog.append(mapPanel);
  expandMapButton.hidden = true;
  closeMapForm.hidden = false;
  mapDialog.showModal();
}

expandMapButton.addEventListener('click', () => {
  openMap(expandMapButton);
  routeMap.fitSelection();
});

mapDialog.addEventListener('close', () => {
  element('route-map-sidebar').append(mapPanel);
  expandMapButton.hidden = false;
  closeMapForm.hidden = true;
  routeMap.fitSelection();
  mapOpener?.focus({preventScroll:true});
});

function revealMap(opener) {
  if (!sidebarMap.matches) openMap(opener);
}

const routeMap = window.TripWiseMap.create({
  container:element('street-map'), svg:element('route-map'), tiles:element('map-tiles'),
  background:element('map-background'), directTiles:true, focusStopWithArrival:true,
  allowWheelZoom:event => mapDialog.open || event.ctrlKey || event.metaKey,
  onStopSelect:index => {
    for (const button of element('route-stops').querySelectorAll('button[data-stop-index]')) {
      button.setAttribute('aria-pressed', String(Number(button.dataset.stopIndex) === index));
    }
  },
  onSelect:html => { element('route-directions').innerHTML = html; },
  onNetworkStatus:text => { element('road-status').textContent = text; },
  onStatus:text => { element('map-status').textContent = text; },
});

function drawMap(rows) {
  element('road-status').textContent = '';
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
        verified:step.geometry_verified === true, coordinates:step.geometry.coordinates}];
      if (step.kind !== 'rail_estimate' || !step.from_stop_location || !step.to_stop_location) return [];
      return [{mode:'transit', network, verified:false,
        coordinates:[step.from_stop_location.coordinates, step.to_stop_location.coordinates]}];
    });
    const hasRoad = row.geometry?.type === 'LineString'&&row.basis === 'routing_provider';
    const segments = row.mode === 'transit'&&rail.length ? rail : row.geometry?.type === 'LineString'
      ? [{mode:row.mode,coordinates:row.geometry.coordinates,verified:hasRoad}] : [];
    return {...row, stations, use_plan_geometry:true, planned_minutes:Math.ceil(row.duration_seconds / 60),
      ...(segments.length ? {geometry_segments: segments} : {})};
  });
  routeMap.setDay({events:mappedRows}, mapPlaces, {required_ids:trip.preferences?.required_ids || []});
  expandMapButton.disabled = false;
}

function renderScheduleRow(row, legIndex, stopIndex) {
  const info = eventDetails(row);
  const item = document.createElement('li');
  item.className = `schedule-row route-event-${row.type}`;

  const action = document.createElement('div');
  action.className = 'schedule-action';
  const place = places.get(row.poi_id);
  const canMap = place && Number.isFinite(place.lat) && Number.isFinite(place.lon);
  const title = document.createElement(canMap || row.type === 'travel' ? 'button' : 'strong');
  title.textContent = info.action;
  if (title.tagName === 'BUTTON') title.type = 'button';
  if (canMap && stopIndex !== undefined) {
    title.dataset.stopIndex = stopIndex;
    title.setAttribute('aria-pressed', 'false');
    title.setAttribute('aria-controls', 'route-map-panel');
    title.onclick = () => {
      revealMap(title);
      routeMap.selectStop(stopIndex);
    };
  }
  if (row.type === 'travel') {
    title.onclick = () => {
      revealMap(title);
      routeMap.selectLeg(legIndex);
    };
  }
  if (info.category) {
    const category = document.createElement('small');
    category.className = 'schedule-category';
    category.textContent = info.category;
    action.append(category);
  }
  action.append(title);
  const pinned = row.pinned_start_time || trip.plan.visit_times?.[row.start_at?.slice(0,10)]?.[row.poi_id];
  if (pinned && row.type === 'visit') {
    const value = document.createElement('small');
    value.textContent = 'Закреплено в ' + (typeof pinned === 'string' ? pinned : clock(row, 'start'));
    action.append(value);
  }
  const approximate = row.mode === 'transit'
    ? row.transport_steps?.some(step => ['walk_access','walk_egress','walk_transfer','rail_estimate'].includes(step.kind) && !step.geometry_verified)
    : !row.geometry || row.basis !== 'routing_provider';
  if (row.type === 'travel' && approximate && trip.city === 'moscow' &&
      trip.poi_snapshot_id.startsWith('moscow-v7:') && Number.isInteger(row.leg_index)) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Проверить геометрию';
    const status = document.createElement('small');
    status.setAttribute('role', 'status');
    retry.onclick = async () => {
      retry.disabled = true;
      status.textContent = 'Проверяем участок…';
      try {
        const version = trip.version;
        const {duration_seconds, ...geometry} = await request(endpoint + '/leg-geometry', {
          version, day:row.day_date, leg_index:row.leg_index,
        });
        if (trip.version !== version) return;
        refreshedLegs.set(row.day_date + '/' + row.leg_index, geometry);
        renderDay();
        element('route-action-status').textContent =
          'Линия участка обновлена на карте. Оценка дороги после проверки — ' + Math.ceil(duration_seconds / 60) +
          ' мин. Расписание поездки сохранено.';
      } catch (error) { status.textContent = error.message; }
      finally { retry.disabled = false; }
    };
    action.append(retry, status);
  }
  if (row.reason && ['waiting', 'wait', 'meal', 'rest'].includes(row.type)) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Подробности';
    const reason = document.createElement('p');
    reason.textContent = row.reason;
    details.append(summary, reason);
    action.append(details);
  }
  if (row.transport_steps?.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Как добраться';
    const steps = document.createElement('ol');
    for (const step of row.transport_steps) {
      const label = {walk_access:'Подход', walk_egress:'Выход к месту', walk_transfer:'Пересадка',
        wait_estimate:'Ожидание', rail_estimate:'Поездка', alight:'Выход со станции'}[step.kind];
      const stop = step.to_stop_location || step.from_stop_location;
      const [mode, line] = (step.line || '').split(':');
      const service = {metro:'Метро', mcc:'МЦК', mcd:'МЦД', tram:'Трамвай', bus:'Автобус',
        electric_bus:'Электробус', trolleybus:'Троллейбус'}[mode];
      const duration = Number.isFinite(step.duration_seconds) ? `${Math.ceil(step.duration_seconds / 60)} мин` : '';
      appendText(steps, [label, service && [service, line].filter(Boolean).join(' '), stop?.name, duration].filter(Boolean).join(' · '));
    }
    details.append(summary, steps);
    action.append(details);
  }
  const minutes = Number.isFinite(info.minutes) ? `${info.durationEstimate ? '≈' : ''}${info.minutes} мин` : '—';
  const cost = Number.isFinite(info.cost)
    ? `${info.costEstimate ? '≈' : ''}${new Intl.NumberFormat('ru-RU').format(info.cost)} ${info.currency}`
    : '—';
  item.append(scheduleCell('Время', `${clock(row, 'start')}–${clock(row, 'end')}`, 'schedule-time'),
    action, scheduleCell('Длительность', minutes), scheduleCell('Стоимость', cost));
  return item;
}

function renderDay() {
  const day = trip.plan.days[Number(element('route-day').value)];
  const rows = day ? events(day) : [];
  const stops = element('route-stops');
  stops.replaceChildren();
  if (rows.length) {
    const {sections, approximate} = daySections(scheduleWithWaiting(rows));
    element('schedule-note').textContent = approximate
      ? 'Без обеда или ужина разделы ориентируются на время дня. ≈ — оценка, прочерк — нет данных.'
      : 'Обед и ужин завершают предыдущий блок. ≈ — оценка, прочерк — нет данных.';
    const stopIndexes = new Map(rows.filter(row => {
      const place = places.get(row.poi_id);
      return ['visit','meal'].includes(row.type) && Number.isFinite(place?.lat) && Number.isFinite(place?.lon);
    }).map((row,index) => [row,index]));
    const legIndexes = new Map(rows.filter(row => row.type === 'travel').map((row, index) => [row, index]));
    for (const section of sections) {
      const block = document.createElement('li');
      block.className = `schedule-block schedule-${section.tone}`;
      const heading = document.createElement('h3');
      heading.textContent = section.title;
      block.append(heading);
      if (!section.rows.length) {
        const empty = document.createElement('p');
        empty.className = 'schedule-empty';
        empty.textContent = 'Нет запланированных действий';
        block.append(empty);
      } else {
        const list = document.createElement('ol');
        list.className = 'schedule-rows';
        for (const row of section.rows) list.append(renderScheduleRow(row, legIndexes.get(row), stopIndexes.get(row)));
        block.append(list);
      }
      stops.append(block);
    }
  } else {
    element('schedule-note').textContent = '';
  }
  for (const tab of element('route-tabs').children) tab.setAttribute('aria-pressed', String(tab.dataset.day === element('route-day').value));
  if (!rows.length) appendText(stops,'В выбранный день не удалось запланировать посещения.');
  if (trip.city === 'moscow' && day?.cost_breakdown) {
    const b = day.cost_breakdown;
    appendText(stops, `За день: посещения ≈${b.visits_rub} ₽ · питание ≈${b.meals_rub} ₽ · транспорт ≈${b.transport_rub} ₽ · всего ≈${b.total_rub} ₽`);
  }
  for (const warning of day?.warnings || []) appendText(stops, warning);
  for (const gap of trip.plan.gap_diagnostics?.remaining || []) {
    if (gap.date === day?.date && gap.minutes > 30 && (gap.kind !== 'internal' || gap.minutes <= 60)) {
      appendText(stops, gap.start_at.slice(11,16) + '–' + gap.end_at.slice(11,16) + ' · ' + gapExplanation(gap));
    }
  }
  drawMap(rows);
}

function renderTrip() {
  routeMap.beginPlan();
  refreshedLegs.clear();
  const moscow = trip.city === 'moscow';
  element('route-title').textContent = moscow ? 'Москва' : 'Шанхай';
  const budget = trip.plan.budget_summary;
  const total = moscow ? budget?.known_total : trip.plan.summary?.total_cost_cny;
  const currency = moscow ? 'RUB' : 'CNY';
  element('route-meta').textContent = (isPreview ? 'Предварительный маршрут · ещё не сохранён' : 'Сохранённая поездка') + ' · ' + trip.plan.days.length + ' дн.';
  element('route-state').textContent=isPreview?'Маршрут пока виден только вам в этом сеансе. Сохраните поездку, чтобы открыть её позднее.':'Поездка сохранена. Для изменений сначала нажмите «Редактировать». ';
  element('save-trip').hidden=!isPreview;
  element('change-preferences').hidden=!isPreview;
  element('route-comparison').hidden = !isPreview || !moscow;
  element('comparison-metrics').replaceChildren();
  element('comparison-link').hidden = true;
  element('comparison-status').textContent = '';
  if(isPreview)element('change-preferences').href='/trip/new?preview='+encodeURIComponent(previewId);
  const rub = value => new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(value);
  element('route-cost').textContent = total == null ? 'Оценка недоступна' :
    '≈ ' + new Intl.NumberFormat('ru-RU',{style:'currency',currency}).format(Number(total));
  element('cost-note').textContent = moscow && budget?.estimated_total != null
    ? `Бюджет: ${rub(budget.budget_amount)} · Посещения: ≈${rub(budget.visits_total)} · Питание: ≈${rub(budget.meals_total)} · Транспорт: ≈${rub(budget.transport_total)} · Остаток: ≈${rub(budget.remaining)}. Стоимость такси и ОТ рассчитана синтетической планировочной моделью, не является тарифом или предложением перевозчика. Часть цен и часов посещений тоже оценочная.`
    : moscow ? 'Оценка неполная. Расходы без достоверной стоимости не равны нулю.'
    : 'Учебная оценка на всю поездку: посещения, питание и переходы.';
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
const editor = element('route-editor');
const editButtons = [...document.querySelectorAll('[data-route-editor]')];
const setEditBusy = busy => editButtons.forEach(button => { button.disabled = busy; });
function openRouteEditor() {
  if (!editor.open) editor.showModal();
}
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
  element('save-trip').disabled=busy||Boolean(proposal);
  for (const button of [chatForm.querySelector('button'),element('confirm-edit'),element('reject-edit')]) button.disabled = busy;
};

async function load() {
  if(!previewId&&!routeId)throw Error('Выберите поездку в разделе «Мои поездки» или создайте новый маршрут.');
  trip = await request(endpoint);
  if(typeof trip.owner_context!=='string'||!trip.owner_context)throw Error('Не удалось проверить аккаунт. Обновите страницу.');
  ownerContext=trip.owner_context;
  window.setDestinationVisual(trip.city==='moscow'?'Москва':'Шанхай');
  const legacy = trip.city === 'moscow' && !trip.poi_snapshot_id.startsWith('moscow-v7:');
  const catalog = await request('/api/planned-trips/catalog/' + trip.city + (legacy ? '?include_legacy=true' : ''));
  for (const place of catalog) places.set(place.id,{...place, area:trip.city === 'moscow' ? 'Москва' : 'Шанхай'});
  renderTrip();
  setEditBusy(false);
  if (!isPreview) return;
  if (new URLSearchParams(window.location.search).get('edit') === '1') openRouteEditor();
  chatStatus.textContent = 'Подключаем помощника…';
  const status = await request(chatPath + 'status');
  for (const item of status.conversation || []) chatLine(item.role === 'user' ? 'Вы' : 'TripWise', item.content);
  const namedAgent = status.agent_version ? 'Помощник Москвы: ' + status.agent_version +
    '. Попробуйте «Закрепи название места в 14:30». Команда работает без OpenRouter.' : '';
  chatStatus.textContent = [namedAgent, status.reason || (status.mode === 'mock' ? 'Тестовый режим: ответы подставляет проверочный сценарий, а не ИИ.' : '')].filter(Boolean).join(' ');
  if (!status.available) return;
  csrf = status.csrf_token;
  registration = await request(chatPath + 'register',{});
  if (!status.reason && status.mode !== 'mock') chatStatus.textContent = namedAgent || 'Помощник подключён.';
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
    const namedTimes = new Map((result.diff?.named_additions || []).map(item =>
      [JSON.stringify([item.name, item.day]), item.time]));
    for (const item of result.diff?.added || []) {
      const time = namedTimes.get(JSON.stringify([item.name, item.day]));
      appendText(preview, '+ ' + item.name + ' · ' + item.day + (time ? ' · ' + time : ''));
    }
    for (const item of result.diff?.removed || []) appendText(preview, '− ' + item.name + ' · ' + item.day);
    for (const meal of result.diff?.meal_replacements || []) {
      const label = {breakfast:'Завтрак', lunch:'Обед', dinner:'Ужин'}[meal.kind] || 'Питание';
      appendText(preview, label + ' · ' + meal.day + ' · ' + meal.time + ' · ' + meal.before + ' → ' + meal.after);
    }
    for (const window of result.diff?.day_windows || []) {
      appendText(preview, window.day + ' · время прогулки ' + window.day_start + '–' + window.day_end);
    }
    for (const pin of result.diff?.visit_times || []) {
      appendText(preview, pin.day + ' · ' + pin.name + ' · закреплено в ' + pin.start_time);
    }
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
    const changedDays = result.diff?.changed_days;
    const previewDays = changedDays?.length ? result.plan.days.filter(day => changedDays.includes(day.date))
      : result.plan.days;
    for (const day of previewDays) {
      for (const row of events(day, result.plan)) appendText(preview, day.date + ' · ' + eventLabel(row));
    }
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
      element('route-state').textContent='Изменения применены к предварительному маршруту. Нажмите «Сохранить поездку», чтобы записать их в аккаунт.';
    }
    const message = result.type === 'confirmed' && isPreview
      ? 'Изменения применены к предпросмотру. Чтобы записать их в аккаунт, сохраните поездку.'
      : result.message;
    if (message) chatLine('TripWise', message);
    chatStatus.textContent = result.type === 'confirmed' ? message : '';
    proposal = null;
    element('chat-preview').hidden = true;
  } catch (error) { chatStatus.textContent = error.message; }
  finally { control(false); }
}
element('confirm-edit').addEventListener('click',() => void decide('confirm'));
element('reject-edit').addEventListener('click',() => void decide('reject'));
element('route-comparison-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  const status = element('comparison-status');
  const metrics = element('comparison-metrics');
  const link = element('comparison-link');
  const version = trip.version;
  status.textContent = 'Рассчитываем вариант с теми же параметрами поездки…';
  button.disabled = true;
  metrics.replaceChildren();
  link.hidden = true;
  try {
    const {base,variant,preview_id,improved} = await request(endpoint+'/compare', {
      expected_version:version, route_priority:element('comparison-priority').value,
    });
    if (trip.version !== version) return;
    const walking = row => row.walking_complete ? String(row.walk_minutes) : row.walk_minutes + ' (частичные данные)';
    for (const [label,before,after] of [
      ['Пешком, мин',walking(base),walking(variant)],
      ['Пересадки',base.transfers,variant.transfers],
      ['Расходы, ₽',base.cost_rub,variant.cost_rub],
      ['Посещения',base.visit_count,variant.visit_count],
    ]) appendText(metrics, label + ' · ' + before + ' → ' + after);
    status.textContent = improved === false ? 'Исходный маршрут → новый вариант. Выбранный показатель не улучшился.'
      : 'Исходный маршрут → новый вариант';
    link.href = '/trip/result?preview='+encodeURIComponent(preview_id);
    link.hidden = false;
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
element('save-trip').addEventListener('click',async()=>{
  if(!trip||!isPreview)return;const button=element('save-trip');button.disabled=true;element('route-action-status').textContent='Сохраняем поездку…';
  try{const saved=await request(endpoint+'/save',{version:trip.version});if(!saved.route_id)throw Error('Сервер не подтвердил сохранение.');window.location.assign('/trip/result?route='+encodeURIComponent(saved.route_id));}
  catch(error){element('route-action-status').textContent=error.message;}finally{button.disabled=false;}
});
async function editRoute() {
  if (!trip) return;
  if (isPreview) { openRouteEditor(); return; }
  setEditBusy(true);
  const status = element('route-action-status');
  status.textContent = 'Открываем редактирование…';
  try {
    const preview = await request(endpoint + '/preview', {version:trip.version});
    if (!preview.preview_id) throw Error('Не удалось открыть редактор.');
    window.location.assign('/trip/result?preview=' + encodeURIComponent(preview.preview_id) + '&edit=1');
  } catch (error) {
    status.textContent = error.message;
    setEditBusy(false);
  }
}
for (const button of editButtons) button.addEventListener('click',editRoute);
load().catch(error => { (trip ? chatStatus : element('route-meta')).textContent = error.message; });
