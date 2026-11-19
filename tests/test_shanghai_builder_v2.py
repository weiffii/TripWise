"""Current builder preserves input and embeds engine without algorithm patches."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('builder_current',ROOT/'scripts/build_shanghai_demo.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
class BuilderTests(unittest.TestCase):
    def test_current_build_preserves_engine_and_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            pois=[{'id':str(i),'kind':'restaurant' if i<2000 else 'attraction','lat':31.2,'lon':121.4,'names':dict(en='Demo',ru='Demo',zh='Demo')} for i in range(5000)]
            original=json.dumps({'is_demo':True,'pois':pois})
            engine="// const anchors=unique(\nfor(const p of restaurants){if(used.has(p.id)||!suitable(p,x.group))continue;const f=flags(p,state.date);if(!f.available)continue;}"
            files={'data/demo/shanghai/pois.json':original,'ai_ds/web_demo/shanghai-engine.js':engine,'examples/shanghai-demo/template.html':'<script>__SHANGHAI_ENGINE__</script><script>const DATA=__SHANGHAI_DATA__;</script>'}
            for rel,text in files.items():
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
            result=builder.build(root)
            html=Path(result['output']).read_text(encoding='utf-8')
            self.assertEqual(result['pois'],5000)
            self.assertIn(engine,html)
            self.assertNotIn('__SHANGHAI_',html)
            self.assertEqual((root/'data/demo/shanghai/pois.json').read_text(encoding='utf-8'),original)
            self.assertTrue(result['offline_core'])
if __name__=='__main__':unittest.main()
