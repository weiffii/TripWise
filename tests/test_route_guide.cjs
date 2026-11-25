const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = readFileSync(path.join(root, 'frontend/route-guide.js'), 'utf8');
const localStories = readFileSync(path.join(root, 'frontend/guide-stories.js'), 'utf8');
const template = readFileSync(path.join(root, 'backend/pages/result.html'), 'utf8');

function setup({voice = true, voices = []} = {}) {
  let document;
  class Element {
    children = [];
    dataset = {};
    attributes = new Map();
    listeners = new Map();
    classList = {toggle() {}};
    hidden = false;
    text = '';
    append(...nodes) {
      for (const node of nodes) {
        if (typeof node !== 'string') {
          if (node.parentElement) node.parentElement.children = node.parentElement.children.filter(child => child !== node);
          node.parentElement = this;
        }
        this.children.push(node);
      }
    }
    before(node) { this.parentElement.append(node); }
    replaceChildren(...nodes) { this.children = []; this.text = ''; this.append(...nodes); }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name); }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    set textContent(value) { this.children = []; this.text = String(value); }
    get textContent() { return this.text + this.children.map(node => node.textContent ?? node).join(''); }
    focus() { document.activeElement = this; }
    click() { this.listeners.get('click')?.(); return this.onclick?.(); }
    querySelector(selector) {
      const key = selector.match(/^\[data-guide-key="(.+)"\]$/)?.[1];
      for (const child of this.children) {
        if (child.dataset?.guideKey === key) return child;
        const nested = child.querySelector?.(selector);
        if (nested) return nested;
      }
      return null;
    }
  }
  const elements = Object.fromEntries([...template.matchAll(/<[^<>]+\bid="([^"]+)"[^<>]*>/g)]
    .map(([html, id]) => [id, Object.assign(new Element(), {hidden:/\bhidden\b/.test(html)})]));
  const get = id => elements[id];
  const formats = ['short','long'].map(length => Object.assign(new Element(), {dataset:{guideLength:length}}));
  const body = new Element();
  body.append(get('route-editor'), get('guide-player'));
  get('guide-list-view').append(get('guide-places'));
  for (const name of ['route', 'guide', 'tips']) get('trip-tab-' + name).setAttribute('aria-controls', 'trip-panel-' + name);
  get('guide-speed').value = '1';
  document = {body, getElementById:get, createElement:() => new Element(), querySelectorAll:() => formats};
  const spoken = [];
  const synth = {
    getVoices:() => voices, paused:false,
    speak:utterance => spoken.push(utterance),
    pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { spoken.push('cancelled'); },
  };
  class Utterance { constructor(text) { this.text = text; } }
  const window = {addEventListener() {}};
  if (voice) Object.assign(window, {speechSynthesis:synth, SpeechSynthesisUtterance:Utterance});
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({window, document, Image:Element, URL, CSS:{escape:value => value},
    SpeechSynthesisUtterance:Utterance,
    setTimeout:callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout:id => timers.delete(id),
    fetch:(url, options) => new Promise(resolve => requests.push({url, options, resolve:(body, ok = true) =>
      resolve({ok, json:async () => body})})),
  });
  vm.runInContext(localStories, context);
  vm.runInContext(source, context);
  const guide = window.TripWiseGuide;
  const show = [];
  const model = (stops, city = 'moscow') => ({city, showOnMap:stop => show.push(stop), days:[
    {date:'2026-11-25',label:'День 1',stops:stops.map((id, index) => ({id,name:'Место ' + id,occurrence:1,canMap:true,mapIndex:index}))},
  ]});
  const runTimers = () => {
    for (const [id, callback] of [...timers]) {
      timers.delete(id);
      callback();
    }
  };
  return {get, guide, spoken, show, requests, formats, model, runTimers};
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const statuses = rows => ({items:rows.map(([poi_id, status, needs_research]) => ({poi_id,status,category:'museums',needs_research}))});
const cards = node => node.children.map(row => row.children[0]);
const cardIds = node => cards(node).map(card => card.dataset.guideKey.split('/').at(-2));
const story = (id, fields = {}) => ({poi_id:id, city:'moscow', status:'available', short_text:'История ' + id + '.',
  full_text:'Подробный рассказ ' + id + '.', language:'ru', facts:[], sources:[], ...fields});

test('guide checks the visible list lazily and keeps eligible stops in route order', async () => {
  const {get, guide, requests, model} = setup();
  guide.update(model(['a','b','c','d','e']));
  assert.equal(requests.length, 0);
  get('trip-tab-guide').click();
  assert.match(get('guide-availability').textContent, /Проверяем/);
  assert.equal(requests[0].url, '/api/guide/availability?city=moscow');
  assert.equal(requests[0].options.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(requests[0].options.body), {poi_ids:['a','b','c','d','e']});
  requests[0].resolve(statuses([['d','available'],['c','excluded'],['a','unknown'],['e','unavailable'],['b','available']]));
  await settle();
  assert.deepEqual(cardIds(get('guide-places')), ['a','b','d','e']);
  assert.match(cards(get('guide-places'))[0].textContent, /Рассказ можно поискать/);
  assert.match(cards(get('guide-places')).at(-1).textContent, /Рассказ пока не найден/);
  cards(get('guide-places')).at(-1).click();
  assert.match(get('guide-detail-status').textContent, /пока не найден/);
  assert.equal(get('guide-listen').hidden, true);
  get('guide-back-list').click();
  assert.equal(requests.length, 1);
  guide.update(model(['d','f']));
  assert.deepEqual(JSON.parse(requests[1].options.body), {poi_ids:['f']});
  requests[1].resolve(statuses([['f','unknown']]));
  await settle();
  assert.deepEqual(cardIds(get('guide-places')), ['d','f']);
});

test('selected guide lazily renders facts, image attribution and safe sources', async () => {
  const {get, guide, requests, spoken, show, model} = setup();
  guide.update(model(['a']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available']]));
  await settle();
  cards(get('guide-places'))[0].click();
  assert.match(get('guide-detail-status').textContent, /Загружаем/);
  assert.equal(requests[1].url, '/api/guide/poi/a?city=moscow&quick=true');
  requests[1].resolve(story('a', {
    facts:[{title:'Факт',text:'Проверенный факт.',detail:'Подробности.'}],
    image:{url:'https://upload.wikimedia.org/photo.jpg',author:'Автор',source_url:'https://commons.wikimedia.org/image',license:'CC BY',license_url:'https://creativecommons.org/'},
    sources:[{title:'Справочник',url:'https://example.org',license:'CC0',license_url:'javascript:alert(1)'}, {title:'Bad url',url:'javascript:alert(1)'}],
  }));
  await settle();
  assert.match(get('guide-history').textContent, /История a\./);
  assert.equal(get('guide-image-credit').textContent, 'Источник фото · Автор · CC BY');
  assert.equal(get('guide-detail-art').src,'https://upload.wikimedia.org/photo.jpg');
  assert.equal(get('guide-source-list').children.length, 1);
  assert.deepEqual(get('guide-source-list').children[0].children.map(node => node.href).filter(Boolean), ['https://example.org/']);
  const fact = get('guide-fact-list').children[0];
  assert.equal(fact.children[1].textContent,'Проверенный факт.');
  fact.children.at(-1).click();
  assert.equal(spoken[0].text, 'Факт. Проверенный факт. Подробности.');
  assert.equal(spoken[0].lang, 'ru-RU');
  get('guide-show-map').click();
  assert.equal(show[0].mapIndex, 0);
  assert.equal(get('guide-player').hidden, false);
  assert.equal(spoken.includes('cancelled'), false);
  get('trip-tab-guide').click();
  get('guide-back-list').click();
  cards(get('guide-places'))[0].click();
  assert.equal(requests.length, 2);
  guide.update(model([]));
  assert.equal(get('guide-player').hidden, true);
  assert.equal(spoken.at(-1), 'cancelled');
});

test('unavailable details do not speak a fabricated placeholder and can be retried', async () => {
  const {get, guide, requests, spoken, model} = setup();
  guide.update(model(['a','b']));
  get('trip-tab-guide').click();
  requests[0].resolve(null, false);
  await settle();
  assert.equal(get('guide-retry-list').hidden, false);
  get('guide-retry-list').click();
  requests[1].resolve(statuses([['a','unknown'],['b','unknown']]));
  await settle();
  const [first] = cards(get('guide-places'));
  first.click();
  get('guide-back-list').click();
  cards(get('guide-places'))[1].click();
  requests[2].resolve(story('a'));
  requests[3].resolve(story('b', {status:'temporarily_unavailable'}));
  await settle();
  assert.equal(get('guide-history').textContent, '');
  assert.equal(get('guide-detail-title').textContent, 'Место b');
  assert.match(get('guide-detail-status').textContent, /временно недоступен/);
  assert.equal(get('guide-player').hidden, true);
  assert.equal(spoken.length, 0);
  get('guide-retry-detail').click();
  requests[4].resolve(story('b'));
  await settle();
  assert.equal(get('guide-history').textContent,'История b.');
  assert.equal(get('guide-player').hidden, false);
});

test('manual Shanghai guides remain usable without TTS or a detail request', async () => {
  const {get, guide, requests, model} = setup({voice:false});
  guide.update(model(['shanghai-demo:bund','unknown'], 'shanghai'));
  get('trip-tab-guide').click();
  assert.deepEqual(JSON.parse(requests[0].options.body), {poi_ids:['unknown']});
  cards(get('guide-places'))[0].click();
  assert.match(get('guide-history').textContent,/историческая набережная Шанхая/);
  assert.equal(get('guide-voice-hint').hidden, false);
  assert.equal(get('guide-player').hidden, true);
  assert.equal(requests.length, 1);
  requests[0].resolve(statuses([['unknown','excluded']]));
  await settle();
});

test('prefetch checks only the selected day, shares a click, and cancels a hidden queue', async () => {
  const {get, guide, requests, model, runTimers} = setup();
  const route = model(['a','b','c']);
  const second = model(['d','e']).days[0];
  route.days.push({...second,date:'2026-11-26',label:'День 2'});
  guide.update(route);
  get('trip-tab-guide').click();
  assert.deepEqual(JSON.parse(requests[0].options.body), {poi_ids:['a','b','c']});
  requests[0].resolve(statuses([['a','unknown'],['b','unknown'],['c','unknown']]));
  await settle();
  runTimers();
  runTimers();
  assert.equal(requests.length, 2);
  assert.match(requests[1].url, /\/a\?city=moscow$/);
  cards(get('guide-places'))[0].click();
  assert.equal(requests.length, 2);
  requests[1].resolve(story('a'));
  await settle();
  assert.equal(get('guide-history').textContent, 'История a.');
  get('guide-back-list').click();
  get('guide-days').children[1].click();
  runTimers();
  assert.equal(requests.length, 3);
  assert.deepEqual(JSON.parse(requests[2].options.body), {poi_ids:['d','e']});
  requests[2].resolve(statuses([['d','unknown'],['e','unknown']]));
  await settle();
  get('trip-tab-route').click();
  runTimers();
  assert.equal(requests.length, 3);
});

test('prefetch loads at most two nearby stories and leaves a failed request retryable', async () => {
  const {get, guide, requests, model, runTimers} = setup();
  guide.update(model(['a','b','c']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','unknown'],['b','unknown'],['c','unknown']]));
  await settle();
  runTimers();
  requests[1].resolve(story('a', {status:'temporarily_unavailable'}));
  await settle();
  runTimers();
  requests[2].resolve(story('b'));
  await settle();
  runTimers();
  assert.equal(requests.length, 3);
  assert.match(requests[2].url, /\/b\?city=moscow$/);
  cards(get('guide-places'))[0].click();
  assert.equal(requests.length, 4);
  requests[3].resolve(story('a'));
  await settle();
  assert.equal(get('guide-history').textContent, 'История a.');
});

test('a running background request does not start another on the previous day', async () => {
  const {get, guide, requests, model, runTimers} = setup();
  const route = model(['a','b']);
  route.days.push({...model(['c']).days[0],date:'2026-11-26',label:'День 2'});
  guide.update(route);
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','unknown'],['b','unknown']]));
  await settle();
  runTimers();
  get('guide-days').children[1].click();
  requests[2].resolve(statuses([['c','unknown']]));
  await settle();
  runTimers();
  assert.equal(requests.length, 3);
  requests[1].resolve(story('a'));
  await settle();
  runTimers();
  assert.equal(requests.length, 4);
  assert.match(requests[3].url, /\/c\?city=moscow$/);
  get('trip-tab-route').click();
  requests[3].resolve(story('c'));
  await settle();
  runTimers();
  assert.equal(requests.length, 4);
});

test('catalog guide opens immediately and a detailed story does not replace active speech', async () => {
  const {get, guide, requests, spoken, model} = setup();
  guide.update(model(['a']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available',true]]));
  await settle();
  cards(get('guide-places'))[0].click();
  assert.equal(requests[1].url, '/api/guide/poi/a?city=moscow&quick=true');
  const catalog = story('a', {coverage:'catalog',research_status:'pending',short_text:'Краткий гид.\n\nВторая фраза каталога.'});
  requests[1].resolve(catalog);
  await settle();
  assert.equal(get('guide-catalog-label').hidden, false);
  assert.equal(get('guide-research').hidden, false);
  get('guide-play').click();
  assert.equal(spoken[0].text, 'Краткий гид.');
  get('guide-research').click();
  assert.match(get('guide-detail-status').textContent, /Ищем/);
  assert.equal(requests[2].url, '/api/guide/poi/a?city=moscow');
  requests[2].resolve(story('a', {short_text:'Подробная история.',language:'en'}));
  await settle();
  assert.equal(get('guide-catalog-label').hidden, true);
  assert.equal(get('guide-research').hidden, true);
  assert.equal(get('guide-history').textContent, 'Подробная история.');
  assert.equal(spoken.length, 1);
  spoken[0].onend();
  assert.equal(spoken[1].text.trim(), 'Вторая фраза каталога.');
  assert.equal(spoken[1].lang, 'ru-RU');
  spoken[1].onend();
  get('guide-play').click();
  assert.equal(spoken[2].text, 'Подробная история.');
});

test('speech prefers an enhanced voice in the story language and preserves phrasing and speed', async () => {
  const local = {name:'Обычный русский', lang:'ru-RU', localService:true, default:true};
  const natural = {name:'Natural Russian', lang:'ru-RU', localService:false};
  const foreign = {name:'Neural English', lang:'en-US', localService:true, default:true};
  const {get, guide, requests, spoken, model} = setup({voices:[foreign, local, natural]});
  const text = 'Музей им. А. С. Пушкина открылся в 1912 г. Посмотрите на здание.\n\nТеперь обратите внимание на вход.';
  guide.update(model(['a']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available']]));
  await settle();
  cards(get('guide-places'))[0].click();
  requests[1].resolve(story('a', {short_text:text}));
  await settle();
  get('guide-speed').value = '1.25';
  get('guide-play').click();
  assert.equal(spoken[0].text, text.split('\n\n')[0]);
  assert.equal(spoken[0].voice, natural);
  assert.equal(spoken[0].lang, 'ru-RU');
  assert.equal(spoken[0].rate, 0.94 * 1.25);
  spoken[0].onend();
  assert.equal(spoken[1].text, text.split('\n\n')[1]);
});

test('catalog guide stays usable and retry is available when a detailed source fails', async () => {
  const {get, guide, requests, model} = setup();
  guide.update(model(['a']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available',true]]));
  await settle();
  cards(get('guide-places'))[0].click();
  const catalog = story('a', {coverage:'catalog',research_status:'pending'});
  requests[1].resolve(catalog);
  await settle();
  get('guide-research').click();
  requests[2].resolve({...catalog,research_status:'temporarily_unavailable'});
  await settle();
  assert.match(get('guide-detail-status').textContent, /временно недоступен/);
  assert.equal(get('guide-research').hidden, false);
  assert.equal(get('guide-listen').hidden, false);
  get('guide-research').click();
  requests[3].resolve({...catalog,research_status:'unavailable'});
  await settle();
  assert.equal(get('guide-research').hidden, true);
  assert.equal(get('guide-listen').hidden, false);
});

test('background checks use research status and a late catalog reply cannot overwrite rich content', async () => {
  const {get, guide, requests, model, runTimers} = setup();
  guide.update(model(['a','b']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available',true],['b','available',false]]));
  await settle();
  runTimers();
  assert.equal(requests[1].url, '/api/guide/poi/a?city=moscow');
  cards(get('guide-places'))[0].click();
  assert.equal(requests[2].url, '/api/guide/poi/a?city=moscow&quick=true');
  requests[1].resolve(story('a', {short_text:'Материал из источника.'}));
  await settle();
  requests[2].resolve(story('a', {coverage:'catalog',research_status:'pending'}));
  await settle();
  assert.equal(get('guide-history').textContent, 'Материал из источника.');
  assert.equal(get('guide-catalog-label').hidden, true);
  get('guide-back-list').click();
  runTimers();
  assert.equal(requests.length, 3);
});
