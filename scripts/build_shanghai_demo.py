"""Собрать автономную демонстрацию Шанхая: данные и алгоритм внутри HTML."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/tripwise-shanghai.html')
    args = parser.parse_args()
    dataset_path = ROOT / 'data/demo/shanghai/pois.json'
    engine_path = ROOT / 'ai_ds/web_demo/shanghai-engine.js'
    template_path = ROOT / 'examples/shanghai-demo/template.html'
    dataset = json.loads(dataset_path.read_text(encoding='utf-8-sig'))
    if not isinstance(dataset, dict) or not isinstance(dataset.get('pois'), list) or not dataset['pois']:
        raise ValueError('Expected nonempty Shanghai demonstration dataset')
    engine = engine_path.read_text(encoding='utf-8-sig')
    if '</script' in engine.lower():
        raise ValueError('Engine contains an unsafe inline script terminator')
    template = template_path.read_text(encoding='utf-8-sig')
    for marker in ('__SHANGHAI_DATA__', '__SHANGHAI_ENGINE__'):
        if template.count(marker) != 1:
            raise ValueError('Expected one template marker: ' + marker)
    data = json.dumps(dataset, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    html = template.replace('__SHANGHAI_DATA__', data).replace('__SHANGHAI_ENGINE__', engine)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()), 'poi_count': len(dataset['pois']),
                      'network_requests': 0, 'runtime': 'browser; recalculates from current form'}))


if __name__ == '__main__':
    main()
