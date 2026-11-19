"""Opt-in live free-provider checks. Never collected by unittest discovery.

Run only against the local live API. No keys/cookies/tokens enter the report.
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8771')
    parser.add_argument('--max-calls', type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    options = dict(primary_interests=['museums', 'history'], secondary_interests=['nature'],
                   days=2, start_date='2026-11-20', day_start='09:00', day_end='21:00',
                   pace='balanced', transport='mixed', budget_cny=5000,
                   group=dict(adult=1, child=0, senior=0, pet=0), required_ids=[])
    code = "const fs=require('fs'),a=require('./ai_ds/web_demo/shanghai-engine.js');const d=JSON.parse(fs.readFileSync('data/demo/shanghai/pois.json','utf8'));d.transport=JSON.parse(fs.readFileSync('data/demo/shanghai/transport.json','utf8'));const p=a.planTrip(d,JSON.parse(fs.readFileSync(0,'utf8')));delete p.ranking;process.stdout.write(JSON.stringify(p));"
    plan = json.loads(subprocess.check_output([shutil.which('node'), '-e', code], input=json.dumps(options).encode(), cwd=ROOT))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = None

    def call(path, body=None):
        headers = {'Content-Type': 'application/json', 'Origin': args.url}
        if body is not None:
            headers['X-TripWise-CSRF'] = csrf
        request = urllib.request.Request(args.url + '/api/assistant/' + path,
                                         data=None if body is None else json.dumps(body, ensure_ascii=False).encode(), headers=headers)
        with opener.open(request, timeout=100) as response:
            return json.load(response)

    status = call('status')
    assert status.get('available') and status.get('mode') == 'live', 'Live API not configured'
    csrf = status['csrf_token']
    registered = call('register', dict(options=options, plan=plan, request_id=uuid4().hex))
    assert registered.get('route_id'), registered.get('message')
    current = {key: registered[key] for key in ('route_id', 'version', 'poi_snapshot_id')}
    texts = ['В первом дне убери третью достопримечательность.',
             'В первом дне перенеси обед на 14:00.',
             'Во втором дне замени один музей на парк поблизости.']
    report = {'provider_mode': 'live', 'mock': False, 'max_calls': args.max_calls,
              'initial_status': plan['status'], 'initial_visits': plan['summary']['visit_count'], 'dialogue': []}
    for index, text in enumerate(texts[:args.max_calls]):
        started = time.monotonic()
        result = call('message', dict(route_id=current['route_id'], base_version=current['version'],
                                     poi_snapshot_id=current['poi_snapshot_id'], text=text, request_id=uuid4().hex))
        row = {'user': text, 'type': result.get('type'), 'message': result.get('message'),
               'code': result.get('code'), 'elapsed_seconds': round(time.monotonic() - started, 3)}
        if result.get('type') == 'proposal':
            candidate = result['plan']
            row.update(diff=result.get('diff'), candidate_status=candidate['status'], checks=candidate['checks'])
            decision = call('decide', dict(proposal_id=result['proposal_id'], base_version=result['base_version'],
                                          decision='confirm', request_id=uuid4().hex))
            assert decision.get('decision') == 'confirmed', decision.get('message')
            assert decision['version'] == current['version'] + 1
            updated = decision['plan']
            assert updated['days'][1 if index < 2 else 0] == plan['days'][1 if index < 2 else 0], 'Untouched day changed'
            if index == 0:
                assert updated['summary']['visit_count'] == plan['summary']['visit_count'] - 1
            if index == 1:
                assert next(e for e in updated['days'][0]['events'] if e.get('meal_kind') == 'lunch')['start_min'] == 840
            row.update(confirmed=True, version=decision['version'])
            current['version'] = decision['version']
            plan = updated
        report['dialogue'].append(row)
        path = ROOT / 'reports/assistant-2026-11-20/live-provider.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({key: row.get(key) for key in ('type', 'code', 'confirmed', 'version', 'elapsed_seconds')}, ensure_ascii=True), flush=True)
        if result.get('type') == 'unavailable':
            break


if __name__ == '__main__':
    main()
