"""Проверки контракта, диагностики и CLI на локальных fixtures."""

from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from ai_ds.data import CATEGORIES, load_pois, main, process_pois, validate_poi

ROOT = Path(__file__).resolve().parents[1]


class DataTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_pois(ROOT / 'data/fixtures/pois.synthetic.json')
        self.poi = deepcopy(self.rows[0])

    def test_fixture_coverage(self):
        accepted, report = process_pois(self.rows)
        self.assertEqual(len(accepted), 16)
        self.assertEqual(report['summary']['rejected_count'], 0)
        self.assertEqual(report['data_kind'], 'synthetic')
        for cat in CATEGORIES:
            self.assertGreaterEqual(sum(cat in p['categories'] for p in accepted), 2)
        self.assertEqual(accepted[-1]['availability'], 'unavailable')

    def test_fixture_cyrillic_is_preserved(self):
        for index, poi in enumerate(self.rows, 1):
            self.assertEqual(poi['name'], f'Тестовый объект {index:02}')
            self.assertEqual(poi['city'], 'Тестовый город')
        accepted, _ = process_pois(self.rows)
        self.assertEqual(accepted, self.rows)
        processed = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')
        self.assertEqual(processed, accepted)
        invalid = load_pois(ROOT / 'tests/fixtures/pois.invalid.synthetic.json')
        for poi in invalid:
            self.assertEqual(poi['city'], 'Тестовый город')
            if 'name' in poi:
                self.assertEqual(poi['name'], 'Тестовый объект 01')
        self.assertNotIn('name', invalid[6])

    def test_cli_preserves_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            output, report = path / 'out.json', path / 'report.json'
            self.assertEqual(self.run_cli('--input', ROOT / 'data/fixtures/pois.synthetic.json',
                                          '--output', output, '--report', report), 0)
            self.assertEqual(load_pois(output), self.rows)
            text = output.read_text(encoding='utf-8')
            self.assertIn('Тестовый объект 01', text)
            self.assertIn('Тестовый город', text)

    def test_invalid_fixture(self):
        accepted, report = process_pois(load_pois(ROOT / 'tests/fixtures/pois.invalid.synthetic.json'))
        self.assertEqual(accepted, [])
        self.assertEqual(report['summary']['rejected_count'], 9)
        self.assertEqual(sum(i['code'] == 'DUPLICATE_ID' for i in report['issues']), 2)

    def test_input_unchanged_and_repeatable(self):
        before = deepcopy(self.rows)
        self.assertEqual(process_pois(self.rows), process_pois(self.rows))
        self.assertEqual(before, self.rows)

    def test_nullable_fields_and_free_entry(self):
        for field in ('coordinates', 'rating', 'popularity', 'estimated_cost'):
            self.poi[field] = None
        self.poi['visit_duration_minutes'] = None
        self.poi['visit_duration_basis'] = None
        self.assertEqual(validate_poi(self.poi), [])
        self.poi['estimated_cost'] = {'amount': 0, 'currency': 'RUB'}
        self.assertEqual(validate_poi(self.poi), [])

    def test_coordinate_boundaries_and_zero(self):
        for lat, lon in [(-90, -180), (90, 180), (0, 0)]:
            self.poi['coordinates'] = dict(latitude=lat, longitude=lon)
            self.assertEqual(validate_poi(self.poi), [])

    def test_bad_types_and_numbers(self):
        for value in [True, '55', float('nan'), float('inf'), -float('inf'), 91]:
            with self.subTest(value=value):
                self.poi['coordinates']['latitude'] = value
                self.assertTrue(validate_poi(self.poi))
        for categories in [[], ['museums', 'museums'], ['unknown'], [{}], None]:
            p = deepcopy(self.rows[0]); p['categories'] = categories
            self.assertTrue(validate_poi(p))

    def test_missing_fields_and_nonobjects(self):
        for field in self.poi:
            p = deepcopy(self.poi); del p[field]
            self.assertTrue(validate_poi(p), field)
        for value in [None, [], 'text', 1]:
            self.assertTrue(validate_poi(value))

    def test_rating_and_duration_rules(self):
        for rating in [dict(value=6, scale_min=0, scale_max=5),
                       dict(value=0, scale_min=0, scale_max=0), {'value': 4}]:
            p = deepcopy(self.poi); p['rating'] = rating
            self.assertTrue(validate_poi(p))
        self.poi['visit_duration_basis'] = None
        self.assertTrue(validate_poi(self.poi))

    def test_provenance(self):
        self.poi['provenance']['is_synthetic'] = False
        self.assertTrue(validate_poi(self.poi))
        self.poi['provenance']['retrieved_at'] = '2026-11-16T12:00:00+03:00'
        self.assertEqual(validate_poi(self.poi), [])
        for value in ['2026-11-16', '2026-11-16T12:00:00', 'invalid']:
            self.poi['provenance']['retrieved_at'] = value
            self.assertTrue(validate_poi(self.poi))

    def test_invalid_url_is_record_error(self):
        self.poi['provenance']['source_url'] = 'https://['
        accepted, report = process_pois([self.poi])
        self.assertEqual(accepted, [])
        self.assertEqual(report['summary']['rejected_count'], 1)

    def test_duplicate_ids_all_rejected_independent_of_order(self):
        other = deepcopy(self.poi); other['name'] = 'Другой объект'
        for rows in ([self.poi, other], [other, self.poi]):
            accepted, report = process_pois(rows)
            self.assertEqual(accepted, [])
            self.assertEqual(report['summary']['rejected_count'], 2)

    def test_conflicts_warn_without_merging(self):
        other = deepcopy(self.poi); other['id'] = 'synthetic:other'
        other['name'] = '  ' + other['name'].upper() + '  '
        accepted, report = process_pois([self.poi, other])
        self.assertEqual(len(accepted), 2)
        codes = [i['code'] for i in report['issues']]
        self.assertEqual(codes.count('SOURCE_RECORD_CONFLICT'), 2)
        self.assertEqual(codes.count('POSSIBLE_DUPLICATE_NAME'), 2)

    def test_empty_and_mixed_sources(self):
        accepted, report = process_pois([])
        self.assertEqual(accepted, [])
        self.assertEqual(report['data_kind'], 'empty')
        other = deepcopy(self.rows[1]); other['provenance']['source_id'] = 'another'
        with self.assertRaises(ValueError):
            process_pois([self.poi, other])
        with self.assertRaises(ValueError):
            process_pois({})

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(list(map(str, args)))

    def test_cli_output_protection_and_input_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, output, report = (path / n for n in ('input.json', 'out.json', 'report.json'))
            source.write_text(json.dumps(self.rows), encoding='utf-8')
            original = source.read_bytes()
            args = ['--input', source, '--output', output, '--report', report]
            self.assertEqual(self.run_cli(*args), 0)
            self.assertEqual(len(load_pois(output)), 16)
            self.assertEqual(self.run_cli(*args), 2)
            self.assertEqual(self.run_cli(*args, '--overwrite'), 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(self.run_cli('--input', source, '--output', source,
                                          '--report', report, '--overwrite'), 2)

    def test_cli_bad_json_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, output, report = (path / n for n in ('in.json', 'out.json', 'report.json'))
            for content in ['{broken', '[NaN]', '[Infinity]', '{}']:
                source.write_text(content, encoding='utf-8')
                self.assertEqual(self.run_cli('--input', source, '--output', output,
                                              '--report', report), 2)
                self.assertFalse(output.exists())
                self.assertFalse(report.exists())

    def test_cli_rejected_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(self.run_cli('--input', ROOT / 'tests/fixtures/pois.invalid.synthetic.json',
                                          '--output', path / 'out.json',
                                          '--report', path / 'report.json'), 1)


if __name__ == '__main__':
    unittest.main()
