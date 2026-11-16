"""Нормализация только искусственных OSM payloads, без реальной загрузки."""

from copy import deepcopy
import unittest

from ai_ds.normalize_pois import categories_from_tags, MAPPING_PATH, normalize_snapshot
from ai_ds.data import load_pois
from test_poi_sources import mock_document


def mock_metadata():
    return {'city_key': 'moscow', 'retrieved_at': '2026-11-16T12:00:00+03:00',
            'selection_basis': 'overpass_map_to_area_from_verified_admin_relation'}


def mock_node(osm_id=1, tags=None):
    return {'type': 'node', 'id': osm_id, 'lat': 55.75, 'lon': 37.62,
            'tags': tags if tags is not None else {'name': 'Тестовый музей', 'tourism': 'museum'}}


class NormalizationTests(unittest.TestCase):
    def document(self, *elements):
        document = mock_document()
        document['elements'].extend(elements)
        return document

    def test_name_coordinates_provenance_and_nulls(self):
        document = self.document(mock_node())
        before = deepcopy(document)
        pois, report = normalize_snapshot(document, mock_metadata())
        self.assertEqual(document, before)
        p = pois[0]
        self.assertEqual(p['name'], 'Тестовый музей')
        self.assertEqual(p['city'], 'Москва')
        self.assertEqual(p['coordinates'], {'latitude': 55.75, 'longitude': 37.62})
        self.assertEqual(p['id'], 'osm:node:1')
        self.assertFalse(p['provenance']['is_synthetic'])
        self.assertIsNone(p['provenance']['verified_at'])
        for field in ('rating', 'popularity', 'visit_duration_minutes', 'visit_duration_basis',
                      'estimated_cost', 'opening_hours'):
            self.assertIsNone(p[field])
        self.assertEqual(report['selected_count'], 1)

    def test_no_name_guessing_or_unusual(self):
        mapping = load_pois(MAPPING_PATH)
        self.assertEqual(categories_from_tags({'name': 'Необычный музей'}, mapping), [])
        self.assertEqual(categories_from_tags({'tourism': 'attraction'}, mapping), [])

    def test_mapping_and_multicategory(self):
        pois, _ = normalize_snapshot(self.document(mock_node(tags={
            'name': 'Тестовый парк', 'leisure': 'park', 'tourism': 'viewpoint'})), mock_metadata())
        self.assertEqual(pois[0]['categories'], ['nature', 'viewpoints'])

    def test_way_center_marked_not_entrance(self):
        way = mock_node(); way.update(type='way', center={'lat': 55.7, 'lon': 37.6})
        del way['lat']; del way['lon']
        pois, report = normalize_snapshot(self.document(way), mock_metadata())
        self.assertEqual(pois[0]['coordinates']['latitude'], 55.7)
        self.assertEqual(report['geometry']['osm:way:1']['basis'], 'bounding_box_center')
        self.assertFalse(report['geometry']['osm:way:1']['is_verified_entrance'])

    def test_invalid_coordinates_name_and_tags(self):
        invalid = mock_node(2); invalid['lat'] = 100
        no_name = mock_node(3); del no_name['tags']['name']
        missing = mock_node(4); del missing['lat']; del missing['lon']
        pois, report = normalize_snapshot(self.document(invalid, no_name, missing), mock_metadata())
        self.assertEqual(pois, [])
        self.assertEqual(len(report['rejected']), 3)

    def test_duplicate_raw_outputs_deduplicated(self):
        node = mock_node()
        pois, _ = normalize_snapshot(self.document(node, deepcopy(node)), mock_metadata())
        self.assertEqual(len(pois), 1)
        other = deepcopy(node); other['tags']['name'] = 'Конфликт'
        with self.assertRaises(ValueError):
            normalize_snapshot(self.document(node, other), mock_metadata())

    def test_opening_hours_stays_only_in_raw(self):
        node = mock_node(); node['tags']['opening_hours'] = 'Mo-Fr 10:00-18:00'
        pois, _ = normalize_snapshot(self.document(node), mock_metadata())
        self.assertIsNone(pois[0]['opening_hours'])
        self.assertEqual(node['tags']['opening_hours'], 'Mo-Fr 10:00-18:00')

    def test_selection_deterministic_and_limited(self):
        nodes = [mock_node(i) for i in range(1, 31)]
        first = normalize_snapshot(self.document(*nodes), mock_metadata())[0]
        second = normalize_snapshot(self.document(*reversed(nodes)), mock_metadata())[0]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 24)
        with self.assertRaises(ValueError):
            normalize_snapshot(self.document(*nodes), mock_metadata(), limit=25)


if __name__ == '__main__':
    unittest.main()
