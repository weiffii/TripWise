#!/usr/bin/env python3
"""Update POI JSON literals and static count labels; do not touch route logic."""
from __future__ import annotations
import argparse,json,re,hashlib
from pathlib import Path

HTMLS={'artifacts/tripwise-shanghai.html':'DATA','artifacts/tripwise-shanghai-coverage.html':'POIS'}

def replace_json(source,name,value):
    key='const '+name+'='
    start=source.index(key)+len(key)
    old,offset=json.JSONDecoder().raw_decode(source[start:])
    if name=='DATA':
        if not (isinstance(old,dict) and len(old.get('pois',[])) in (2000,5000)):
            raise ValueError('Unexpected embedded DATA catalogue')
        old['pois']=value['pois']
        for k in ['description','catalog_provenance','synthetic_placement_notice']:
            if k in value:old[k]=value[k]
        final=old
    else:
        if not(isinstance(old,list) and len(old) in (2000,5000)):raise ValueError('Unexpected embedded coverage POIS')
        final=[{'id':p['id'],'name':p.get('name_ru') or p['name'],
             'en':p.get('name_en') or p['name'], 'zh':p.get('name_zh') or p['name'],
             'lat':p['lat'],'lon':p['lon'],'categories':p['categories'],
             'kind':p['kind'],'area':p.get('area','Шанхай')}
             for p in value['pois']]
    literal=json.dumps(final,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    return source[:start]+literal+source[start+offset:]

def update_text(path,text):
    if path.endswith('tripwise-shanghai.html'):
        replacements={
          '<span class="tag">2000 объектов</span>':'<span class="tag">5000 объектов</span>',
          '<strong>Тестовая база из 2000 объектов.</strong> 500 исходных POI с сохранёнными координатами и 1500 вымышленных заведений и объектов посещения.':
          '<strong>Тестовая база из 5000 объектов.</strong> 2000 исходных POI сохранены без изменений; добавлено 3000 вымышленных мест. 4500 объектов в общей базе — синтетические. Физическое существование новых точек, здания и подходы по улицам НЕ проверены.',
          '<p class="section-caption">2000 объектов · названия и координаты из источников, параметры синтетические</p>':
          '<p class="section-caption">5000 объектов · часть названий и координат синтетические; здания и дорожные подходы не подтверждены</p>',
          "[['2000','объектов в базе','500 исходных POI + 1500 вымышленных мест (координаты моделируются)']":
          "[['5000','объектов в базе','2000 сохранённых POI + 3000 новых синтетических мест; проверка дорожной сети не проводилась']"
        }
    else:
        replacements={
          'TripWise — карта покрытия Шанхая · 2000 мест':'TripWise — карта покрытия Шанхая · 5000 мест',
          '<strong>Все 2000 мест, фильтры и масштабирование работают даже без интернета.</strong>':
          '<strong>Все 5000 модельных и исходных мест, фильтры и масштабирование работают даже без интернета.</strong>',
          'aria-label="Интерактивная карта 2000 мест Шанхая"':'aria-label="Интерактивная карта 5000 мест Шанхая"',
        }
    for old,new in replacements.items():
        if old not in text:raise ValueError('Static caption not found in original HTML: '+old[:90])
        text=text.replace(old,new,1)
    return text

def engine_sha(s):
    chunks=re.findall(r'<script[^>]*>(.*?)</script>',s,re.S)
    if len(chunks)<4:raise ValueError('Main HTML has unexpected JS scripts')
    return [hashlib.sha256(x.encode('utf8')).hexdigest() for x in chunks[:3]]

def refresh(source_root,dest_root,data_file):
    data=json.loads(data_file.read_text(encoding='utf8'))
    assert len(data['pois'])==5000
    report={}
    for rel,name in HTMLS.items():
        old=(source_root/rel).read_text(encoding='utf8')
        updated=update_text(rel,replace_json(old,name,data))
        if name=='DATA':
            assert engine_sha(old)==engine_sha(updated),'Routing/transport/map algorithms changed!'
            report['unchanged_engine_sha256']=engine_sha(old)
        target=dest_root/rel;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(updated,encoding='utf8')
        report[rel]={'before_sha256':hashlib.sha256(old.encode('utf8')).hexdigest(),
            'after_sha256':hashlib.sha256(updated.encode('utf8')).hexdigest(),
            'embedded_count':5000,'javascript_algorithms_unmodified':True}
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--source-root',type=Path,required=True)
    ap.add_argument('--output-root',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data/demo/shanghai/pois.json')
    a=ap.parse_args();print(json.dumps(refresh(a.source_root,a.output_root,a.data),ensure_ascii=False,indent=2))
