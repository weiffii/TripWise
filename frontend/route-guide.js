(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const routeTab = el('trip-tab-route');
  const guideTab = el('trip-tab-guide');
  const routePanel = el('trip-panel-route');
  const guidePanel = el('trip-panel-guide');
  const listView = el('guide-list-view');
  const detail = el('guide-detail');
  const rail = el('guide-days');
  const list = el('guide-places');
  const player = el('guide-player');
  const playerSlot = el('guide-player-slot');
  const dismiss = el('guide-dismiss-player');
  const toggle = el('guide-play');
  const speed = el('guide-speed');
  const previous = el('guide-previous');
  const next = el('guide-next');
  const notification = el('guide-audio-status');
  const synth = window.speechSynthesis;
  const supported = Boolean(synth && window.SpeechSynthesisUtterance);
  let days = [], city = '', showOnMap, dayIndex = 0, detailKey = '', audioKey = '', lastCard;
  let length = 'short', state = 'idle', segmentIndex = 0, segments = [], utterance, generation = 0;

  function switchTab(isGuide, focus = false) {
    routePanel.hidden = isGuide;
    guidePanel.hidden = !isGuide;
    for (const [tab, active] of [[routeTab, !isGuide], [guideTab, isGuide]]) {
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    updatePlayer();
    if (focus) (isGuide ? guideTab : routeTab).focus();
  }
  for (const [tab, isGuide] of [[routeTab, false], [guideTab, true]]) {
    tab.addEventListener('click', () => switchTab(isGuide));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      switchTab(event.key === 'Home' ? false : event.key === 'End' ? true : !isGuide, true);
    });
  }

  const all = () => days.flatMap(day => day.stops);
  const stopByKey = key => key && all().find(stop => stop.key === key);
  const audioStop = () => stopByKey(audioKey);
  const detailStop = () => stopByKey(detailKey);
  const number = index => String(index + 1).padStart(2, '0');
  const artwork = stop => '/assets/card-' + (stop.story?.art || (
    /музей|галере|выставк/i.test(stop.name) ? 'museums' :
    /парк|сад|сквер/i.test(stop.name) ? 'nature' :
    /мост|панорам|башн/i.test(stop.name) ? 'viewpoints' : 'history'
  )) + '.svg';
  function texts(stop) {
    if (stop.story) return [stop.story.history, stop.story.detail];
    const plan = stop.dateLabel + '. «' + stop.name + '». ' +
      (stop.time ? 'Начало посещения по плану в ' + stop.time.replace(':', ' часов ') + '. ' : '') +
      (Number.isFinite(stop.minutes) ? 'На остановку отведено ' + stop.minutes + ' мин. ' : '') +
      'Проверенной исторической справки для этого места пока нет.';
    const following = days[stop.day]?.stops[stop.order + 1];
    return [plan, following ? 'Следующая экскурсионная остановка: «' + following.name + '». Откройте расписание и карту, чтобы посмотреть переходы и перерывы между этими местами.' : 'Это последняя экскурсионная остановка этого дня. В расписании можно проверить, остались ли переходы или перерывы.'];
  }
  function stopSpeech(reset = true) {
    generation++;
    if (utterance) synth.cancel();
    utterance = undefined;
    state = 'idle';
    if (reset) { segmentIndex = 0; segments = []; }
  }
  function updatePlayer() {
    const map = el('route-map-dialog');
    const inline = !guidePanel.hidden && !detail.hidden && !map.open;
    const active = audioStop();
    const stop = active || (inline ? detailStop() : null);
    const mount = inline ? playerSlot : map.open ? map : el('route-editor').parentElement;
    if (player.parentElement !== mount) {
      if (inline || map.open) mount.append(player);
      else el('route-editor').before(player);
    }
    player.classList.toggle('is-inline', inline);
    player.dataset.state = state;
    player.hidden = !stop;
    dismiss.hidden = inline && !active;
    dismiss.setAttribute('aria-label', inline ? 'Остановить рассказ' : 'Закрыть плеер');
    document.body.classList.toggle('has-guide-player', Boolean(active) && !inline);
    if (!stop) return;
    el('guide-now').textContent = stop.name;
    toggle.textContent = state === 'playing' ? 'Пауза' : state === 'paused' ? 'Продолжить' : 'Слушать';
    toggle.disabled = !supported;
    const stops = all();
    const position = stops.findIndex(item => item.key === stop.key);
    previous.disabled = position <= 0;
    next.disabled = position < 0 || position >= stops.length - 1;
    for (const button of document.querySelectorAll('[data-guide-length]')) {
      button.setAttribute('aria-pressed', String((!active || audioKey === detailKey) && button.dataset.guideLength === length));
    }
  }
  function speak() {
    const stop = audioStop();
    if (!stop || !supported) return;
    const voices = synth.getVoices().filter(voice => /^ru(?:[-_]|$)/i.test(voice.lang));
    if (!segments.length) {
      const paragraphs = texts(stop).slice(0, length === 'short' ? 1 : 2);
      segments = paragraphs.flatMap(paragraph => paragraph.match(/[^.!?]+[.!?]*/g) || [paragraph]);
    }
    const token = ++generation;
    const sentence = new SpeechSynthesisUtterance(segments[segmentIndex]);
    utterance = sentence;
    sentence.lang = 'ru-RU';
    sentence.rate = Number(speed.value);
    const voice = voices.find(voice => voice.localService) || voices[0];
    if (voice) sentence.voice = voice;
    sentence.onend = () => {
      if (token !== generation) return;
      utterance = undefined;
      if (++segmentIndex < segments.length) speak();
      else { stopSpeech(); notification.textContent = 'Рассказ завершён.'; updatePlayer(); }
    };
    sentence.onerror = () => {
      if (token !== generation) return;
      utterance = undefined;
      stopSpeech();
      notification.textContent = 'Озвучивание не запустилось. Проверьте, доступен ли русский голос на устройстве. Текст рассказа есть в карточке.';
      updatePlayer();
    };
    state = 'playing';
    notification.textContent = '';
    // Некоторые браузеры оставляют очередь на паузе после cancel().
    if (synth.paused) synth.resume();
    synth.speak(sentence);
    updatePlayer();
  }

  toggle.addEventListener('click', () => {
    if (!supported) return;
    if (state === 'playing') { synth.pause(); state = 'paused'; updatePlayer(); }
    else if (state === 'paused') { synth.resume(); state = 'playing'; updatePlayer(); }
    else if (!audioKey && detailStop()) chooseAudio(detailStop(), length);
    else speak();
  });
  speed.addEventListener('change', () => {
    const wasPlaying = state === 'playing';
    const wasPaused = state === 'paused';
    stopSpeech(false);
    if (wasPlaying) speak();
    else if (wasPaused) notification.textContent = 'Скорость изменена. Нажмите «Слушать», чтобы продолжить.';
    updatePlayer();
  });
  function chooseAudio(stop, format) {
    const same = stop.key === audioKey && format === length;
    if (same && state === 'playing') { toggle.click(); return; }
    if (same && state === 'paused') { toggle.click(); return; }
    stopSpeech();
    audioKey = stop.key;
    length = format;
    notification.textContent = supported ? '' : 'Этот браузер не поддерживает озвучивание. Можно прочитать рассказ ниже.';
    updatePlayer();
    speak();
  }

  function renderList(focus = false) {
    listView.hidden = false;
    detail.hidden = true;
    detailKey = '';
    rail.replaceChildren();
    list.replaceChildren();
    days.forEach((day, index) => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.setAttribute('aria-pressed', String(index === dayIndex));
      tab.textContent = day.label;
      tab.onclick = () => {
        // Сохраняем горизонтальную позицию ряда дней.
        dayIndex = index;
        showDay();
      };
      rail.append(tab);
    });
    showDay();
    updatePlayer();
    if (focus) (lastCard && list.querySelector('[data-guide-key="' + CSS.escape(lastCard) + '"]') || rail.children[dayIndex])?.focus();
  }

  function showDay() {
    [...rail.children].forEach((tab, index) => tab.setAttribute('aria-pressed', String(index === dayIndex)));
    const stops = days[dayIndex]?.stops || [];
    el('guide-empty').hidden = stops.length > 0;
    list.replaceChildren();
    for (const stop of stops) {
      const li = document.createElement('li');
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'guide-card';
      card.dataset.guideKey = stop.key;
      const art = new Image();
      art.src = artwork(stop);
      art.alt = '';
      const copy = document.createElement('span');
      const tag = document.createElement('small');
      const title = document.createElement('strong');
      tag.textContent = 'Остановка ' + number(stop.order) + (stop.time ? ' · ' + stop.time : '');
      title.textContent = stop.name;
      copy.append(tag,title);
      const arrow = document.createElement('span');
      arrow.textContent = '→';
      arrow.setAttribute('aria-hidden', 'true');
      card.append(art,copy,arrow);
      card.onclick = () => { lastCard = stop.key; renderDetail(stop, true); };
      li.append(card);
      list.append(li);
    }
  }

  function renderDetail(stop, focus = false) {
    detailKey = stop.key;
    listView.hidden = true;
    detail.hidden = false;
    el('guide-detail-art').src = artwork(stop);
    el('guide-detail-title').textContent = stop.name;
    el('guide-detail-kicker').textContent = stop.dateLabel + ' · Остановка ' + number(stop.order);
    const [history, note] = texts(stop);
    el('guide-history-title').textContent = stop.story ? 'Историческая справка' : 'Остановка в вашем маршруте';
    el('guide-note-title').textContent = stop.story ? 'На что обратить внимание' : 'Что дальше';
    el('guide-history').textContent = history;
    el('guide-note').textContent = note;
    const source = el('guide-source');
    source.hidden = !stop.story;
    if (stop.story) source.href = stop.story.source;
    for (const button of document.querySelectorAll('[data-guide-length]')) {
      button.disabled = !supported;
      button.onclick = () => chooseAudio(stop, button.dataset.guideLength);
      button.setAttribute('aria-pressed', String(audioKey === stop.key && button.dataset.guideLength === length));
    }
    el('guide-show-map').hidden = !stop.canMap;
    el('guide-show-map').onclick = () => {
      switchTab(false);
      showOnMap(stop);
    };
    if (!supported) el('guide-voice-hint').hidden = false;
    updatePlayer();
    if (focus) el('guide-detail-title').focus();
  }
  function move(delta) {
    const stops = all();
    const current = audioKey || detailKey;
    const target = stops[stops.findIndex(stop => stop.key === current) + delta];
    if (!target) return;
    const wasPlaying = state === 'playing';
    stopSpeech();
    if (audioKey) audioKey = target.key;
    notification.textContent = '';
    if (!guidePanel.hidden) {
      dayIndex = target.day;
      lastCard = target.key;
      renderDetail(target);
    }
    updatePlayer();
    if (wasPlaying) speak();
  }
  previous.onclick = () => move(-1);
  next.onclick = () => move(1);
  el('guide-back-list').onclick = () => renderList(true);
  dismiss.onclick = () => {
    stopSpeech();
    audioKey = '';
    notification.textContent = '';
    updatePlayer();
    if (!guidePanel.hidden && !detail.hidden) el('guide-detail-title').focus();
    else routeTab.focus();
  };
  window.addEventListener('pagehide', () => { stopSpeech(); updatePlayer(); });
  el('guide-voice-hint').hidden = supported;

  window.TripWiseGuide = {
    syncPlayer:updatePlayer,
    update(model) {
      city = model.city;
      showOnMap = model.showOnMap;
      const priorDate = days[dayIndex]?.date;
      days = model.days.map((day, index) => ({...day, stops: day.stops.map((stop, order) => ({
        ...stop, key: [day.date,stop.id,stop.occurrence].join('/'), day:index, order,
        dateLabel:day.label, story:window.tripWiseGuideStory(city, stop),
      }))}));
      dayIndex = Math.max(0, days.findIndex(day => day.date === priorDate));
      if (audioKey && !audioStop()) {
        stopSpeech();
        audioKey = '';
        el('guide-notice').textContent = 'Маршрут обновлён. Место, которое звучало, больше не входит в этот день.';
      }
      const opened = all().find(stop => stop.key === detailKey);
      if (opened) {
        dayIndex = opened.day;
        renderDetail(opened);
      } else renderList();
      updatePlayer();
      el('trip-result-tabs').hidden = false;
    }
  };
})();
