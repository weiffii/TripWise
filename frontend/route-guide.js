(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const routeTab = el('trip-tab-route');
  const guidePanel = el('trip-panel-guide');
  const tabs = [routeTab, el('trip-tab-guide'), el('trip-tab-tips')];
  const panels = tabs.map(tab => el(tab.getAttribute('aria-controls')));
  const listView = el('guide-list-view');
  const detail = el('guide-detail');
  const rail = el('guide-days');
  const list = el('guide-places');
  const listStatus = el('guide-availability');
  const retryList = el('guide-retry-list');
  const retryDetail = el('guide-retry-detail');
  const researchButton = el('guide-research');
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
  const enhancedVoice = /\b(?:natural|neural|premium|enhanced|wavenet)\b|естествен|нейрон|улучшенн/iu;
  const speechChunkLength = 220;
  const statuses = new Map();
  const stories = new Map();
  const requests = new Map();
  const prefetchedByDay = new Map();
  const localArtwork = new Set(['museums', 'nature', 'viewpoints', 'history', 'entertainment', 'shopping', 'unusual']);
  let days = [], city = '', showOnMap, dayIndex = 0, detailKey = '', audioKey = '', lastCard;
  let length = 'short', state = 'idle', segmentIndex = 0, segments = [], utterance, generation = 0;
  let availabilityRequest, availabilityError, prefetchTimer, backgroundRequest;
  let detailVersion = 0, audioFact = null, audioStory = null;

  function switchTab(index, focus = false) {
    tabs.forEach((tab, position) => {
      const active = position === index;
      panels[position].hidden = !active;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    if (index === 1) checkAvailability();
    else cancelPrefetch();
    updatePlayer();
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener('click', () => switchTab(index));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const offset = event.key === 'ArrowRight' ? 1 : -1;
      switchTab(event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + offset + tabs.length) % tabs.length, true);
    });
  });

  const all = () => days.flatMap(day => day.stops);
  const selectedStops = () => days[dayIndex]?.stops || [];
  const selectedDayKey = () => city + '/' + days[dayIndex]?.date;
  const stopByKey = key => key && all().find(stop => stop.key === key);
  const audioStop = () => stopByKey(audioKey);
  const detailStop = () => stopByKey(detailKey);
  const contentKey = (destination, id) => destination + '/' + id;
  const storyFor = stop => stop && (stop.story || stories.get(stop.contentKey));
  const statusFor = stop => storyFor(stop)?.status || statuses.get(stop.contentKey)?.status || 'unknown';
  const researchKey = stop => stop.contentKey + '/research';
  const availableStops = () => all().filter(stop => statusFor(stop) === 'available');
  const number = index => String(index + 1).padStart(2, '0');
  const catalogServices = destination => destination === 'moscow' || destination === 'shanghai';
  const artwork = stop => {
    const category = storyFor(stop)?.art || statuses.get(stop.contentKey)?.category;
    return '/assets/card-' + (localArtwork.has(category) ? category : 'history') + '.svg';
  };
  async function requestJson(url, options) {
    const response = await fetch(url, {credentials:'same-origin', ...options});
    if (!response.ok) throw new Error('Guide unavailable');
    return response.json();
  }
  function checkAvailability() {
    if (!catalogServices(city) || guidePanel.hidden) return;
    const day = selectedDayKey();
    const failed = availabilityError === day;
    retryList.hidden = !failed;
    if (failed) {
      listStatus.textContent = 'Не удалось проверить список рассказов. Можно открыть отдельное место.';
      return;
    }
    const destination = city;
    const ids = [...new Set(selectedStops().filter(stop => !statuses.has(stop.contentKey) && !storyFor(stop)).map(stop => stop.id))];
    listStatus.textContent = ids.length ? 'Проверяем, для каких мест есть рассказы…' : '';
    if (!ids.length) { schedulePrefetch(); return; }
    if (availabilityRequest) return;
    availabilityRequest = (async () => {
      for (let index = 0; index < ids.length; index += 256) {
        const batch = ids.slice(index, index + 256);
        const data = await requestJson('/api/guide/availability?city=' + encodeURIComponent(destination), {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({poi_ids:batch}),
        });
        const items = new Map(data.items.map(item => [item.poi_id, item]));
        for (const id of batch) statuses.set(contentKey(destination, id), items.get(id) || {status:'unknown'});
        if (guidePanel.hidden || selectedDayKey() !== day) return;
      }
    })().catch(() => {
      if (selectedDayKey() === day) availabilityError = day;
    }).finally(() => {
      availabilityRequest = undefined;
      if (!listView.hidden) showDay();
      updatePlayer();
      checkAvailability();
    });
  }
  retryList.onclick = () => {
    availabilityError = undefined;
    checkAvailability();
  };
  function loadStory(stop, research = false) {
    const saved = storyFor(stop);
    if (saved && (!research || saved.coverage !== 'catalog' || saved.research_status === 'unavailable')) return Promise.resolve(saved);
    if (!catalogServices(stop.city) || ['unavailable', 'excluded'].includes(statusFor(stop))) {
      return Promise.resolve({status:statusFor(stop) === 'excluded' ? 'excluded' : 'unavailable'});
    }
    const key = research ? researchKey(stop) : stop.contentKey + '/quick';
    if (!requests.has(key)) {
      const url = '/api/guide/poi/' + encodeURIComponent(stop.id) + '?city=' + encodeURIComponent(stop.city) + (research ? '' : '&quick=true');
      const pending = requestJson(url).then(story => {
        if (story.poi_id !== stop.id || story.city !== stop.city) throw new Error('Unexpected guide');
        const current = storyFor(stop);
        if (current?.status === 'available' && (!research || story.status !== 'available' ||
          story.coverage === 'catalog' && current.coverage !== 'catalog')) return current;
        if (story.status === 'available' || story.status === 'excluded' || research && story.status === 'unavailable') {
          stories.set(stop.contentKey, story);
        }
        return story;
      }).catch(() => {
        const current = storyFor(stop);
        if (current?.coverage === 'catalog' && research) return {...current, research_status:'temporarily_unavailable'};
        return current || {status:'temporarily_unavailable'};
      }).finally(() => requests.delete(key));
      requests.set(key, pending);
    }
    return requests.get(key);
  }
  async function openStory(stop) {
    if (!statuses.has(stop.contentKey) && availabilityRequest) await availabilityRequest;
    if (statuses.get(stop.contentKey)?.status === 'unknown' && statusFor(stop) === 'unknown') return loadStory(stop, true);
    const story = await loadStory(stop);
    if (story.status === 'unavailable' && statusFor(stop) === 'unknown') return loadStory(stop, true);
    return story;
  }
  function canPrefetch(stop) {
    const availability = statuses.get(stop.contentKey);
    const story = storyFor(stop);
    if (requests.has(researchKey(stop)) || !availability) return false;
    if (story) return story.coverage === 'catalog' && story.research_status === 'pending' && availability.needs_research;
    return availability.status === 'unknown' || availability.needs_research;
  }
  function cancelPrefetch() {
    clearTimeout(prefetchTimer);
    prefetchTimer = undefined;
  }
  function schedulePrefetch() {
    if (prefetchTimer !== undefined || backgroundRequest || guidePanel.hidden || listView.hidden) return;
    const day = selectedDayKey();
    const attempted = prefetchedByDay.get(day) || new Set();
    if (attempted.size >= 2) return;
    const candidate = () => selectedStops().find(stop => !attempted.has(stop.contentKey) && canPrefetch(stop));
    if (!candidate()) return;
    prefetchTimer = setTimeout(() => {
      prefetchTimer = undefined;
      if (guidePanel.hidden || listView.hidden || selectedDayKey() !== day) return;
      const stop = candidate();
      if (!stop) return;
      attempted.add(stop.contentKey);
      prefetchedByDay.set(day, attempted);
      // A click on the same POI can share this in-flight fetch. Dropping the
      // queued work leaves no extra request when the tab or date changes.
      backgroundRequest = loadStory(stop, true).then(story => {
        if (detailKey === stop.key && !guidePanel.hidden && story.status === 'available' && storyFor(stop) === story) {
          showStory(stop, story, true);
        }
      }).finally(() => {
        backgroundRequest = undefined;
        if (!guidePanel.hidden && !listView.hidden) showDay();
        updatePlayer();
        schedulePrefetch();
      });
    }, 400);
  }
  function stopSpeech(reset = true) {
    generation++;
    if (utterance) synth.cancel();
    utterance = undefined;
    state = 'idle';
    if (reset) { segmentIndex = 0; segments = []; audioStory = null; }
  }
  function updatePlayer() {
    const map = el('route-map-dialog');
    const inline = !guidePanel.hidden && !detail.hidden && !el('guide-listen').hidden && !map.open;
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
    el('guide-now').textContent = audioFact ? stop.name + ' · ' + audioFact.title : stop.name;
    toggle.textContent = {playing:'Пауза', paused:'Продолжить', loading:'Загрузка…'}[state] || 'Слушать';
    toggle.disabled = !supported || state === 'loading' || storyFor(stop)?.status !== 'available';
    const stops = availableStops();
    const position = stops.findIndex(item => item.key === stop.key);
    previous.disabled = position <= 0;
    next.disabled = position < 0 || position >= stops.length - 1;
    for (const button of document.querySelectorAll('[data-guide-length]')) {
      button.setAttribute('aria-pressed', String(!audioFact && (!active || audioKey === detailKey) && button.dataset.guideLength === length));
    }
  }

  function pickVoice(language) {
    const locale = value => value.replace(/_/g, '-').toLowerCase();
    const wanted = locale(language);
    const base = wanted.split('-')[0];
    const matching = synth.getVoices().filter(voice => locale(voice.lang).split('-')[0] === base);
    const score = voice => Number(enhancedVoice.test(voice.name + ' ' + voice.voiceURI)) * 8
      + Number(locale(voice.lang) === wanted) * 4 + (voice.default ? 2 : 0) + (voice.localService ? 1 : 0);
    return matching.sort((a, b) => score(b) - score(a))[0];
  }

  function speechChunks(text, language) {
    const chunks = [];
    const segmenter = typeof Intl.Segmenter === 'function'
      ? new Intl.Segmenter(language.split('-')[0], {granularity:'sentence'}) : null;
    for (const paragraph of text.trim().split(/\n\s*\n/)) {
      const parts = segmenter ? Array.from(segmenter.segment(paragraph), item => item.segment) : [paragraph];
      let chunk = '';
      for (const part of parts) {
        const sentence = part.trim();
        if (!sentence) continue;
        if (chunk && chunk.length + sentence.length + 1 > speechChunkLength) {
          chunks.push(chunk);
          chunk = '';
        }
        if (sentence.length <= speechChunkLength) {
          chunk = chunk ? chunk + ' ' + sentence : sentence;
          continue;
        }
        // Leave ordinary sentences intact. Split unusually long sentences at
        // word boundaries so all browsers can finish the utterance.
        for (const word of sentence.split(/\s+/)) {
          if (chunk && chunk.length + word.length + 1 > speechChunkLength) {
            chunks.push(chunk);
            chunk = '';
          }
          chunk = chunk ? chunk + ' ' + word : word;
        }
      }
      if (chunk) chunks.push(chunk);
    }
    return chunks;
  }

  function speak() {
    const stop = audioStop();
    if (!stop || !supported) return;
    const story = audioStory || storyFor(stop);
    if (!story || story.status !== 'available') return;
    audioStory = story;
    const language = /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/i.test(story.language || '') ? story.language : 'ru';
    const locale = language.toLowerCase() === 'ru' ? 'ru-RU' : language;
    if (!segments.length) {
      const text = audioFact ? audioFact.text : length === 'long' ? story.full_text || story.short_text : story.short_text || story.full_text;
      if (typeof text !== 'string' || !text.trim()) return;
      segments = speechChunks(text, locale);
    }
    const token = ++generation;
    const sentence = new window.SpeechSynthesisUtterance(segments[segmentIndex]);
    utterance = sentence;
    sentence.lang = locale;
    sentence.rate = 0.94 * (Number(speed.value) || 1);
    const voice = pickVoice(locale);
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
      notification.textContent = 'Озвучивание не запустилось. Проверьте, доступен ли нужный голос на устройстве. Текст можно прочитать в карточке.';
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
    if (!supported || state === 'loading') return;
    if (state === 'playing') { synth.pause(); state = 'paused'; updatePlayer(); }
    else if (state === 'paused') { synth.resume(); state = 'playing'; updatePlayer(); }
    else if (!audioKey && detailStop()) chooseAudio(detailStop(), length);
    else speak();
  });
  speed.addEventListener('change', () => {
    if (state === 'loading') return;
    const wasPlaying = state === 'playing';
    const wasPaused = state === 'paused';
    stopSpeech(false);
    if (wasPlaying) speak();
    else if (wasPaused) notification.textContent = 'Скорость изменена. Нажмите «Слушать», чтобы продолжить.';
    updatePlayer();
  });
  function chooseAudio(stop, format, fact = null) {
    const same = stop.key === audioKey && format === length && fact?.text === audioFact?.text;
    if (same && state === 'playing') { toggle.click(); return; }
    if (same && state === 'paused') { toggle.click(); return; }
    stopSpeech();
    audioKey = stop.key;
    audioFact = fact;
    length = format;
    notification.textContent = supported ? '' : 'Этот браузер не поддерживает озвучивание. Можно прочитать рассказ ниже.';
    updatePlayer();
    speak();
  }

  function renderList(focus = false) {
    detailVersion++;
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
        cancelPrefetch();
        dayIndex = index;
        showDay();
        checkAvailability();
      };
      rail.append(tab);
    });
    showDay();
    checkAvailability();
    updatePlayer();
    if (focus) (lastCard && listView.querySelector('[data-guide-key="' + CSS.escape(lastCard) + '"]') || rail.children[dayIndex])?.focus();
  }

  function showDay() {
    [...rail.children].forEach((tab, index) => tab.setAttribute('aria-pressed', String(index === dayIndex)));
    const stops = selectedStops().filter(stop => statusFor(stop) !== 'excluded');
    const activeCard = document.activeElement?.dataset.guideKey;
    const empty = el('guide-empty');
    empty.textContent = 'На этот день нет остановок для аудиогида.';
    empty.hidden = stops.length > 0;
    list.replaceChildren(...stops.map(placeCard));
    if (activeCard) (listView.querySelector('[data-guide-key="' + CSS.escape(activeCard) + '"]') || rail.children[dayIndex])?.focus();
  }

  function placeCard(stop) {
    const li = document.createElement('li');
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'guide-card';
    card.dataset.guideKey = stop.key;
    const art = new Image();
    art.src = artwork(stop);
    art.alt = '';
    art.loading = 'lazy';
    const copy = document.createElement('span');
    const tag = document.createElement('small');
    const title = document.createElement('strong');
    const label = {available:'Рассказ доступен', unavailable:'Рассказ пока не найден', unknown:'Рассказ можно поискать'};
    tag.textContent = 'Остановка ' + number(stop.order) + ' · ' + label[statusFor(stop)];
    title.textContent = stop.name;
    copy.append(tag,title);
    const arrow = document.createElement('span');
    arrow.textContent = '→';
    arrow.setAttribute('aria-hidden', 'true');
    card.append(art,copy,arrow);
    card.onclick = () => { lastCard = stop.key; renderDetail(stop, true); };
    li.append(card);
    return li;
  }

  function externalUrl(value) {
    if (typeof value !== 'string') return '';
    try {
      const url = new URL(value);
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : '';
    } catch { return ''; }
  }
  function addLink(container, title, url) {
    const href = externalUrl(url);
    if (!href) { container.append(title); return; }
    const link = document.createElement('a');
    link.href = href;
    link.textContent = title;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    container.append(link);
  }
  function renderImage(stop, image) {
    const element = el('guide-detail-art');
    const credit = el('guide-image-credit');
    const photo = externalUrl(image?.url);
    credit.replaceChildren();
    credit.hidden = !photo;
    element.onerror = null;
    element.alt = photo ? stop.name : '';
    element.src = photo || artwork(stop);
    if (!photo) return;
    addLink(credit, 'Источник фото', image.source_url);
    if (image.author) credit.append(' · ' + image.author);
    if (image.license) {
      credit.append(' · ');
      addLink(credit, image.license, image.license_url);
    }
    element.onerror = () => {
      if (detailKey === stop.key) renderImage(stop, null);
    };
  }
  function renderFacts(stop, facts) {
    const root = el('guide-fact-list');
    root.replaceChildren();
    for (const fact of facts || []) {
      if (!fact.title || !fact.text) continue;
      const section = document.createElement('details');
      const title = document.createElement('summary');
      title.textContent = fact.title;
      section.append(title);
      for (const text of [fact.text, fact.detail]) {
        if (!text) continue;
        const paragraph = document.createElement('p');
        paragraph.textContent = text;
        section.append(paragraph);
      }
      if (supported) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'outline-button';
        button.textContent = 'Озвучить факт';
        button.setAttribute('aria-label', 'Озвучить факт: ' + fact.title);
        button.onclick = () => chooseAudio(stop, 'short', {title:fact.title,
          text:[fact.title, fact.text, fact.detail].filter(Boolean).map(part => {
            const text = part.trim();
            return /[.!?…][»”"]?$/.test(text) ? text : text + '.';
          }).join(' ')});
        section.append(button);
      }
      root.append(section);
    }
    el('guide-facts').hidden = !root.children.length;
  }
  function renderSources(sources) {
    const list = el('guide-source-list');
    list.replaceChildren();
    for (const source of sources || []) {
      if (!source.title || !externalUrl(source.url)) continue;
      const item = document.createElement('li');
      addLink(item, source.title, source.url);
      if (source.attribution && source.attribution !== source.title) item.append(' · ' + source.attribution);
      if (source.license) {
        item.append(' · ');
        addLink(item, source.license, source.license_url);
      }
      list.append(item);
    }
    el('guide-sources').hidden = !list.children.length;
  }
  function showStory(stop, story, preserveReading = false) {
    const available = story?.status === 'available' && Boolean(story.short_text || story.full_text);
    const catalog = available && story.coverage === 'catalog';
    const detailStatus = el('guide-detail-status');
    const short = available ? story.short_text || story.full_text : '';
    const full = available && story.full_text !== short ? story.full_text : '';
    const additional = full?.startsWith(short) ? full.slice(short.length).trim() : full;
    const more = el('guide-full-story');
    renderImage(stop, available ? story.image : null);
    el('guide-reading').hidden = !available;
    el('guide-listen').hidden = !available || !supported;
    el('guide-voice-hint').hidden = !available || supported;
    el('guide-history').textContent = short;
    el('guide-note').textContent = additional || '';
    more.hidden = !additional;
    if (!preserveReading) more.open = false;
    renderFacts(stop, available ? story.facts : []);
    renderSources(available ? story.sources : []);
    const messages = {
      temporarily_unavailable:'Источник временно недоступен. Попробуйте открыть рассказ ещё раз.',
      unavailable:'Проверенный рассказ для этого места пока не найден.',
      excluded:'Это место не входит в аудиогид.',
    };
    const researchMessages = {
      temporarily_unavailable:'Подробный источник временно недоступен. Краткий гид можно прочитать и послушать.',
      unavailable:'Подробную историю по проверенным источникам пока не удалось найти.',
    };
    detailStatus.textContent = available ? (catalog ? researchMessages[story.research_status] || '' :
      story.stale ? 'Показываем сохранённую версию рассказа.' : '') :
      (story ? messages[story.status] || messages.unavailable : 'Загружаем рассказ…');
    el('guide-catalog-label').hidden = !catalog;
    researchButton.hidden = !catalog || story.research_status === 'unavailable';
    researchButton.disabled = false;
    researchButton.onclick = async () => {
      const version = detailVersion;
      researchButton.disabled = true;
      detailStatus.textContent = 'Ищем подробную историю…';
      const researched = await loadStory(stop, true);
      if (version === detailVersion) showStory(stop, researched, true);
    };
    detail.setAttribute('aria-busy', String(!story));
    retryDetail.hidden = story?.status !== 'temporarily_unavailable';
    retryDetail.onclick = () => renderDetail(stop, true);
    for (const button of document.querySelectorAll('[data-guide-length]')) {
      button.disabled = !supported || !available || button.dataset.guideLength === 'long' && !full;
      button.onclick = () => chooseAudio(stop, button.dataset.guideLength);
    }
    updatePlayer();
  }

  async function renderDetail(stop, focus = false) {
    cancelPrefetch();
    const version = ++detailVersion;
    detailKey = stop.key;
    listView.hidden = true;
    detail.hidden = false;
    el('guide-detail-title').textContent = stop.name;
    el('guide-detail-kicker').textContent = stop.dateLabel + ' · Остановка ' + number(stop.order);
    el('guide-show-map').hidden = !stop.canMap;
    el('guide-show-map').onclick = () => {
      switchTab(0);
      showOnMap(stop);
    };
    const status = statusFor(stop);
    const saved = storyFor(stop) || (['unavailable', 'excluded'].includes(status) ? {status} : null);
    showStory(stop, saved);
    if (focus) el('guide-detail-title').focus();
    if (saved) return;
    const story = await openStory(stop);
    if (version === detailVersion) showStory(stop, story);
  }
  async function move(delta) {
    const stops = availableStops();
    const current = audioKey || detailKey;
    const position = stops.findIndex(stop => stop.key === current);
    const target = position >= 0 && stops[position + delta];
    if (!target) return;
    const wasPlaying = state === 'playing' || state === 'loading';
    stopSpeech();
    audioFact = null;
    notification.textContent = '';
    if (!guidePanel.hidden || detailKey) {
      dayIndex = target.day;
      lastCard = target.key;
      renderDetail(target);
    }
    if (!audioKey) return;
    audioKey = target.key;
    if (storyFor(target)) {
      if (wasPlaying) speak();
      else updatePlayer();
      return;
    }
    state = 'loading';
    updatePlayer();
    const token = generation;
    const story = await openStory(target);
    if (token !== generation || audioKey !== target.key) return;
    state = 'idle';
    if (story.status !== 'available') notification.textContent = 'Рассказ не загрузился. Откройте карточку места, чтобы повторить попытку.';
    if (wasPlaying && story.status === 'available') speak();
    else updatePlayer();
  }
  previous.onclick = () => move(-1);
  next.onclick = () => move(1);
  el('guide-back-list').onclick = () => renderList(true);
  dismiss.onclick = () => {
    stopSpeech();
    audioKey = '';
    audioFact = null;
    notification.textContent = '';
    updatePlayer();
    if (!guidePanel.hidden && !detail.hidden) el('guide-detail-title').focus();
    else routeTab.focus();
  };
  window.addEventListener('pagehide', () => { cancelPrefetch(); stopSpeech(); updatePlayer(); });
  el('guide-voice-hint').hidden = true;

  window.TripWiseGuide = {
    syncPlayer:updatePlayer,
    update(model) {
      cancelPrefetch();
      if (city !== model.city) {
        availabilityError = undefined;
        retryList.hidden = true;
        listStatus.textContent = '';
      }
      city = model.city;
      showOnMap = model.showOnMap;
      const priorDate = days[dayIndex]?.date;
      days = model.days.map((day, index) => ({...day, stops: day.stops.map((stop, order) => ({
        ...stop, key: [city,day.date,stop.id,stop.occurrence].join('/'), day:index, order, city,
        contentKey:contentKey(city, stop.id),
        dateLabel:day.label, story:window.tripWiseGuideStory(city, stop),
      }))}));
      dayIndex = Math.max(0, days.findIndex(day => day.date === priorDate));
      if (audioKey && !audioStop()) {
        stopSpeech();
        audioKey = '';
        audioFact = null;
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
