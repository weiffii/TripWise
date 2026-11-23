'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const api = require('../ai_ds/web_demo/shanghai-engine.js');

const date = '2026-11-24';
const options = {
  days: 1, start_date: date, day_start: '12:00', day_end: '18:00',
  transport: 'walk', transport_modes: ['walk'], pace: 'balanced',
  budget_cny: 100, primary_interests: ['museums'], required_ids: ['early', 'late'],
  group: { adult: 1, child: 0, senior: 0, pet: 0 },
};
const clock = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
const minutes = event => event.end_min - event.start_min;
const maxGap = events => Math.max(0, ...events.slice(1).map((row, index) =>
  row.start_min - events[index].end_min));
const toEvents = plan => plan.days[0].events;
const visit = (events, id) => events.find(row => row.type === 'visit' && row.poi_id === id);

function fixture({currency = 'RUB', early = {}, late = {}, includeFood = false, gate = false} = {}) {
  const place = (id, position, open, close, delta = {}) => ({
    id, name: id, lat: currency === 'RUB' ? 55.75 : 31.23,
    lon: (currency === 'RUB' ? 37.62 : 121.47) + position * .001,
    kind: 'attraction', duration_minutes: 30, price_cny: 40,
    categories: ['museums'], opening: { open, close, closed_weekdays: [] },
    duration_source: 'synthetic', ...delta,
  });
  const pois = [place('early', 0, '12:00', '13:15', early), place('late', 1, '15:10', '18:00', late)];
  if (gate) pois.push(place('gate', 2, '12:00', '12:20', {price_cny:0, duration_minutes:20, duration_locked:true}));
  if (includeFood) pois.push(place('cafe', 2, '12:00', '16:00', {
    kind: 'restaurant', price_cny: 10, duration_minutes: 50, categories: ['cafes'],
    food_type: 'restaurant', meal_suitability: {lunch: 'likely'},
  }));
  const row = {mode: 'walk', source: 'synthetic-test', base_minutes: 5, planned_minutes: 5,
    buffer_minutes: 0, distance_m: 200, transport_cost_cny: 0};
  const walk = Object.fromEntries(pois.map(from => [from.id, Object.fromEntries(pois
    .filter(to => to.id !== from.id).map(to => [to.id, {...row}]))]));
  return {currency, compact_waits: true, pois, leg_profiles: {walk}};
}

function assertTimeline(data, plan, prefs = options) {
  const events = toEvents(plan);
  assert(api.validateEditedTrip(data, prefs, plan).valid);
  assert(plan.summary.total_cost_cny <= prefs.budget_cny);
  for (const [index, event] of events.entries()) {
    assert(event.start_min >= clock(prefs.day_start));
    assert(event.end_min <= clock(prefs.day_end));
    if (index) assert(events[index - 1].end_min <= event.start_min);
    const point = data.pois.find(p => p.id === event.poi_id);
    if (point) {
      assert(event.start_min >= clock(point.opening.open));
      assert(event.end_min <= clock(point.opening.close));
    }
  }
  return events;
}

test('RUB plan bounds extended duration even in a gap over 120 minutes', () => {
  const data = fixture({gate:true});
  const baseline = {...data, defer_visit_extensions:true};
  const preferences = {...options, required_ids:['gate','early','late']};
  const original = assertTimeline(baseline, api.planTrip(baseline, preferences), preferences);
  assert(maxGap(original) >= 120);
  const result = assertTimeline(data, api.planTrip(data, preferences), preferences);
  const extended = visit(result, 'early');
  assert(extended.extension_minutes > 0);
  assert(extended.extension_minutes <= 30);
  assert.equal(minutes(extended), 30 + extended.extension_minutes);
  assert(maxGap(result) < maxGap(original));
});

test('early access to a following visit preserves the travel duration and reserved lunch', () => {
  const data = fixture({includeFood:true});
  data.reserved_breaks = {[date]:[[810, 850]]};
  const day = {date, events:[
    {type:'meal', poi_id:'cafe', start_min:740, end_min:790},
    {type:'travel', to_poi_id:'late', start_min:895, end_min:900},
    {type:'visit', poi_id:'late', start_min:900, end_min:930},
  ]};
  data.pois.find(p=>p.id==='late').opening.open='14:30';
  api.stretchVisitGaps(data,options,[day]);
  const [,leg,next] = day.events;
  assert.equal(leg.end_min-leg.start_min,5);
  assert.equal(leg.end_min,next.start_min);
  assert.equal(next.start_min,870);
  assert.equal(next.extension_minutes,30);
  assert(leg.start_min>=850);
});

test('validator rejects extra time beyond the visit allowance', () => {
  const data = fixture();
  const plan = api.planTrip(data, options);
  const first = visit(toEvents(plan), 'early');
  first.extension_minutes = 31;
  const verdict = api.validateEditedTrip(data,options,plan);
  assert(!verdict.valid);
  assert(verdict.errors.some(error=>error.startsWith('INVALID_VISIT_EXTENSION')));
});

test('large mall stays within four hours after adding time', () => {
  const data = fixture({early:{visit_type:'large_shopping_mall',duration_minutes:220}});
  const day = {date, events:[
    {type:'visit', poi_id:'early', start_min:720, end_min:940},
    {type:'travel', to_poi_id:'late', start_min:1020, end_min:1025},
    {type:'visit', poi_id:'late', start_min:1025, end_min:1055},
  ]};
  data.pois[0].opening.close='18:00';
  api.stretchVisitGaps(data,options,[day]);
  assert.equal(minutes(day.events[0]),240);
  assert.equal(day.events[0].extension_minutes,20);
});

test('fixed RUB schedule extends only flexible visits and preserves pinned start exactly', () => {
  const data = fixture({includeFood: true});
  const base_plan = api.planTrip(data, options);
  const spec = {base_plan, changed_days: [date],
    sequences: {[date]: [{poi_id: 'early'}, {poi_id: 'cafe', meal_kind: 'lunch'}, {poi_id: 'late'}]},
    visit_times: {[date]: {late: '15:10'}}};
  const {plan, options: settings} = api.scheduleSequences(data, options, spec);
  const events = assertTimeline(data, plan, settings);
  assert.equal(visit(events, 'late').start_min, 910);
  assert.equal(minutes(visit(events, 'late')), 30);
  assert.equal(minutes(events.find(row => row.poi_id === 'cafe')), 50);
  assert(events.filter(row => row.type === 'meal').every(row => !row.extension_minutes));
  const pinned = api.scheduleSequences(data, options, {
    ...spec, sequences: {[date]: ['early', 'late']},
    visit_times: {[date]: {early: '12:00', late: '15:10'}},
  });
  assert.equal(minutes(visit(assertTimeline(data, pinned.plan, pinned.options), 'early')), 30);
});

test('source duration, closing time and non-RUB plans stay unchanged', () => {
  for (const data of [fixture({early: {duration_source: 'source'}}),
    fixture({early: {opening: {open: '12:00', close: '12:30', closed_weekdays: []}}}),
    fixture({currency: 'CNY'})]) {
    const events = assertTimeline(data, api.planTrip(data, options));
    assert.equal(minutes(visit(events, 'early')), 30);
    assert(!visit(events, 'early').extension_minutes);
  }
});
