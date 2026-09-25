import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addons/asset_pipeline/backend'))
from asset_pipeline.store import Store
from asset_pipeline.subject_selection import create,PROMPT
class SelectionTests(unittest.TestCase):
 def test_selection_pins_original_and_validates_bounds(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(d); p=Path(d)/'a.png'; p.write_bytes(b'original')
   q=Path(d)/'marked.png'; q.write_bytes(b'marked')
   n=s.create_node({'kind':'reference','label':'source'})
   v=s.import_version(n['id'],[{'path':str(p),'role':'image'}])
   args={'node_id':n['id'],'version_id':v['id'],'source_path':v['files'][0]['path'],'annotated_path':str(q),'box':[2,3,20,30],'image_size':[100,100],'color':'00ffff'}
   result=create(s,args)
   plan=s.plan(result['subject_id'])
   self.assertEqual(plan['prompt'],PROMPT)
   self.assertEqual([i['role'] for i in plan['inputs']],['reference','selection_reference'])
   self.assertEqual(s.get_node(result['annotation_id'])['versions'][0]['metadata']['box_pixels'],args['box'])
   p.write_bytes(b'changed'); s.import_version(n['id'],[{'path':str(p),'role':'image'}])
   self.assertFalse(s.get_node(result['subject_id'])['stale'])
   count=len(s.snapshot()['nodes'])
   with self.assertRaises(ValueError): create(s,{**args,'box':[90,0,20,20]})
   self.assertEqual(len(s.snapshot()['nodes']),count)
