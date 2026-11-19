"""Собрать один автономный HTML Shanghai Lab из существующих файлов TripWise."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(root: Path = ROOT, output: Path | None = None) -> dict:
    data = json.loads((root / 'data/demo/shanghai/pois.json').read_text(encoding='utf-8-sig'))
    assert data['is_demo'] is True and len(data['pois']) == 500, 'Нужно ровно 500 демонстрационных объектов.'
    transport_path = root / 'data/demo/shanghai/transport.json'
    if transport_path.exists():
        data['transport'] = json.loads(transport_path.read_text(encoding='utf-8-sig'))
    ids = [p['id'] for p in data['pois']]
    assert len(ids) == len(set(ids)), 'Повторяющиеся идентификаторы объектов.'
    template = (root / 'examples/shanghai-demo/template.html').read_text(encoding='utf-8-sig')
    engine = (root / 'ai_ds/web_demo/shanghai-engine.js').read_text(encoding='utf-8-sig')
    assert template.count('__SHANGHAI_ENGINE__') == 1 and template.count('__SHANGHAI_DATA__') == 1
    assert '</script' not in engine.lower(), 'Нельзя вставлять закрывающий script в код движка.'
    modules = {}
    for marker, filename in [('__SHANGHAI_TRANSIT__', 'shanghai-transit.js'),
                             ('__SHANGHAI_MAP__', 'shanghai-map.js')]:
        if marker not in template:
            continue
        module_path = root / 'ai_ds/web_demo' / filename
        source = module_path.read_text(encoding='utf-8-sig')
        assert '</script' not in source.lower(), 'Нельзя вставлять закрывающий script в код модуля.'
        modules[marker] = source
    rendered = (template.replace('__SHANGHAI_ENGINE__', engine)
        .replace('__SHANGHAI_DATA__', json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')))
    for marker, source in modules.items():
        if marker in rendered:
            assert rendered.count(marker) == 1, 'Повторная точка вставки: ' + marker
            rendered = rendered.replace(marker, source)
    output = output or root / 'artifacts/tripwise-shanghai.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding='utf-8')
    return {'output': str(output), 'pois': len(data['pois']), 'offline_core': True, 'optional_online_services': ['OSM tiles','OSRM foot and car']}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=None)
    args = ap.parse_args()
    print(json.dumps(build(output=args.output), ensure_ascii=False))
