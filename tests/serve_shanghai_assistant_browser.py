"""Explicit mock-LLM server for browser tests; real HTTP, store and JS router.

Not a production interpreter and never a fallback for unavailable OpenRouter.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_ds.route_assistant import AssistantError
from ai_ds.shanghai_assistant import ShanghaiAssistantService
from scripts.serve_shanghai_demo import create_server


def interpretation(payload):
    text = payload['text']
    days = payload['days']
    required = set(payload.get('required_poi_ids', []))

    def visit(day, index=0):
        return [p for p in day['visits'] if p['type'] == 'visit' and p['poi_id'] not in required][index]['poi_id']

    if text == 'Замени музей на парк':
        return {'action': 'needs_clarification', 'operations': [], 'clarification': 'В какой день заменить музей?'}
    if text in ('Во второй', 'Нет, лучше другой парк во втором дне'):
        if text == 'Во второй':
            assert any('Замени музей на парк' in row['content'] for row in payload['conversation']), 'dialog context was lost'
        # The request does not specify which museum. The mock chooses the longest
        # optional visit, a meaningful less-tiring replacement with real POIs.
        def minutes(value):
            hour, minute = map(int, value.split(':'))
            return hour * 60 + minute
        target = max((p for p in days[1]['visits'] if p['type'] == 'visit' and p['poi_id'] not in required),
                     key=lambda p: minutes(p['end_time']) - minutes(p['start_time']))
        ops = [{'op': 'replace_poi', 'day': days[1]['date'], 'target_poi_id': target['poi_id'],
                'desired_category': 'nature', 'preference': 'alternative'}]
    elif text == 'Убери третью достопримечательность во втором дне':
        ops = [{'op': 'remove_poi', 'poi_id': visit(days[1], 2)}]
    elif text == 'Убери первую необязательную достопримечательность в первом дне и перенеси обед на 14:00':
        ops = [{'op': 'remove_poi', 'poi_id': visit(days[0])},
               {'op': 'set_meal_break', 'day': days[0]['date'], 'meal_kind': 'lunch', 'start_time': '14:00'}]
    elif text == 'Удали обязательное место':
        assert required, 'the test must mark a required place first'
        ops = [{'op': 'remove_poi', 'poi_id': sorted(required)[0]}]
    elif text == 'Добавь красивое место рядом':
        raise AssistantError('INTERPRETER_UNAVAILABLE')
    else:
        raise AssertionError('Unexpected browser test message')
    return {'action': 'propose', 'operations': ops, 'clarification': None}


if __name__ == '__main__':
    server = create_server(0, assistant_service=ShanghaiAssistantService(interpreter=interpretation, mode='mock'))
    print('http://127.0.0.1:%d/tripwise-shanghai.html' % server.server_port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
