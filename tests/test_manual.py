import sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addons/asset_pipeline/backend'))
from asset_pipeline.service import PipelineService
from test_service import FakeProvider

class ManualTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.s=PipelineService(self.tmp.name,FakeProvider(),poll_interval=.01)
  self.a=self.s.store.create_node({'kind':'concept','label':'A'})['id']
  self.b=self.s.store.create_node({'kind':'subject','label':'B','inputs':[{'node_id':self.a,'role':'image'}]})['id']
 def tearDown(self):
  self.s.stopping.set(); self.tmp.cleanup()
 def test_update_cycle_and_undo(self):
  self.s.dispatch('manual.update',{'node_id':self.a,'patch':{'label':'Renamed'}})
  self.s.dispatch('manual.undo',{}); self.assertEqual(self.s.store.get_node(self.a)['label'],'A')
  self.s.dispatch('manual.redo',{}); self.assertEqual(self.s.store.get_node(self.a)['label'],'Renamed')
  with self.assertRaises(ValueError): self.s.dispatch('manual.update',{'node_id':self.a,'patch':{'inputs':[{'node_id':self.b,'role':'image'}]}})
 def test_copy_template(self):
  result=self.s.dispatch('manual.copy',{'node_ids':[self.b,self.a]})['mapping']
  self.assertEqual(self.s.store.get_node(result[self.b])['inputs'][0]['node_id'],result[self.a])
  self.s.dispatch('manual.undo',{}); self.assertTrue(self.s.store.get_node(result[self.a])['archived'])
  self.s.dispatch('manual.redo',{}); self.assertFalse(self.s.store.get_node(result[self.a]).get('archived',False))
  template=self.s.dispatch('manual.template_save',{'node_ids':[self.a,self.b],'label':'test'})
  loaded=self.s.dispatch('manual.template_load',{'template_id':template['id']})
  self.assertEqual(len(loaded['mapping']),2)
 def test_rebuild_scheduler_no_automatic_submission(self):
  plan=self.s.dispatch('manual.plan',{'node_id':self.a,'mode':'all'})
  self.assertEqual(plan['node_ids'],[self.a,self.b])
  result=self.s.dispatch('manual.rebuild',{'node_ids':plan['node_ids']})
  self.assertEqual(self.s.provider.submissions,0)
  self.s.run_batch(result['batch_id'])
  for _ in range(200):
   batch=self.s.store.get_batch(result['batch_id'])
   if batch['status'] in ('completed','blocked'): break
   time.sleep(.02)
  self.assertEqual(batch['status'],'completed',batch)
  self.assertEqual(self.s.provider.submissions,2)
  self.assertIsNone(self.s.store.get_node(self.a)['current_version'])

 def test_import_drop_position(self):
  paths=[]
  for i in range(4):
   path=Path(self.tmp.name)/('asset%d.png'%i); path.write_bytes(b'preview'); paths.append(str(path))
  nodes=self.s.dispatch('manual.import',{'paths':paths,'position':[-40,250]})
  self.assertEqual([n['position'] for n in nodes],[[-40,250],[240,250],[520,250],[-40,570]])
  for n in nodes:
   self.assertTrue(self.s.store.get_node(n['id'])['current_version'])
  before=len(self.s.store.snapshot()['nodes'])
  with self.assertRaises(ValueError): self.s.dispatch('manual.import',{'paths':paths,'position':[float('nan'),0]})
  self.assertEqual(len(self.s.store.snapshot()['nodes']),before)
