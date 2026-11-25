const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = readFileSync(path.join(root, 'frontend/route-guide.js'), 'utf8');
const localStories = readFileSync(path.join(root, 'frontend/guide-stories.js'), 'utf8');
const template = readFileSync(path.join(root, 'backend/pages/result.html'), 'utf8');

function setup({voice = true} = {}) {
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
  get('guide-list-view').append(get('guide-places'), get('guide-unknown-places'));
  for (const name of ['route', 'guide', 'tips']) get('trip-tab-' + name).setAttribute('aria-controls', 'trip-panel-' + name);
  get('guide-speed').value = '1';
  document = {body, getElementById:get, createElement:() => new Element(), querySelectorAll:() => formats};
  const spoken = [];
  const synth = {
    getVoices:() => [], paused:false,
    speak:utterance => spoken.push(utterance),
    pause() { this.paused = true; }, resume() { this.paused = false; }, cancel() { spoken.push('cancelled'); },
  };
  class Utterance { constructor(text) { this.text = text; } }
  const window = {addEventListener() {}};
  if (voice) Object.assign(window, {speechSynthesis:synth, SpeechSynthesisUtterance:Utterance});
  const requests = [];
  const context = vm.createContext({window, document, Image:Element, URL, CSS:{escape:value => value},
    SpeechSynthesisUtterance:Utterance,
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
  return {get, guide, spoken, show, requests, formats, model};
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const statuses = rows => ({items:rows.map(([poi_id, status]) => ({poi_id,status,category:'museums'}))});
const cards = node => node.children.map(row => row.children[0]);
const cardIds = node => cards(node).map(card => card.dataset.guideKey.split('/').at(-2));
const story = (id, fields = {}) => ({poi_id:id, city:'moscow', status:'available', short_text:'История ' + id + '.',
  full_text:'Подробный рассказ ' + id + '.', language:'ru', facts:[], sources:[], ...fields});

test('guide requests availability lazily and keeps stop order in both lists', async () => {
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
  assert.deepEqual(cardIds(get('guide-places')), ['b','d']);
  assert.deepEqual(cardIds(get('guide-unknown-places')), ['a']);
  assert.equal(requests.length, 1);
  guide.update(model(['d','f']));
  assert.deepEqual(JSON.parse(requests[1].options.body), {poi_ids:['f']});
  requests[1].resolve(statuses([['f','unknown']]));
  await settle();
  assert.deepEqual(cardIds(get('guide-places')), ['d']);
  assert.deepEqual(cardIds(get('guide-unknown-places')), ['f']);
});

test('selected guide lazily renders facts, image attribution and safe sources', async () => {
  const {get, guide, requests, spoken, show, model} = setup();
  guide.update(model(['a']));
  get('trip-tab-guide').click();
  requests[0].resolve(statuses([['a','available']]));
  await settle();
  cards(get('guide-places'))[0].click();
  assert.match(get('guide-detail-status').textContent, /Загружаем/);
  assert.equal(requests[1].url, '/api/guide/poi/a?city=moscow');
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
  assert.equal(spoken[0].text, 'Факт.');
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
  const [first] = cards(get('guide-unknown-places'));
  first.click();
  get('guide-back-list').click();
  cards(get('guide-unknown-places'))[1].click();
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
