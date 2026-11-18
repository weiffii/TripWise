"""Smoke-test the additive builder's 100-POI merge and template transformations."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tripwise_builder',ROOT/'scripts/build_shanghai_demo.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

# This is a reduced structurally equivalent template, NOT a copy of repository UI.
TEMPLATE='''<!doctype html><html><head><style></style></head><body>
<form><label class="field" for="budget">Бюджет на всю поездку, ¥
На одного человека: посещения и питание. Юани (CNY). Цена транспорта не включена.</label></form>
<section class="panel recommendations">Recommendations</section>
<script>__SHANGHAI_ENGINE__</script><script>
const DATA=__SHANGHAI_DATA__;
const money=value=>Number.isFinite(Number(value))?Math.round(Number(value)).toLocaleString('ru-RU')+' ¥':'—';
$('interests').innerHTML=Object.entries(CATEGORIES).map(([id,label])=>`<label class="interest"><input type="checkbox" name="interest" value="${id}" ${id==='museums'||id==='history'?'checked':''}><span>${escapeHTML(label)}</span></label>`).join('');
function optionsFromForm(){return{interests:chosen('interest'),pace:$('pace').value,days:Number($('days').value),start_date:$('start-date').value,day_start:$('day-start').value,day_end:$('day-end').value,budget_cny:Number($('budget').value),required_ids:chosen('required')};}
function renderResult(){renderDay();renderRecommendations();}
const text=event.transport_assumption==='synthetic_urban_transfer'?'Условный городской переезд с запасом':'Пеший переход с запасом';
const price=money(poi?poi.price_cny:0);
$('trip-form').addEventListener('submit',runPlan);
</script></body></html>'''

class BuilderTests(unittest.TestCase):
    def test_additive_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for rel in ('data/demo/shanghai/pois.json','data/demo/shanghai/pois-extra.json',
                'ai_ds/web_demo/shanghai-engine-v2.js','examples/shanghai-demo/template.html'):
                (root/rel).parent.mkdir(parents=True,exist_ok=True)
            data=json.loads((ROOT/'data/demo/shanghai/pois-extra.json').read_text(encoding='utf8'))
            originals=[]
            for i,p in enumerate(data['pois'][:46]):
                q=dict(p,id=f'original-demo-{i+1:02d}',name=f'Original POI {i+1}')
                originals.append(q)
            (root/'data/demo/shanghai/pois.json').write_text(json.dumps({'is_demo':True,'pois':originals}),encoding='utf8')
            (root/'data/demo/shanghai/pois-extra.json').write_text(json.dumps(data),encoding='utf8')
            (root/'ai_ds/web_demo/shanghai-engine-v2.js').write_text(
                (ROOT/'ai_ds/web_demo/shanghai-engine-v2.js').read_text(encoding='utf8'),encoding='utf8')
            (root/'examples/shanghai-demo/template.html').write_text(TEMPLATE,encoding='utf8')
            output=root/'artifacts/tripwise-shanghai.html'
            result=builder.build(root,output)
            html=output.read_text(encoding='utf8')
            self.assertEqual(result['pois'],100)
            self.assertIn('primary_interests',html)
            self.assertIn('group-senior',html)
            self.assertIn('synthetic-v2-01-01',html)
            self.assertIn('function renderExplanation()',html)
            self.assertNotIn('__SHANGHAI_DATA__',html)
            self.assertNotIn('__SHANGHAI_ENGINE__',html)
            self.assertTrue(result['offline'])

if __name__=='__main__':
    unittest.main()
