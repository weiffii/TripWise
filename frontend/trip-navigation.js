(() => {
  const byId = id => document.getElementById('navigation-' + id);
  const dialog = document.getElementById('trip-navigation');
  const mapBox = byId('map'), input = byId('origin'), status = byId('status');
  const pick = byId('pick'), locate = byId('locate'), build = byId('build');
  const notice = text => { status.textContent = text; };
  let session, map, pointer, operation = 0, locationAttempt = 0;

  function showPath(destination, result) {
    if (!map) map = window.TripWiseMap.create({container:mapBox, directTiles:true, fetchRoads:false,
      svg:mapBox.querySelector('.navigation-lines'), tiles:mapBox.querySelector('.navigation-tiles'),
      background:mapBox.querySelector('.navigation-background')});
    const finish = {...destination, id:'finish'};
    const stops = new Map([['finish',finish]]);
    const events = [{type:'visit', poi_id:'finish'}];
    if (result) {
      stops.set('start', {...result.origin, id:'start', name:'Начало пути'});
      events.unshift({type:'visit', poi_id:'start'}, {type:'travel', from_poi_id:'start', to_poi_id:'finish',
        mode:result.mode === 'car' ? 'taxi' : 'walk', use_plan_geometry:true, basis:'routing_provider',
        geometry:result.geometry, planned_minutes:Math.ceil(result.duration_seconds / 60),
        geometry_segments:[{mode:result.mode === 'car' ? 'taxi' : 'walk', verified:true, coordinates:result.geometry.coordinates}]});
    }
    map.setDay({events}, stops);
    if (result) map.selectLeg(0);
  }

  const format = ({lat,lon}) => `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  function stopPicking() {
    pick.setAttribute('aria-pressed','false');
    mapBox.classList.remove('picking');
    pointer = null;
  }

  function parseOrigin(text) {
    const match = text.match(/^\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:;|,\s*|\s+)\s*([+-]?\d+(?:[.,]\d+)?)\s*$/);
    if (match) {
      const [lat,lon] = match.slice(1).map(value => Number(value.replace(',','.')));
      if (Math.abs(lat) <= 85 && Math.abs(lon) <= 180) return {lat,lon};
    }
    const normalize = value => value.trim().toLowerCase().replace(/ё/g,'е');
    const matches = session.places.filter(place => normalize(place.name) === normalize(text) || normalize(place.label) === normalize(text));
    if (matches.length === 1) return {lat:matches[0].lat, lon:matches[0].lon};
    return null;
  }

  function suggest() {
    const value = input.value.trim().toLowerCase().replace(/ё/g,'е');
    const options = value.length < 2 ? [] : session.places.filter(place => place.name.toLowerCase().replace(/ё/g,'е').includes(value)).slice(0, 12);
    byId('places').replaceChildren(...options.map(place => Object.assign(document.createElement('option'), {value:place.label})));
  }

  window.TripWiseNavigation = {open(options) {
    const suitable = [...options.places.values()].filter(place => Number.isFinite(place.lat) && Number.isFinite(place.lon));
    const names = new Map();
    for (const place of suitable) names.set(place.name, (names.get(place.name) || 0) + 1);
    const places = suitable.map(place => ({...place, label:names.get(place.name) === 1 ? place.name : `${place.name} · ${format(place)}`}));
    session = {...options, places};
    byId('destination').textContent = options.destination.name;
    byId('mode').value = options.mode;
    input.value = '';
    byId('places').replaceChildren();
    byId('steps').hidden = true;
    build.disabled = locate.disabled = false;
    notice('Выберите место из подсказок, укажите точку на карте или поделитесь геопозицией.');
    dialog.showModal();
    showPath(options.destination);
    input.focus({preventScroll:true});
  }};

  dialog.addEventListener('close', () => {
    ++operation; ++locationAttempt;
    stopPicking();
    session?.opener.focus({preventScroll:true});
    session = null;
  });
  input.addEventListener('input', () => {
    if (!session) return;
    ++locationAttempt;
    locate.disabled = false;
    stopPicking();
    suggest();
  });
  locate.onclick = () => {
    if (!navigator.geolocation) return notice('Геопозиция недоступна. Укажите начальную точку вручную.');
    const attempt = ++locationAttempt;
    stopPicking();
    locate.disabled = true;
    notice('Определяем вашу геопозицию…');
    const done = text => { locate.disabled = false; notice(text); };
    navigator.geolocation.getCurrentPosition(({coords}) => {
      if (attempt !== locationAttempt || !dialog.open) return;
      input.value = format({lat:coords.latitude,lon:coords.longitude});
      done('Геопозиция выбрана. Нажмите «Построить маршрут».');
    }, () => {
      if (attempt === locationAttempt && dialog.open) done('Не удалось получить геопозицию. Выберите точку на карте или введите её вручную.');
    }, {enableHighAccuracy:true, timeout:12000, maximumAge:15000});
  };
  pick.onclick = () => {
    if (pick.getAttribute('aria-pressed') === 'true') { stopPicking(); notice('Выбор точки отменён.'); return; }
    ++locationAttempt;
    locate.disabled = false;
    pick.setAttribute('aria-pressed','true');
    mapBox.classList.add('picking');
    notice('Коснитесь карты, чтобы выбрать старт. Карту по-прежнему можно двигать.');
  };
  const onMap = event => !event.target.closest('.tw-map-controls, .tw-map-popup, .map-credit');
  mapBox.addEventListener('pointerdown', event => {
    if (pick.getAttribute('aria-pressed') === 'true' && event.button === 0 && onMap(event)) pointer = {id:event.pointerId, x:event.clientX, y:event.clientY, view:map.debug()};
  }, true);
  mapBox.addEventListener('pointercancel', () => { pointer = null; });
  mapBox.addEventListener('pointerup', event => {
    const start = pointer;
    pointer = null;
    if (!start || start.id !== event.pointerId || Math.hypot(start.x - event.clientX, start.y - event.clientY) > 5 || !onMap(event)) return;
    const {center:[lon,lat], zoom} = start.view;
    const rect = mapBox.getBoundingClientRect(), scale = 256 * 2 ** zoom;
    const x = lon + (event.clientX - rect.left - rect.width / 2) * 360 / scale;
    const mercator = Math.asinh(Math.tan(lat * Math.PI / 180)) - (event.clientY - rect.top - rect.height / 2) * 2 * Math.PI / scale;
    input.value = format({lat:Math.atan(Math.sinh(mercator)) * 180 / Math.PI, lon:x});
    stopPicking();
    notice('Точка выбрана. Нажмите «Построить маршрут».');
  });
  byId('form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!session || build.disabled) return;
    const origin = parseOrigin(input.value);
    if (!origin) { notice('Выберите точное место из подсказок или введите координаты, например 55.75, 37.62.'); input.focus(); return; }
    const job = ++operation;
    ++locationAttempt;
    stopPicking();
    locate.disabled = false;
    build.disabled = true;
    byId('steps').hidden = true;
    showPath(session.destination);
    notice('Строим путь по улицам…');
    try {
      const result = await session.build(origin, byId('mode').value);
      if (job !== operation || !dialog.open) return;
      showPath(session.destination, result);
      const metres = Math.round(result.distance_meters);
      const distance = metres >= 1000 ? `${(metres / 1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} км` : `${metres} м`;
      byId('summary').textContent = `${distance} · ≈${Math.ceil(result.duration_seconds / 60)} мин`;
      byId('instructions').replaceChildren(...result.steps.map(step => Object.assign(document.createElement('li'), {textContent:step})));
      byId('steps').hidden = false;
      notice(result.steps.length ? 'Путь построен. Следуйте шагам ниже.' : 'Путь построен. Дорожный сервис не вернул шаги.');
    } catch (error) {
      if (job === operation && dialog.open) notice(error.message);
    } finally {
      if (job === operation) build.disabled = false;
    }
  });
})();
