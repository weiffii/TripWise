'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const mapSource = readFileSync(path.join(root, 'ai_ds/web_demo/shanghai-map.js'), 'utf8');
const resultSource = readFileSync(path.join(root, 'frontend/trip-result.js'), 'utf8');

function element() {
  return {clientWidth:640, clientHeight:300, children:[],
    appendChild(child) { this.children.push(child); }, addEventListener() {},
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    querySelectorAll() { return []; }, setAttribute() {}};
}

test('selecting a stop focuses it and selecting a leg shows fare and clears place selection', () => {
  const context = vm.createContext({document:{createElement:element}, clearTimeout});
  vm.runInContext(mapSource, context);
  const selectedPlaces = [], details = [], svg = element();
  const map = context.TripWiseMap.create({
    container:element(), svg, tiles:element(), background:element(),
    directTiles:true, focusStopDetails:true, focusStopWithArrival:true, currency:()=> 'RUB',
    onStopSelect:index=>selectedPlaces.push(index), onSelect:(html,index)=>details.push({html,index}),
  });
  const points = new Map([['a',{id:'a',name:'A',lon:37.62,lat:55.75}],
                          ['b',{id:'b',name:'B',lon:37.50,lat:55.70}]]);
  const events = [{type:'visit',poi_id:'a'},
    {type:'travel',mode:'transit',from_poi_id:'a',to_poi_id:'b',planned_minutes:22,
      transport_cost_rub:150, transport_cost_basis:'synthetic_fare_estimate',
      instructions:[{text:'Метро, линия 1 · A → B'},{text:'Пересадка · B'}]},
    {type:'visit',poi_id:'b'}];
  map.setDay({events}, points, {online:false});
  map.selectStop(1);
  assert.equal(selectedPlaces.at(-1), 1);
  assert.deepEqual(Array.from(map.debug().center), [37.50,55.70]);
  assert(map.debug().zoom >= 17);
  assert.equal(details.at(-1).html, '');
  assert.match(svg.innerHTML, /B · выбрано/);
  map.selectLeg(0);
  assert.equal(selectedPlaces.at(-1), -1);
  assert.match(details.at(-1).html, /Стоимость для группы · ≈ 150/);
  assert.match(details.at(-1).html, /Метро, линия 1/);
  assert.match(details.at(-1).html, /Пересадка/);

  map.setDay({events:[{...events[1],mode:'taxi',transport_cost_rub:500,instructions:[]}]},
    points, {online:false});
  map.selectLeg(0);
  assert.match(details.at(-1).html, /Стоимость для группы · ≈ 500/);
  map.setDay({events:[]}, points, {online:false});
  assert.equal(selectedPlaces.at(-1), -1);
});

test('successive stations on one line are grouped and transfers remain visible', () => {
  const context = vm.createContext({});
  vm.runInContext(resultSource.slice(resultSource.indexOf('function moscowTransitInstructions('),
    resultSource.indexOf('function displayMoney(')), context);
  const step = (kind,line,from,to)=>({kind,line,from_stop_location:{name:from},to_stop_location:{name:to}});
  const instructions = context.moscowTransitInstructions([
    step('rail_estimate','metro:1','A','B'), step('rail_estimate','metro:1','B','C'),
    step('walk_transfer','','C','D'), step('rail_estimate','metro:5','D','E')]);
  assert.deepEqual(Array.from(instructions, row=>row.text), [
    'Метро, линия 1 · A → C','Пересадка · C → D','Метро, линия 5 · D → E']);
});

test('place card shows group ticket rates and ignores a previous selection response', async () => {
  const panel = element(), pending = [];
  const context = vm.createContext({
    document:{createElement:element},
    element:name=>name==='route-place-details'?panel:{value:'0'},
    appendText(parent,text) { parent.append({textContent:text}); },
    placeDetails:new Map(), selectionVersion:0,
    mapStops:[{type:'visit',poi_id:'a',price_cny:750},{type:'visit',poi_id:'b',price_cny:750}],
    places:new Map([['a',{name:'Первое место'}],['b',{name:'Второе место'}]]),
    trip:{city:'shanghai',preferences:{group:{adult:2,child:1}},plan:{days:[{date:'2026-11-24'}]}},
    request:url=>new Promise(resolve=>pending.push({url,resolve})),
  });
  vm.runInContext(resultSource.slice(resultSource.indexOf('function displayMoney('),
    resultSource.indexOf('const routeMap = ')), context);
  const first = context.showPlaceDetails(0), second = context.showPlaceDetails(1);
  assert.match(pending[1].url,/b\?visit_date=2026-11-24$/);
  pending[1].resolve({opening:['10:00','18:00'],opening_basis:'synthetic',unit_prices:{adult:300,child:150}});
  await second;
  pending[0].resolve({opening:[]});
  await first;
  const [heading,hours,costs] = panel.children;
  assert.equal(heading.textContent,'Второе место');
  assert.match(hours.textContent,/10:00–18:00 \(оценка\)/);
  assert.match(costs.children[1].textContent,/Взрослые.*300.*× 2.*600/);
  assert.match(costs.children[2].textContent,/Дети.*150.*× 1/);
  await context.showPlaceDetails(-1);
  assert.equal(panel.hidden,true);
});
