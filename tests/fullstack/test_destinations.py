"""Реальные HTTP-проверки многонаправленной анкеты и локального времени."""

from test_accounts import LiveServerTests
from test_drafts import valid_preferences


class DestinationTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner = self.request('/api/trip-draft')[1]['owner_context']
        self.catalog = self.request('/api/catalog')[1]

    def preferences(self, country='FR', city='Париж', zone='Europe/Paris'):
        data = valid_preferences()
        data.update(schema_version='fullstack-draft-0.2', country_code=country, city=city, timezone=zone,
                    first_walk_at='2026-11-24T10:00:00', last_walk_end_at='2026-11-26T18:00:00')
        return data

    def save(self, data, revision=0):
        return self.request('/api/trip-draft', {'revision': revision, 'preferences': data},
                            method='PUT', headers={'X-TripWise-Draft-Owner': self.owner})

    def test_public_catalog_priority_and_source(self):
        anonymous = self.request('/api/catalog')
        self.assertEqual(anonymous[0], 200)
        self.assertEqual([c['code'] for c in self.catalog['currencies'][:4]], ['RUB', 'USD', 'EUR', 'CNY'])
        codes = [c['code'] for c in self.catalog['currencies']]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertGreater(len(codes), 100)
        self.assertIn('GBP', codes)
        self.assertNotIn('XXX', codes)
        self.assertNotIn('XAU', codes)
        self.assertIn('six-group.com', self.catalog['provenance']['currency_source'])
        self.assertEqual(self.request('/api/travel-catalog')[1], self.catalog)

    def test_all_catalog_destinations_and_multiple_places_roundtrip(self):
        revision = 0
        for country in self.catalog['countries']:
            for city in country['cities']:
                with self.subTest(city=city['name']):
                    data = self.preferences(country['code'], city['name'], city['timezone'])
                    data['required_poi_ids'] = [p['id'] for p in city['pois']]
                    data['budget_currency'] = 'GBP'
                    status, saved, _ = self.save(data, revision)
                    self.assertEqual(status, 200)
                    revision = saved['draft']['revision']
                    loaded = self.request('/api/trip-draft')[1]['draft']['preferences']
                    self.assertEqual(loaded['city'], city['name'])
                    self.assertEqual(loaded['required_poi_ids'], data['required_poi_ids'])
                    self.assertEqual(loaded['first_walk_at'], data['first_walk_at'])

    def test_country_city_zone_pairing_and_unknown_places(self):
        for field, value in [('country_code','RU'), ('city','Неизвестный город'), ('timezone','Europe/London'), ('timezone','bad/zone'), ('required_poi_ids',['invented'])]:
            data = self.preferences()
            data[field] = value
            with self.subTest(field=field):
                self.assertEqual(self.save(data)[0], 422)

    def test_places_cannot_repeat_or_cross_city(self):
        data = self.preferences()
        paris = next(c for c in self.catalog['countries'] if c['code']=='FR')['cities'][0]['pois'][0]['id']
        moscow = self.catalog['countries'][0]['cities'][0]['pois'][0]['id']
        for ids in [[paris,paris],[moscow]]:
            data['required_poi_ids'] = ids
            self.assertEqual(self.save(data)[0],422)

    def test_dst_nonexistent_and_ambiguous_local_time_rejected(self):
        for start,end in [('2027-03-28T02:30:00','2027-03-28T12:00:00'),('2027-10-31T02:30:00','2027-10-31T12:00:00')]:
            data=self.preferences()
            data.update(first_walk_at=start,last_walk_end_at=end)
            self.assertEqual(self.save(data)[0],422)
        data=self.preferences()
        data.update(first_walk_at='2027-03-28T01:30:00',last_walk_end_at='2027-03-28T04:30:00')
        self.assertEqual(self.save(data)[0],200)

    def test_new_local_schema_rejects_offsets_and_end_before_start(self):
        for start,end in [('2026-11-24T10:00:00+01:00','2026-11-26T18:00:00'),('2026-11-24T10:00:00','2026-11-24T09:00:00')]:
            data=self.preferences()
            data.update(first_walk_at=start,last_walk_end_at=end)
            self.assertEqual(self.save(data)[0],422)

    def test_editing_without_put_does_not_save_anything(self):
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])
        self.assertEqual(self.request('/trip/new')[0],200)
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])
        self.assertEqual(self.save(self.preferences())[0],200)
        self.assertEqual(self.request('/api/trip-draft')[1]['draft']['revision'],1)
