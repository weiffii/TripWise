"""Rebuild existing Shanghai HTML with all 2000 supplied records. No external data fetch."""
from __future__ import annotations
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def build(root:Path=ROOT,output:Path|None=None)->dict:
    path=root/'data/demo/shanghai/pois.json'
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    assert data.get('is_demo') is True and len(data['pois'])==2000, 'Expected 2000 demo POIs'
    assert len({p['id'] for p in data['pois']})==2000, 'Duplicate IDs'
    assert sum(p['kind']=='restaurant' for p in data['pois'])==850, 'Expected 850 restaurants'
    for p in data['pois']:
        assert -90<=p['lat']<=90 and -180<=p['lon']<=180
        assert p['names'].get('en') and p['names'].get('ru') and p['names'].get('zh')
    transport_path=root/'data/demo/shanghai/transport.json'
    if transport_path.exists():data['transport']=json.loads(transport_path.read_text(encoding='utf-8-sig'))
    template=(root/'examples/shanghai-demo/template.html').read_text(encoding='utf-8-sig')
    engine=(root/'ai_ds/web_demo/shanghai-engine.js').read_text(encoding='utf-8-sig')
    assert template.count('__SHANGHAI_ENGINE__')==1 and template.count('__SHANGHAI_DATA__')==1
    # Fast radius shortlist before expensive route calls when catalog reaches 2000 POIs.
    meal_line="for(const p of restaurants){if(used.has(p.id)||!suitable(p,x.group))continue;const f=flags(p,state.date);if(!f.available)continue;"
    if meal_line in engine and "const anchors=unique(" in engine:
        engine=engine.replace(meal_line,
            "for(const p of restaurants){if(used.has(p.id)||!suitable(p,x.group))continue;"+
            "if(anchors.some(Boolean)&&!anchors.some(a=>a&&distance(a,p)<=2500))continue;"+
            "const f=flags(p,state.date);if(!f.available)continue;",1)
    modules={}
    for marker,name in [('__SHANGHAI_TRANSIT__','shanghai-transit.js'),('__SHANGHAI_MAP__','shanghai-map.js')]:
        if marker in template:modules[marker]=(root/'ai_ds/web_demo'/name).read_text(encoding='utf-8-sig')
    rendered=template.replace('__SHANGHAI_ENGINE__',engine).replace('__SHANGHAI_DATA__',json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<','\\u003c'))
    for marker,src in modules.items():rendered=rendered.replace(marker,src)
    output=output or root/'artifacts/tripwise-shanghai.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(rendered,encoding='utf-8')
    return {'pois':2000,'output':str(output)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path)
    a=p.parse_args();print(build(output=a.output))
