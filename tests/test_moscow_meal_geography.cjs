'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const api = require('../ai_ds/web_demo/shanghai-engine.js');

const date = '2026-11-21';
const options = {
  transport: 'walk', days: 1, start_date: date, day_start: '10:30', day_end: '21:00',
  primary_interests: ['museums'], budget_cny: 10000,
  group: { adult: 1, child: 0, senior: 0, pet: 0 },
};

function place(id, lon, open, close, duration, cost = 10, kind = 'attraction') {
  return {
    id, name: id, lat: 55.75, lon, kind, duration_minutes: duration,
    price_cny: cost, categories: [kind === 'restaurant' ? 'cafes' : 'museums'],
    opening: { open, close, closed_weekdays: [] },
  };
}

function streetFixture(pois, time) {
  const walk = Object.fromEntries(pois.map(a => [a.id, Object.fromEntries(pois
    .filter(b => b !== a).map(b => {
      const minutes = time(a, b);
      return [b.id, {
        mode: 'walk', source: 'OSM_OSRM', basis: 'routing_provider', distance_m: minutes * 80,
        base_minutes: minutes, planned_minutes: minutes, buffer_minutes: 0, transport_cost_cny: 0,
      }];
    }))]));
  return { pois, currency: 'RUB', compact_waits: true, leg_profiles: { walk } };
}

test('joint meal search reduces the reproduced 65-minute dinner wait', () => {
  let state = 28;
  function sample(maximum) {
    state = (1664525 * state + 1013904223) >>> 0;
    return Math.floor(state / 2 ** 32 * maximum);
  }
  const time = n => String(Math.floor(n / 60)).padStart(2, '0') + ':' + String(n % 60).padStart(2, '0');
  // Fixed tiny fixture that yielded a 65-minute gap under the initial timetable.
  const pois = [...'abcdefgh'].map((id, i) => {
    const restaurant = i >= 4;
    const open = restaurant ? (i < 6 ? 720 + sample(160) : i === 6 ? 900 + sample(180) : 630) : 630 + sample(360);
    const close = restaurant ? 1260 : Math.min(1260, open + 60 + sample(220));
    const lon = 37.62 + sample(60) * .001;
    const cost = 10 + sample(200);
    return place(id, lon, time(open), time(close), restaurant ? 50 : 25 + sample(65), cost,
      restaurant ? 'restaurant' : 'attraction');
  });
  const data = streetFixture(pois, (a, b) => Math.max(2, Math.round(api.distance(a, b) / 220 + sample(5))));
  const preferences = { ...options, required_ids: ['a', 'b'] };
  const plan = api.planTrip(data, preferences);
  const events = plan.days[0].events;
  const lunch = events.find(event => event.meal_kind === 'lunch');
  const dinnerIndex = events.findIndex(event => event.meal_kind === 'dinner');
  assert.ok(lunch && dinnerIndex > 1);
  assert.ok(events[dinnerIndex].start_min >= lunch.end_min + 180);
  assert.ok(events[dinnerIndex - 1].start_min - events[dinnerIndex - 2].end_min <= 60);
  assert.equal(plan.summary.visit_count, 4);
  // Full day beginning before noon now legitimately requires breakfast as well.
  assert.equal(plan.summary.meal_count, 3);
  assert.ok(events.some(event => event.meal_kind === 'breakfast'));
  assert.ok(api.validateEditedTrip(data, preferences, plan).valid);
});

test('actual directed travel times group two banks in one crossing', () => {
  const pois = [
    { ...place('east-1', 37.64, '12:00', '18:00', 30), river_bank: 'east' },
    { ...place('west-1', 37.63, '12:00', '18:00', 30), river_bank: 'west' },
    { ...place('east-2', 37.64, '12:00', '18:00', 30), river_bank: 'east' },
    { ...place('west-2', 37.63, '12:00', '18:00', 30), river_bank: 'west' },
  ];
  const data = streetFixture(pois, (a, b) => a.river_bank === b.river_bank ? 5 : a.river_bank === 'east' ? 30 : 40);
  const preferences = { ...options, day_start: '12:00', day_end: '16:00', required_ids: pois.map(p => p.id) };
  const plan = api.planTrip(data, preferences);
  const visits = plan.days[0].visit_ids.map(id => pois.find(p => p.id === id).river_bank);
  assert.equal(visits.length, 4);
  assert.equal(visits.slice(1).filter((bank, i) => bank !== visits[i]).length, 1);
  assert.equal(plan.summary.planned_travel_minutes, 40);
  assert.ok(api.validateEditedTrip(data, preferences, plan).valid);
});

test('irreducible gap keeps its opening-hours explanation', () => {
  const pois = [
    place('early', 37.62, '09:00', '10:00', 45),
    place('late', 37.625, '16:00', '18:00', 45),
  ];
  const data = streetFixture(pois, () => 5);
  const preferences = { ...options, day_start: '09:00', day_end: '17:00', required_ids: ['early', 'late'] };
  const plan = api.planTrip(data, preferences);
  const travel = plan.days[0].events.find(event => event.type === 'travel');
  assert.ok(travel.wait_before_min >= 350);
  assert.match(travel.waiting_reason, /Ожидание открытия/);
  assert.ok(api.validateEditedTrip(data, preferences, plan).valid);
});
