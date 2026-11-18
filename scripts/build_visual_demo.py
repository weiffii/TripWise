"""Создать автономный просмотр результатов настоящих алгоритмов без API."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_ds.recommendation import recommend
from ai_ds.multiday import build_route, validate_route


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


def main():
    fixed_time = datetime(2026, 11, 18, tzinfo=timezone.utc)
    categories = ['museums', 'history', 'nature', 'cafes', 'entertainment',
                  'shopping', 'viewpoints', 'unusual']
    output = {'recommendations': {}, 'routes': {}}
    for kind, poi_file, profile_file in [
        ('synthetic', 'data/processed/pois.synthetic.validated.json', 'examples/preferences/history.json'),
        ('real', 'data/geoapify/ru/20261117T173836.768006Z/pois.validated.json', 'examples/preferences/real/moscow.museums.json')]:
        pois, profile = read(poi_file), read(profile_file)
        names = {p['id']: p for p in pois}
        output['recommendations'][kind] = {}
        for category in categories:
            preferences = deepcopy(profile)
            preferences.update(primary_interests=[category], secondary_interests=[], required_poi_ids=[], top_k=5)
            result = recommend(preferences, pois, evaluated_at=fixed_time)
            output['recommendations'][kind][category] = {
                'items': [dict(name=names[item['poi_id']]['name'],
                               categories=names[item['poi_id']]['categories'],
                               match=category in names[item['poi_id']]['categories'])
                          for item in result['items']],
                'warnings': result['warnings']}
    for kind, prefix in [('real', 'moscow.reviewed-three'), ('synthetic', None)]:
        folder = 'examples/routing/v02/'
        pois = read(folder + (prefix + '.pois.json' if prefix else 'pois.synthetic.json'))
        profile = read(folder + (prefix + '.preferences.json' if prefix else 'preferences.synthetic.json'))
        scenario = read(folder + (prefix + '.scenario.json' if prefix else 'scenario.synthetic.json'))
        output['routes'][kind] = {'pois': {p['id']: {'name': p['name'], 'coordinates': p['coordinates']}
                                         for p in pois}, 'cases': {}}
        for pace in ['relaxed', 'balanced', 'intensive']:
            for budget in ['normal', 'low']:
                preferences = deepcopy(profile)
                preferences['pace'] = pace
                if kind == 'real' and budget == 'low':
                    preferences['budget']['amount'] = 1000
                recommendation = recommend(preferences, pois, evaluated_at=fixed_time)
                route = build_route(preferences, pois, recommendation,
                                    scenario['travel_times'], scenario['routing_context'])
                validation = validate_route(route, preferences, pois, recommendation,
                                            scenario['travel_times'], scenario['routing_context'])
                # Для успешно построенного расписания проверяем воспроизводимость.
                if not route['unplaced_required_poi_ids']:
                    assert validation['valid'], validation['errors']
                output['routes'][kind]['cases'][pace + '-' + budget] = route
    template = (ROOT / 'examples/visual-demo/template.html').read_text(encoding='utf-8')
    data = json.dumps(output, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    assert template.count('__DEMO_DATA__') == 1
    destination = ROOT / 'artifacts/tripwise-demo.html'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(template.replace('__DEMO_DATA__', data), encoding='utf-8')
    print(json.dumps({'output': str(destination), 'network_requests': 0,
                      'recommendation_cases': 16, 'route_cases': 12}))


if __name__ == '__main__':
    main()
