"""Create an isolated, hard-linked capture workspace; never modify the source project."""
import argparse,json,os,shutil,sqlite3,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addons/asset_pipeline/backend'))
from asset_pipeline.store import Store
from asset_pipeline.service import PipelineService

def prepare(source):
 source=Path(source).resolve(); target=Path(tempfile.mkdtemp(prefix='godot-workflow-demo-')).resolve()
 shutil.copytree(ROOT/'addons',target/'addons',ignore=shutil.ignore_patterns('__pycache__'))
 (target/'project.godot').write_text('config_version=5\n[application]\nconfig/name="Asset Pipeline workflow demo"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
 (target/'.asset_pipeline').mkdir()
 with sqlite3.connect(source/'.asset_pipeline/state.sqlite3') as a,sqlite3.connect(target/'.asset_pipeline/state.sqlite3') as b:a.backup(b)
 store=Store(target); snapshot=store.snapshot()
 for node in snapshot['nodes']:
  for version in node['versions']:
   for f in version['files']:
    src=source/f['path']; dst=target/f['path']
    if src.is_file() and not dst.exists():dst.parent.mkdir(parents=True,exist_ok=True);os.link(src,dst)
 sessions=source/'artifacts/asset_pipeline_video'
 shutil.copytree(sessions,target/'artifacts/asset_pipeline_video',copy_function=os.link)
 service=PipelineService(target)
 with store._connection(write=True) as db:
  db.execute('DELETE FROM canvas_nodes');db.execute('DELETE FROM canvases')
  # No interrupted/running production is resumed in the showcase.
  db.execute('DELETE FROM jobs');db.execute('DELETE FROM batches')
  ids=['zzz_reference','zzz_subject','zzz_front','zzz_right','zzz_rear','zzz_model']
  for row in db.execute('SELECT id,data FROM nodes').fetchall():
   if row['id'] not in ids:
    data=json.loads(row['data']);data['archived']=True
    db.execute('UPDATE nodes SET data=? WHERE id=?',(json.dumps(data),row['id']))
  positions=[[0,250],[320,250],[650,0],[650,280],[650,560],[1000,250]]
  for bid,name,subset in [('select','01 · 主体提取',ids[:1]),('trace','02 · 依赖与版本',ids),('work','03 · 工作画布',ids),('reuse','04 · 复用资产',[])]:
   db.execute('INSERT INTO canvases VALUES (?,?)',(bid,name))
   for i,nid in enumerate(subset):db.execute('INSERT INTO canvas_nodes VALUES (?,?,?)',(bid,nid,json.dumps(positions[i] if bid!='work' else [i%3*220,i//3*170])))
 manifest=next((target/'artifacts/asset_pipeline_video').glob('*/manifest.json'))
 data=json.loads(manifest.read_text());selection=json.loads((manifest.parent/'selection.json').read_text())['selection']
 (manifest.parent/'selection.json').unlink() # Only the demo copy; commit now exercises real node creation.
 (target/'demo.json').write_text(json.dumps({'session':data,'selection':selection}))
 return target
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',required=True);args=p.parse_args();print(prepare(args.project))
