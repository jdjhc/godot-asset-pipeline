import json
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from test_manual import PipelineService, FakeProvider
from asset_pipeline.store import Store

class CanvasTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.s=PipelineService(self.tmp.name,FakeProvider())
 def tearDown(self):
  self.s.stopping.set(); self.tmp.cleanup()
 def board(self,id): return next(b for b in self.s.store.snapshot()['canvases'] if b['id']==id)
 def create(self,name): return self.s.dispatch('canvas.create',{'name':name})['canvas_id']
 def test_shared_assets_independent_positions_and_undo(self):
  n=self.s.dispatch('manual.create',{'node':{'label':'Asset'}})['id']
  b=self.create('第二张')
  self.s.dispatch('canvas.add',{'canvas_id':b,'node_ids':[n],'position':[100,200]})
  self.s.dispatch('canvas.move',{'canvas_id':b,'node_ids':[n],'position':[300,400]})
  self.assertEqual(self.board('default')['placements'][n],[0,0])
  self.assertEqual(self.board(b)['placements'][n],[300,400])
  self.s.dispatch('manual.undo',{})
  self.assertEqual(self.board(b)['placements'][n],[100,200])
  self.s.dispatch('manual.redo',{})
  self.assertEqual(self.board(b)['placements'][n],[300,400])
  self.assertEqual(len(self.s.store.snapshot()['nodes']),1)
 def test_delete_preserves_files_and_restores_canvas(self):
  b=self.create('实验')
  f=Path(self.tmp.name)/'image.png';f.write_bytes(b'asset')
  n=self.s.dispatch('manual.import',{'canvas_id':b,'paths':[str(f)]})[0]['id']
  self.assertNotIn(n,self.board('default')['placements'])
  self.s.dispatch('canvas.delete',{'canvas_id':b})
  node=self.s.store.get_node(n)
  self.assertFalse(node.get('archived',False))
  self.assertTrue(node['current_version'])
  self.s.dispatch('manual.undo',{})
  self.assertIn(n,self.board(b)['placements'])
  self.s.dispatch('manual.redo',{})
  restarted=Store(self.tmp.name).snapshot()
  self.assertNotIn(n,restarted['canvases'][0]['placements'])
  self.assertEqual(len(restarted['nodes']),1)
 def test_rename_last_canvas_and_global_trash(self):
  b=self.create('实验')
  self.s.dispatch('canvas.rename',{'canvas_id':b,'name':'街区'})
  self.assertEqual(self.board(b)['name'],'街区')
  with self.assertRaises(ValueError): self.create('街区')
  n=self.s.dispatch('manual.create',{'canvas_id':b,'node':{'label':'Shared'}})['id']
  self.s.dispatch('canvas.add',{'canvas_id':'default','node_ids':[n]})
  self.s.dispatch('manual.archive',{'node_ids':[n]})
  self.assertTrue(self.s.store.get_node(n)['archived'])
  self.s.dispatch('canvas.delete',{'canvas_id':'default'})
  with self.assertRaises(ValueError): self.s.dispatch('canvas.delete',{'canvas_id':b})
  new=self.s.store.create_node({'label':'New'})['id']
  self.assertIn(new,self.board(b)['placements'])
 def test_migration_once(self):
  n=self.s.store.create_node({'label':'Old','position':[42,84]})['id']
  with contextlib.closing(sqlite3.connect(self.s.store.database)) as db:
   db.execute('DROP TRIGGER canvas_new_node');db.execute('DROP TABLE canvas_nodes');db.execute('DROP TABLE canvases')
   db.execute("DELETE FROM settings WHERE key='canvas_migration'")
   db.commit()
  migrated=Store(self.tmp.name)
  self.assertEqual(migrated.snapshot()['canvases'][0]['placements'][n],[42,84])
  self.assertEqual(len(Store(self.tmp.name).snapshot()['canvases']),1)
 def test_invalid_canvas_does_not_import(self):
  with self.assertRaises(ValueError): self.s.dispatch('manual.create',{'canvas_id':'missing','node':{'label':'No'}})
  self.assertEqual(self.s.store.snapshot()['nodes'],[])
 def test_auto_layout_no_overlap_shared_positions_and_undo(self):
  ids=[]
  for i in range(6):
   edges=[] if i in (0,5) else [{'node_id':ids[0] if i<4 else ids[1],'role':'image'}]
   ids.append(self.s.store.create_node({'label':str(i),'inputs':edges,'position':[0,0]})['id'])
  other=self.create('Other')
  self.s.dispatch('canvas.add',{'canvas_id':other,'node_ids':ids,'position':[33,44]})
  before=self.board('default')['placements']; untouched=self.board(other)['placements']
  sizes={n:[300,410] for n in ids}
  self.s.dispatch('canvas.layout',{'canvas_id':'default','sizes':sizes})
  pos=self.board('default')['placements']
  for n in ids:
   for edge in self.s.store.get_node(n)['inputs']:
    self.assertLess(pos[edge['node_id']][0]+300,pos[n][0])
   for m in ids:
    if n==m: continue
    x,y=pos[n];xx,yy=pos[m]
    self.assertTrue(x+300<=xx or xx+300<=x or y+410<=yy or yy+410<=y)
  self.assertEqual(self.board(other)['placements'],untouched)
  self.s.dispatch('manual.undo',{})
  self.assertEqual(self.board('default')['placements'],before)
  self.s.dispatch('manual.redo',{})
  self.assertEqual(self.board('default')['placements'],pos)
