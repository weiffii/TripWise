"""Демонстрационный CLI: операции в памяти, без внешних запросов."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AssistantCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B',
            str(ROOT / 'scripts/demo_route_assistant.py'), *args],
            cwd=ROOT, capture_output=True)

    def test_default_preview_is_labelled_mock_and_does_not_apply(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        value = json.loads(result.stdout)
        self.assertEqual(value['interpreter_kind'], 'synthetic_fixture')
        self.assertTrue(value['original_unchanged_before_confirmation'])
        self.assertEqual(value['stored_version'], 1)
        self.assertIsNone(value['decision_result'])
        self.assertEqual(value['preview']['candidate_route']['status'], 'unknown')

    def test_confirm_requires_explicit_draft_consent(self):
        result = self.run_cli('--decision', 'confirm')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)['error']['code'],
                         'DRAFT_CONFIRMATION_REQUIRED')

    def test_explicit_confirmation_changes_only_in_memory_version(self):
        result = self.run_cli('--decision', 'confirm', '--allow-draft')
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        value = json.loads(result.stdout)
        self.assertEqual(value['stored_version'], 2)
        self.assertTrue(value['decision_result']['is_draft'])
        self.assertEqual(value['decision_result']['decision'], 'confirmed')
        self.assertEqual(value['storage'], 'in_memory_demo_only')

    def test_reject_keeps_original_version(self):
        result = self.run_cli('--decision', 'reject')
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        value = json.loads(result.stdout)
        self.assertEqual(value['stored_version'], 1)
        self.assertEqual(value['decision_result']['decision'], 'rejected')


    def test_reviewed_three_pois_preserves_required_and_known_paths(self):
        result = self.run_cli('--scenario-prefix', 'moscow.reviewed-three',
                              '--decision', 'confirm', '--allow-draft')
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        value = json.loads(result.stdout)
        self.assertTrue(value['original_unchanged_before_confirmation'])
        self.assertEqual(value['interpreter_kind'], 'synthetic_fixture')
        self.assertEqual(value['stored_version'], 2)
        route = value['decision_result']['route']
        visits = [v for d in route['days'] for v in d['visits']]
        legs = [leg for d in route['days'] for leg in d['legs']]
        profile = json.loads((ROOT / 'examples/routing/v02/moscow.reviewed-three.preferences.json').read_text(encoding='utf-8'))
        self.assertEqual({v['poi_id'] for v in visits}, set(profile['required_poi_ids']))
        self.assertEqual(len(visits), 3)
        self.assertEqual(len(legs), 2)
        self.assertTrue(all(leg['geometry']['type'] == 'LineString' and
                            leg['distance_meters'] > 0 and leg['duration_seconds'] > 0
                            for leg in legs))
        self.assertEqual(route['status'], 'unknown')
        self.assertEqual(route['constraint_checks']['time'], 'passed')
        self.assertEqual(route['constraint_checks']['budget'], 'unknown')
        self.assertEqual(route['budget_summary']['known_total'], 1800)


if __name__ == '__main__':
    unittest.main()
