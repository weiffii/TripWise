"""Сборка актуального интерфейса с локальным транспортом и картой."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shanghai_map_builder', ROOT / 'scripts/build_shanghai_demo.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ShanghaiMapBuilderTests(unittest.TestCase):
    def fixture(self, root):
        files = {
            'data/demo/shanghai/pois.json': json.dumps({'is_demo': True, 'pois': [
                {'id': str(i), 'name': '</script>test' if i == 0 else 'demo', 'kind': 'restaurant' if i < 2000 else 'attraction', 'lat': 31.2, 'lon': 121.4, 'names': {'en':'Demo','ru':'Demo','zh':'Demo'}} for i in range(5000)]}),
            'data/demo/shanghai/transport.json': json.dumps({'schema_version': 'shanghai-transport-v1'}),
            'ai_ds/web_demo/shanghai-engine.js': 'globalThis.TripWiseShanghai={};',
            'ai_ds/web_demo/shanghai-transit.js': 'globalThis.TripWiseTransit={};',
            'ai_ds/web_demo/shanghai-map.js': 'globalThis.TripWiseMap={};',
            'examples/shanghai-demo/template.html': '<script>__SHANGHAI_TRANSIT__</script><script>__SHANGHAI_ENGINE__</script><script>__SHANGHAI_MAP__</script><script>const DATA=__SHANGHAI_DATA__;</script>',
        }
        for rel, value in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value, encoding='utf-8')

    def test_inlines_all_local_modules_transport_and_escapes_script_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            result = builder.build(root)
            html = Path(result['output']).read_text(encoding='utf-8')
            self.assertEqual(result['pois'], 5000)
            self.assertIn('TripWiseMap', html)
            self.assertIn('shanghai-transport-v1', html)
            self.assertIn('\\u003c/script>', html)
            self.assertNotIn('__SHANGHAI_', html)
            self.assertTrue(result['offline_core'])

    def test_missing_map_module_fails_before_delivering_a_broken_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            # A fixture without the required local map file represents an incomplete unpack.
            module = root / 'ai_ds/web_demo/shanghai-map.js'
            module.rename(module.with_suffix('.missing'))
            with self.assertRaises(FileNotFoundError):
                builder.build(root)

    def test_inline_script_terminator_in_module_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / 'ai_ds/web_demo/shanghai-map.js').write_text('</script>', encoding='utf-8')
            with self.assertRaises(AssertionError):
                builder.build(root)


if __name__ == '__main__':
    unittest.main()
