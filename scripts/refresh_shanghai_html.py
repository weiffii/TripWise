"""Refresh embedded Shanghai JSON without touching JS algorithms or UI markup.

Run from the TripWise repository root:
    python scripts/refresh_shanghai_html.py

Changes only the JSON literals of const DATA and const POIS. Both HTML files
remain self-contained and require no data fetch to show POIs.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def patch_literal(html: str, name: str, value) -> str:
    needle = 'const ' + name + '='
    start = html.find(needle)
    if start < 0:
        raise ValueError(f'Embedded variable {name} not found')
    begin = start + len(needle)
    old_value, end = json.JSONDecoder().raw_decode(html[begin:])
    if name == 'DATA':
        if not isinstance(old_value,dict) or len(old_value.get('pois',[])) != 2000:
            raise ValueError('Unexpected Shanghai algorithm data structure')
        # Transport/algorithm data other than POIs remains as in user's current HTML.
        old_value['pois']=value['pois']
        old_value['description']=value.get('description',old_value.get('description',''))
        old_value['synthetic_placement_notice']=value.get('synthetic_placement_notice',{})
        value=old_value
    elif name == 'POIS':
        if not isinstance(old_value,list) or len(old_value)!=2000:
            raise ValueError('Unexpected Shanghai coverage data structure')
        value=[{'id':p['id'],'name':p['names'].get('ru') or p['name'],
                'en':p['names'].get('en') or p['name'],
                'zh':p['names'].get('zh') or p['name'],
                'lat':p['lat'],'lon':p['lon'],'categories':p['categories'],
                'kind':p['kind'],'area':p.get('area','Шанхай')}
               for p in value['pois']]
    replacement=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    return html[:begin]+replacement+html[begin+end:]


def refresh(root:Path):
    data=json.loads((root/'data/demo/shanghai/pois.json').read_text(encoding='utf8'))
    assert len(data['pois'])==2000
    for relative,name in [('artifacts/tripwise-shanghai.html','DATA'),
                          ('artifacts/tripwise-shanghai-coverage.html','POIS')]:
        target=root/relative
        source=target.read_text(encoding='utf8')
        fresh=patch_literal(source,name,data)
        target.write_text(fresh,encoding='utf8')
        print(f'Updated {target}: {len(data["pois"])} POIs')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    args=parser.parse_args();refresh(args.root)
