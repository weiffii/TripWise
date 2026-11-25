import io
import json
import unittest
from datetime import datetime, timezone
from email.message import Message
from email.utils import format_datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from backend import guide_wikimedia as wiki


def snak(value, rank='normal'):
    return {'rank': rank, 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}}


def coords(lat=55.75, lon=37.61):
    return {'latitude': lat, 'longitude': lon, 'globe': 'http://www.wikidata.org/entity/Q2'}


def entity(qid='Q123', *, name='Музей часов', description='исторический музей в Москве',
           geographic=None, aliases=(), image=None, types=('Q33506',)):
    item = {
        'id': qid,
        'labels': {'ru': {'value': name}},
        'aliases': {'ru': [{'value': value} for value in aliases]},
        'descriptions': {'ru': {'value': description}},
        'sitelinks': {'ruwiki': {'title': name}},
        'claims': {
            'P625': [snak(geographic or coords())],
            'P31': [snak({'id': value}) for value in types],
            'P571': [snak({'precision': 11, 'time': '+1905-12-01T00:00:00Z',
                           'calendarmodel': 'http://www.wikidata.org/entity/Q1985727'})],
        },
    }
    if image:
        item['claims']['P18'] = [snak(image)]
    return item


def poi(name='Музей часов', **overrides):
    return {'poi_id': 'mock-poi', 'city': 'moscow', 'name': name, 'category': 'museums',
            'coordinates': coords(), 'aliases': [], 'wikidata_id': None, **overrides}


EXTRACT = """Музей часов знакомит посетителей с историей точного времени и часовым искусством.
Постоянная экспозиция находится в старом городском особняке.
== История ==
Первые механизмы для коллекции поступили из городских мастерских.
Со временем музей открыл отдельный зал для редких настольных часов.
Городские мастера помогали музею восстанавливать и сохранять часовые механизмы.
<table><tr><td>Строка таблицы не может стать частью рассказа о музее.</td></tr></table>
<ul><li>Строка списка не может стать частью рассказа о музее.</li></ul>
* Вики-список не может стать частью рассказа об истории этого особняка.
1. Нумерованный список не может стать частью рассказа о музее.
== Архитектура ==
У главного входа сохранилась каменная облицовка и кованая решётка.
Здание продолжает занимать участок на тихой городской улице.
== Видео ==
Этот большой видеоролик нельзя показывать среди фактов и в расширенном тексте.
=== Старинные кадры ===
Подраздел видео также должен быть отброшен из описания статьи.
== Примечания ==
Библиография и списки не должны попадать в гид автоматически."""


def article(qid='Q123', **overrides):
    page = {'title': 'Музей часов', 'pageid': 42, 'ns': 0, 'lastrevid': 9, 'extract': EXTRACT,
            'pageprops': {'wikibase_item': qid}}
    return {'query': {'pages': [{**page, **overrides}]}}


def image_response(*, author='<a href="/wiki/User:Anna">Анна</a>', label='CC BY-SA 4.0',
                   license_url='https://creativecommons.org/licenses/by-sa/4.0/',
                   image_url='https://upload.wikimedia.org/wikipedia/commons/a/aa/image.jpg'):
    return {'query': {'pages': [{
        'ns': 6, 'imageinfo': [{
            'url': image_url,
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Image.jpg',
            'mime': 'image/jpeg',
            'extmetadata': {k: {'value': v} for k, v in {
                'Artist': author, 'LicenseShortName': label, 'LicenseUrl': license_url,
            }.items()},
        }],
    }]}}


class StoryTests(unittest.TestCase):
    def provider(self, items, page=None, picture=None):
        def request(endpoint, params):
            if endpoint == wiki.WIKIDATA_URL and params['action'] == 'wbgetentities':
                return {'entities': {qid: items[qid] for qid in params['ids'].split('|') if qid in items}}
            if endpoint == wiki.WIKIDATA_URL:
                return {'search': [{'id': qid} for qid in items]}
            if endpoint == wiki.WIKIPEDIA_URL:
                return page if page is not None else article()
            if endpoint == wiki.COMMONS_URL and picture is not None:
                return picture
            self.fail(f'Unexpected request: {endpoint} {params}')
        return patch.object(wiki, '_request', side_effect=request)

    def test_builds_licensed_lead_full_story_and_structured_facts(self):
        with self.provider({'Q123': entity(image='Image.jpg')}, picture=image_response()) as request:
            story = wiki.build_story(poi(wikidata_id='Q123'))
        self.assertEqual(len(request.call_args_list), 3)
        self.assertEqual(request.call_args_list[0].args[1]['action'], 'wbgetentities')
        self.assertEqual(story['match'], {
            'wikidata_id': 'Q123', 'confidence': 'high', 'method': 'catalog_wikidata',
        })
        self.assertIn('Музей часов', story['short_text'])
        self.assertIn('сохранять часовые механизмы', story['full_text'])
        self.assertNotIn('каменная облицовка', story['full_text'])
        self.assertNotIn('поступили из городских мастерских', story['full_text'])
        for unwanted in ('таблицы', 'Строка списка', 'Вики-список', 'Нумерованный',
                         'видеоролик', 'Подраздел видео', 'Библиография'):
            self.assertNotIn(unwanted, story['full_text'])
        self.assertEqual(story['facts'][0], {
            'title': 'Год основания', 'text': 'Датой основания этого места считается 1905 год.',
        })
        self.assertIn('отдельный зал', story['facts'][1]['detail'])
        self.assertNotIn('отдельный зал', story['full_text'])
        self.assertEqual(story['sources'][0]['license'], 'CC BY-SA 4.0')
        self.assertTrue(story['sources'][0]['url'].endswith('?oldid=9'))
        self.assertEqual(story['sources'][1]['license'], 'CC0 1.0')
        self.assertEqual(story['image']['author'], 'Анна')

    def test_search_checks_geography_category_and_wikibase_id(self):
        items = {
            'Q234': entity('Q234', geographic=coords(59.93, 30.33)),
            'Q345': entity('Q345', name='Часовой завод', description='промышленное предприятие',
                           aliases=('Музей часов',), types=()),
            'Q123': entity(name='Коллекция часов', aliases=('Музей часов',)),
        }
        with self.provider(items):
            self.assertEqual(wiki.build_story(poi(wikidata_id='Q234'))['match']['method'],
                             'name_category_coordinates')
        for bad_page in (article(qid='Q700'), article(pageprops={'disambiguation': '', 'wikibase_item': 'Q123'})):
            with self.provider(items, page=bad_page):
                self.assertIsNone(wiki.build_story(poi()))

    def test_ambiguous_neighbors_and_generic_pavilion_alias_are_rejected(self):
        with self.provider({'Q123': entity(), 'Q234': entity('Q234', geographic=coords(55.751, 37.61))}):
            self.assertIsNone(wiki.build_story(poi()))
        unrelated_building = entity(name='Павильон № 57', description='выставочный павильон',
                                    types=())
        target = poi('Павильон № 57. Россия — моя история', aliases=['Павильон № 57'])
        with self.provider({'Q123': unrelated_building}) as request:
            self.assertIsNone(wiki.build_story(target))
        self.assertNotIn('Павильон № 57', [
            call.args[1].get('search') for call in request.call_args_list
        ])

    def test_foreign_only_and_coordinate_free_items_are_rejected(self):
        no_article = entity()
        no_article['sitelinks'] = {'enwiki': {'title': 'Clock Museum'}}
        no_coords = entity('Q234')
        no_coords['claims']['P625'] = []
        with self.provider({'Q123': no_article, 'Q234': no_coords}):
            self.assertIsNone(wiki.build_story(poi()))

    def test_nearby_metro_with_same_name_is_not_a_park(self):
        item = entity(name='Парк Победы (станция метро, Москва)',
                      description='станция московского метрополитена в парке', types=('Q928830',))
        with self.provider({'Q123': item}):
            self.assertIsNone(wiki.build_story(poi('Парк Победы', category='nature')))

    def test_commercial_building_is_rejected_despite_generic_historical_description(self):
        item = entity(name='Здание торгового дома', description='здание исторического района',
                      types=('Q11315',))
        with self.provider({'Q123': item}):
            self.assertIsNone(wiki.build_story(poi('Здание торгового дома', category='history')))

    def test_short_story_can_reach_roughly_800_characters_without_cutting_a_sentence(self):
        lead = ['Музей часов собирает материалы о работе часовых мастерских и лабораторий.']
        lead += [
            f'Коллекция под номером {index} включает местные приборы для наблюдения за точным временем.'
            for index in range(12)
        ]
        page = article(extract=' '.join(lead) + '\n' + EXTRACT)
        with self.provider({'Q123': entity()}, page=page):
            story = wiki.build_story(poi())
        self.assertTrue(700 <= len(story['short_text']) <= 880)
        self.assertTrue(story['short_text'].endswith('.'))
        self.assertGreater(len(story['full_text']), len(story['short_text']))

    def test_image_is_optional_and_rejects_unknown_author_or_untrusted_url_or_license(self):
        item = {'Q123': entity(image='Image.jpg')}
        for fixture in (
            image_response(author='Unknown author'),
            image_response(label='CC BY-NC 4.0'),
            image_response(license_url='https://creativecommons.org/licenses/by/4.0/'),
            image_response(image_url='https://upload.wikimedia.org.example.org/wikipedia/commons/i.jpg'),
        ):
            with self.subTest(fixture=fixture), self.provider(item, picture=fixture):
                self.assertIsNone(wiki.build_story(poi())['image'])
        with self.provider({'Q123': entity(image='Clip.mp4')}) as request:
            self.assertIsNone(wiki.build_story(poi())['image'])
        self.assertNotIn(wiki.COMMONS_URL, [call.args[0] for call in request.call_args_list])

    def test_wikimedia_outage_is_not_a_missing_story(self):
        with patch.object(wiki, '_request', side_effect=wiki.WikimediaUnavailable):
            with self.assertRaises(wiki.WikimediaUnavailable):
                wiki.build_story(poi())

    def test_commons_outage_keeps_the_verified_text(self):
        responses = [{'entities': {'Q123': entity(image='Image.jpg')}}, article(), wiki.WikimediaUnavailable()]
        with patch.object(wiki, '_request', side_effect=responses):
            story = wiki.build_story(poi(wikidata_id='Q123'))
        self.assertIn('Музей часов', story['short_text'])
        self.assertIsNone(story['image'])


class RequestTests(unittest.TestCase):
    def setUp(self):
        limiter = patch.object(wiki, '_NEXT_REQUEST', {})
        limiter.start()
        self.addCleanup(limiter.stop)

    @staticmethod
    def failure(code, retry=None):
        headers = Message()
        if retry is not None:
            headers['Retry-After'] = retry
        return HTTPError(wiki.WIKIDATA_URL, code, 'Failure', headers, io.BytesIO())

    def test_fixed_endpoints_identified_agent_and_two_attempts(self):
        data = io.BytesIO(json.dumps({'search': []}).encode())
        with patch.object(wiki, 'urlopen', side_effect=[URLError('network'), data]) as opener, \
                patch.object(wiki.time, 'sleep') as sleep:
            self.assertEqual(wiki._request(wiki.WIKIDATA_URL, {'action': 'wbsearchentities',
                                                              'search': 'Название & имя'}),
                             {'search': []})
        request = opener.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).hostname, 'www.wikidata.org')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)['search'], ['Название & имя'])
        self.assertIn('TripWiseGuide', request.get_header('User-agent'))
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(.25)
        with self.assertRaises(ValueError):
            wiki._request('https://example.org/api.php', {'action': 'query'})

    def test_retry_after_sets_cooldown_to_protect_later_requests(self):
        with patch.object(wiki, 'urlopen', side_effect=self.failure(429, '60')) as opener:
            for _ in range(2):
                with self.assertRaises(wiki.WikimediaUnavailable):
                    wiki._request(wiki.WIKIDATA_URL, {'action': 'wbsearchentities'})
        self.assertEqual(opener.call_count, 1)
        self.assertAlmostEqual(wiki._retry_delay(
            format_datetime(datetime.fromtimestamp(100, timezone.utc)), 0),
            0)

    def test_network_outage_is_retried_once_then_shared_across_card_requests(self):
        with patch.object(wiki, 'urlopen', side_effect=URLError('network')) as opener, \
                patch.object(wiki.time, 'sleep') as sleep:
            for _ in range(2):
                with self.assertRaises(wiki.WikimediaUnavailable):
                    wiki._request(wiki.WIKIDATA_URL, {'action': 'wbsearchentities'})
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(.25)

    def test_hard_size_limit_and_malformed_json(self):
        for body in (b'x' * (wiki.MAX_BYTES + 2), b'{not json'):
            with self.subTest(length=len(body)), patch.object(wiki, 'urlopen', return_value=io.BytesIO(body)):
                with self.assertRaises(wiki.WikimediaUnavailable):
                    wiki._request(wiki.WIKIPEDIA_URL, {'action': 'query'})

    def test_bounded_concurrency_reports_service_busy(self):
        with patch.object(wiki, '_REQUESTS') as semaphore, patch.object(wiki, 'urlopen') as opener:
            semaphore.acquire.return_value = False
            with self.assertRaises(wiki.WikimediaUnavailable):
                wiki._request(wiki.WIKIDATA_URL, {'action': 'wbsearchentities'})
            opener.assert_not_called()
            semaphore.release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
