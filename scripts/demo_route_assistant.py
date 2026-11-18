"""Локальный preview/confirm/reject Assistant; LLM mock по умолчанию."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_ds.openrouter import OpenRouterInterpreter
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, RouteAssistant


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario-dir', type=Path,
                        default=ROOT / 'examples/routing/v02')
    parser.add_argument('--scenario-prefix',
                        choices=('moscow.real', 'moscow.reviewed-three'),
                        default='moscow.real')
    parser.add_argument('--live', action='store_true',
                        help='Один настоящий бесплатный OpenRouter-запрос')
    parser.add_argument('--decision', choices=('preview', 'confirm', 'reject'),
                        default='preview')
    parser.add_argument('--allow-draft', action='store_true')
    args = parser.parse_args(argv)
    try:
        def read(name):
            return json.loads((args.scenario_dir / name).read_text(encoding='utf-8-sig'))
        pois = read(args.scenario_prefix + '.pois.json')
        profile = read(args.scenario_prefix + '.preferences.json')
        scenario = read(args.scenario_prefix + '.scenario.json')
        route = read(args.scenario_prefix + '.draft.json')
        context = scenario['routing_context']
        day = profile['date_start']
        store = InMemoryRouteStore()
        store.register_route('demo-actor', 'local-demo',
            preferences=profile, pois=pois, recommendation=recommend(profile, pois),
            travel_times=scenario['travel_times'], routing_context=context,
            poi_snapshot_id=context['poi_snapshot_id'], route=route)
        def fixture_interpreter(payload):
            return {'action': 'propose', 'operations': [
                {'op': 'set_day_pace', 'day': day, 'pace': 'relaxed'}],
                'clarification': None}
        interpreter = OpenRouterInterpreter() if args.live else fixture_interpreter
        assistant = RouteAssistant(store, interpreter, proposal_ttl_seconds=300)
        before = store.get_route('demo-actor', 'local-demo')
        preview = assistant.propose('demo-actor', {
            'schema_version': '0.2', 'route_id': 'local-demo', 'base_version': 1,
            'poi_snapshot_id': context['poi_snapshot_id'],
            'text': f'Сделай день {day} спокойным: темп relaxed.'})
        unchanged = before == store.get_route('demo-actor', 'local-demo')
        if not unchanged:
            raise RuntimeError('PREVIEW_MUTATED_SOURCE')
        result = None
        if args.decision != 'preview':
            if preview['validation_status'] != 'ready_for_confirmation':
                raise AssistantError('NO_CONFIRMABLE_PROPOSAL')
            result = assistant.decide('demo-actor', {
                'proposal_id': preview['proposal_id'], 'base_version': 1,
                'decision': args.decision}, allow_draft=args.allow_draft)
        print(json.dumps({
            'scenario_kind': 'real_pois_and_paths_with_estimated_visit_durations',
            'interpreter_kind': 'live_openrouter_free' if args.live else 'synthetic_fixture',
            'original_unchanged_before_confirmation': unchanged,
            'stored_version': store.get_route('demo-actor', 'local-demo')['version'],
            'preview': preview, 'decision_result': result,
            'storage': 'in_memory_demo_only'}, ensure_ascii=False, allow_nan=False))
        return 0
    except AssistantError as error:
        print(json.dumps(error.response(), ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print(json.dumps({'error': {'code': 'DEMO_INPUT_OR_INTERPRETER_ERROR'}}),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
