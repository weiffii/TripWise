"""Изолированные HTTP-проверки API гида с временным кэшем и Wikimedia-заглушкой."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException

from backend import accounts, guide
from backend.shanghai import router as trip_router


catalog_lookup = guide.get_poi


def request(app, method, target, body=None, origin='http://testserver'):
    url = urlsplit(target)
    data = b'' if body is None else json.dumps(body).encode('utf8')
    headers = [(b'host', b'testserver')]
    if origin is not None:
        headers.append((b'origin', origin.encode('ascii')))
    if body is not None:
        headers.append((b'content-type', b'application/json'))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'method': method, 'scheme': 'http',
             'server': ('testserver', 80), 'client': ('127.0.0.1', 50001), 'root_path': '',
             'path': url.path, 'query_string': url.query.encode('ascii'), 'headers': headers}
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': data, 'more_body': False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(content)


class GuideIntegrationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(TemporaryDirectory())
        stack.enter_context(patch.object(accounts, 'DB_PATH', Path(directory) / 'guide.sqlite3'))
        guide.init_guide()
        self.app = FastAPI()
        self.app.include_router(guide.router)
        self.app.dependency_overrides[accounts.current_user] = lambda: {'id': 1}
        self.story = {
            'short_text': 'Музей знакомит с историей Москвы.',
            'full_text': 'Музей знакомит с историей Москвы и хранит городские коллекции.',
            'facts': [], 'sources': [{'title': 'Википедия', 'url': 'https://ru.wikipedia.org/wiki/Москва'}],
            'match': {'method': 'wikidata_id', 'qid': 'Q123'}, 'image': None, 'language': 'ru',
        }
        self.poi = {'poi_id': 'museum', 'name': 'Музей Москвы', 'category': 'museums',
                    'coordinates': {'latitude': 55.74, 'longitude': 37.59}}
        self.lookup = stack.enter_context(patch.object(
            guide, 'get_poi', side_effect=lambda city, poi_id: {**self.poi, 'city': city}
            if poi_id == 'museum' else None))
        self.wikimedia = stack.enter_context(patch.object(guide, 'build_story', return_value=self.story))

    def test_route_catalog_transfers_only_selected_places_in_requested_order(self):
        self.app.include_router(trip_router)
        ids = ['moscow-cultural-609', 'osm-r-19077852', 'missing', 'moscow-cultural-609']
        status, result = request(self.app, 'POST', '/api/planned-trips/catalog/moscow/selection', {'poi_ids': ids})
        self.assertEqual(status, 200)
        self.assertEqual([row['id'] for row in result], ids[:2])
        self.assertEqual(result[0]['name'], 'Музей Москвы')
        self.assertIsInstance(result[0]['lat'], (int, float))
        self.wikimedia.assert_not_called()

    def test_editorial_guide_is_available_when_wikimedia_is_offline(self):
        from backend.guide_wikimedia import WikimediaUnavailable
        self.wikimedia.side_effect = WikimediaUnavailable()
        self.lookup.side_effect = None
        for key, marker in (('moscow-cultural-609', '1896'), ('osm-r-2421978', '1912'),
                            ('osm-r-19077852', '1859')):
            self.lookup.return_value = {**self.poi, 'poi_id': key, 'city': 'moscow'}
            self.assertEqual(self.availability([key])[1]['items'][0]['status'], 'available')
            status, result = self.details(poi=key)
            self.assertEqual((status, result['status']), (200, 'available'))
            self.assertIn(marker, result['full_text'])
            self.assertTrue(result['facts'] and result['sources'])

    def details(self, poi='museum', city='moscow'):
        return request(self.app, 'GET', f'/api/guide/poi/{poi}?city={city}')

    def availability(self, ids, **kwargs):
        return request(self.app, 'POST', '/api/guide/availability?city=moscow', {'poi_ids': ids}, **kwargs)

    def test_availability_keeps_route_order_without_wikimedia_fetch(self):
        status, payload = self.availability(['unknown', 'museum'])
        self.assertEqual(status, 200)
        self.assertEqual([row['poi_id'] for row in payload['items']], ['unknown', 'museum'])
        self.assertTrue(all({'poi_id', 'status', 'category'} <= row.keys() for row in payload['items']))
        self.assertEqual([(row['status'], row['category']) for row in payload['items']],
                         [('excluded', None), ('unknown', 'museums')])
        self.wikimedia.assert_not_called()

    def test_unknown_details_are_404_without_external_fetch(self):
        self.assertEqual(self.details(poi='unknown')[0], 404)
        self.wikimedia.assert_not_called()

    def test_ineligible_cafe_does_not_make_external_request(self):
        self.lookup.side_effect = None
        self.lookup.return_value = {**self.poi, 'city': 'moscow', 'poi_id': 'cafe', 'category': 'cafes'}
        status, detail = self.details(poi='cafe')
        self.assertEqual(status, 200)
        self.assertEqual(detail['status'], 'excluded')
        self.wikimedia.assert_not_called()

    def test_real_catalog_museum_and_secondary_history_are_eligible_in_both_cities(self):
        self.lookup.side_effect = catalog_lookup
        fixture = {
            'moscow': [('moscow-cultural-1', 'museums'), ('moscow-cultural-p21', 'history')],
            'shanghai': [('shanghai-demo:shanghai-museum', 'museums'), ('shanghai-demo:yu-garden', 'history')],
        }
        for city, places in fixture.items():
            with self.subTest(city=city):
                status, payload = request(self.app, 'POST', '/api/guide/availability?city=' + city,
                                          {'poi_ids': [poi_id for poi_id, _ in places]})
                self.assertEqual(status, 200)
                self.assertEqual([(row['poi_id'], row['category']) for row in payload['items']], places)
                self.assertTrue(all(row['status'] in {'available', 'unknown'} for row in payload['items']))
        self.wikimedia.assert_not_called()

    def test_real_catalog_food_is_excluded_in_both_cities(self):
        self.lookup.side_effect = catalog_lookup
        for city, poi_id in [('moscow', 'moscow-osm-node-3478472276'), ('shanghai', 'shanghai-demo:nanxiang')]:
            with self.subTest(city=city):
                status, payload = self.details(poi_id, city)
                self.assertEqual(status, 200)
                self.assertEqual(payload['status'], 'excluded')
        self.wikimedia.assert_not_called()

    def test_real_catalog_primary_shopping_blocks_secondary_history(self):
        self.lookup.side_effect = catalog_lookup
        status, payload = self.details('shanghai-demo:xintiandi', 'shanghai')
        self.assertEqual(status, 200)
        self.assertEqual(payload['status'], 'excluded')
        self.wikimedia.assert_not_called()

    def test_editorial_moscow_story_survives_external_outage(self):
        from backend.guide_wikimedia import WikimediaUnavailable

        self.lookup.side_effect = catalog_lookup
        self.wikimedia.side_effect = WikimediaUnavailable('offline')
        fixtures = {'moscow-cultural-609': '1896', 'osm-r-1360641': 'крепости',
                    'osm-n-2368886164': '1635'}
        status, listing = self.availability(list(fixtures))
        self.assertEqual(status, 200)
        self.assertEqual([row['status'] for row in listing['items']], ['available'] * len(fixtures))
        self.wikimedia.assert_not_called()
        for poi_id, fragment in fixtures.items():
            with self.subTest(poi_id=poi_id):
                status, payload = self.details(poi_id)
                self.assertEqual(status, 200)
                self.assertEqual(payload['status'], 'available')
                self.assertEqual(payload['match']['method'], 'editorial_catalog_id')
                self.assertIn(fragment, payload['full_text'])
                self.assertTrue(payload['facts'])
                self.assertTrue(all(item['url'].startswith('https://') for item in payload['sources']))
                self.assertIsNone(payload['image'])

    def test_cache_upgrade_ignores_old_negative_and_recovers_old_positive(self):
        from backend.guide_wikimedia import WikimediaUnavailable

        now = 1_795_564_800
        with patch.object(guide, 'VERSION', guide.PREVIOUS_VERSION):
            guide.save_story('moscow', 'museum', None, now-1)
            guide.save_story('shanghai', 'museum', self.story, now-1)
        with patch.object(guide, 'time', return_value=now):
            self.assertEqual(self.availability(['museum'])[1]['items'][0]['status'], 'unknown')
            self.assertEqual(self.details()[1]['status'], 'available')
            self.wikimedia.side_effect = WikimediaUnavailable('offline')
            status, payload = self.details(city='shanghai')
            self.assertEqual(status, 200)
            self.assertTrue(payload['stale'])
            self.assertEqual(payload['short_text'], self.story['short_text'])
        self.assertEqual(self.wikimedia.call_count, 2)

    def test_details_contract_and_positive_cache(self):
        status, result = self.details()
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['short_text'], self.story['short_text'])
        self.assertTrue({'full_text', 'facts', 'sources', 'match', 'image', 'language',
                         'updated_at', 'stale'} <= result.keys())
        self.assertFalse(result['stale'])
        self.assertEqual(self.details(), (status, result))
        self.wikimedia.assert_called_once()

    def test_expired_success_can_be_returned_stale_when_wikimedia_fails(self):
        from backend.guide_wikimedia import WikimediaUnavailable

        moment = 1_795_564_800
        with patch.object(guide, 'time', return_value=moment) as clock:
            status, result = self.details()
            self.assertEqual(status, 200)
            clock.return_value += guide.POSITIVE_TTL + 1
            self.wikimedia.side_effect = WikimediaUnavailable()
            stale_status, stale = self.details()
        self.assertEqual(stale_status, 200)
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['short_text'], result['short_text'])
        self.assertEqual(stale['updated_at'], result['updated_at'])
        self.assertEqual(self.wikimedia.call_count, 2)

    def test_old_story_is_no_longer_available_after_stale_deadline(self):
        from backend.guide_wikimedia import WikimediaUnavailable

        with patch.object(guide, 'time', return_value=1_795_564_800) as clock:
            self.assertEqual(self.details()[1]['status'], 'available')
            clock.return_value += guide.STALE_TTL + 1
            self.assertEqual(self.availability(['museum', 'museum'])[1]['items'],
                             [{'poi_id': 'museum', 'status': 'unknown', 'category': 'museums'}])
            self.wikimedia.side_effect = WikimediaUnavailable('offline')
            self.assertEqual(self.details()[1]['status'], 'temporarily_unavailable')

    def test_cache_is_scoped_by_city_even_when_ids_overlap(self):
        self.assertEqual(self.details()[0], 200)
        self.wikimedia.return_value = {**self.story, 'short_text': 'Другой музей.'}
        status, city_other = self.details(city='shanghai')
        self.assertEqual(status, 200)
        self.assertEqual(city_other['short_text'], 'Другой музей.')
        self.assertEqual(self.details()[1]['short_text'], self.story['short_text'])
        self.assertEqual(self.wikimedia.call_count, 2)

    def test_negative_result_is_cached_and_positive_result_replaces_it(self):
        self.wikimedia.return_value = None
        with patch.object(guide, 'time', return_value=1_795_564_800) as clock:
            self.assertEqual(self.details()[1]['status'], 'unavailable')
            self.assertEqual(self.details()[1]['status'], 'unavailable')
            self.wikimedia.assert_called_once()
            clock.return_value += guide.NEGATIVE_TTL + 1
            self.wikimedia.return_value = self.story
            self.assertEqual(self.details()[1]['status'], 'available')
        self.assertEqual(self.wikimedia.call_count, 2)

    def test_failure_without_cache_is_temporary(self):
        from backend.guide_wikimedia import WikimediaUnavailable

        self.wikimedia.side_effect = WikimediaUnavailable('offline')
        status, detail = self.details()
        self.assertEqual(status, 200)
        self.assertEqual(detail['status'], 'temporarily_unavailable')
        self.assertFalse(detail['stale'])

    def test_city_is_validated_before_wikimedia_lookup(self):
        self.assertEqual(self.details(city='not-supported')[0], 422)
        self.wikimedia.assert_not_called()

    def test_malformed_availability_body_rejected(self):
        status, _ = request(self.app, 'POST', '/api/guide/availability?city=moscow',
                            {'poi_ids': {'not': 'a list'}})
        self.assertEqual(status, 422)
        self.wikimedia.assert_not_called()

    def test_cross_origin_availability_rejected(self):
        status, _ = self.availability(['museum'], origin='https://other.example')
        self.assertEqual(status, 403)
        self.wikimedia.assert_not_called()

    def test_unauthenticated_request_does_not_fetch(self):
        def unauthenticated():
            raise HTTPException(401, 'Требуется вход.')

        self.app.dependency_overrides[accounts.current_user] = unauthenticated
        self.assertEqual(self.details(), (401, {'detail': 'Требуется вход.'}))
        self.wikimedia.assert_not_called()

    def test_http_details_from_mocked_wikimedia_preserve_attribution_and_reject_bad_license(self):
        from backend import guide_wikimedia as source

        def claim(value):
            return {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}}

        entity = {'id': 'Q123', 'labels': {'ru': {'value': 'Музей Москвы'}},
                  'sitelinks': {'ruwiki': {'title': 'Музей Москвы'}},
                  'claims': {'P625': [claim({'latitude': 55.74, 'longitude': 37.59,
                                            'globe': 'http://www.wikidata.org/entity/Q2'})],
                             'P18': [claim('Example museum.jpg')]}}
        article = {'title': 'Музей Москвы', 'ns': 0, 'lastrevid': 91,
                   'pageprops': {'wikibase_item': 'Q123'},
                   'extract': 'Музей Москвы хранит городские коллекции. В музее открыта экспозиция.'
                              '\n== Примечания ==\nНЕ ДЛЯ ТЕКСТА.'}
        image = {'ns': 6, 'imageinfo': [{
            'url': 'https://upload.wikimedia.org/wikipedia/commons/0/00/Example.jpg', 'mime': 'image/jpeg',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Example_museum.jpg',
            'extmetadata': {'Artist': {'value': '<a href="https://example.org">Автор</a>'},
                            'LicenseShortName': {'value': 'CC BY-SA 4.0'},
                            'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/4.0/'}}}]}

        def response(endpoint, params):
            if endpoint == source.WIKIDATA_URL:
                if params['action'] == 'wbsearchentities':
                    return {'search': [{'id': 'Q123'}]}
                return {'entities': {'Q123': entity}}
            if endpoint == source.WIKIPEDIA_URL:
                return {'query': {'pages': [article]}}
            if endpoint == source.COMMONS_URL:
                return {'query': {'pages': [image]}}
            raise AssertionError(endpoint)

        self.wikimedia.side_effect = source.build_story
        with patch.object(source, '_request', side_effect=response):
            status, result = self.details()
            self.assertEqual(status, 200)
            self.assertNotIn('НЕ ДЛЯ ТЕКСТА', result['full_text'])
            self.assertIn('oldid=91', result['sources'][0]['url'])
            self.assertEqual(result['match']['wikidata_id'], 'Q123')
            self.assertEqual(result['image']['author'], 'Автор')
            image['imageinfo'][0]['extmetadata']['LicenseUrl']['value'] = 'https://creativecommons.org/licenses/by/3.0/'
            other, no_image = self.details(city='shanghai')
            self.assertEqual(other, 200)
            self.assertIsNone(no_image['image'])
            self.assertTrue(no_image['short_text'])
