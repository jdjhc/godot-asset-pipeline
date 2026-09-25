"""User-defined subject bounds, preserved original and marked reference."""
import math
from .store import _json

PROMPT = ('Image 1 is the unmarked original reference. Image 2 is the same image with a single colored rectangular selection. '
'The rectangle is a selection guide, not part of the subject. Extract only the main physical object indicated by that rectangle. '
'Use the original image to preserve its appearance, proportions, materials and details. Remove other objects and the surrounding scene. '
'Keep the original viewing direction. Show one complete isolated object centered on a plain light-gray background. '
'Conservatively complete parts of the selected object hidden by occlusion or cut by the selection. '
'Do not include the selection outline, annotations, labels or additional objects. Do not redesign the subject.')

def create(store,p):
    node=store.get_node(p['node_id'])
    version=next((v for v in node['versions'] if v['id']==p['version_id']),None)
    if not version or not any(f['path']==p['source_path'] for f in version['files']):
        raise ValueError('参考图片与所选版本不匹配，请重新选择')
    box=p.get('box'); size=p.get('image_size')
    if not isinstance(box,list) or len(box)!=4 or not isinstance(size,list) or len(size)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in box+size):
        raise ValueError('无效框选坐标')
    x,y,w,h=box
    if min(size)<=0 or x<0 or y<0 or w<8 or h<8 or x+w>size[0]+.01 or y+h>size[1]+.01:
        raise ValueError('选择框必须位于图片内且至少 8×8 像素')
    original=store._source_path(p['source_path']); annotated=store._source_path(p['annotated_path'])
    meta={'source':'manual_subject_selection','original_node_id':node['id'],'original_version_id':version['id'],'original_path':p['source_path'],'box_pixels':box,'image_size':size,'outline_color':p.get('color','ff00ff')}
    created_dirs=[]
    try:
        with store._connection(write=True) as db:
            # Create the entire chain atomically using the store's configuration contracts.
            import uuid
            from .store import _now
            def make(kind,label,inputs,prompt='',position=None):
                id='node_'+uuid.uuid4().hex
                n=store._clean_config({'kind':kind,'label':label,'prompt':prompt,'params':{},'position':position or [0,0]})
                n.update(id=id,inputs=store._validate_inputs(db,id,inputs),current_version=None,stale=False,created_at=_now())
                db.execute('INSERT INTO nodes(id,data) VALUES (?,?)',(id,_json(n)))
                return id
            base=node.get('position',[0,0]); count=db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            raw=make('reference','框选原图 · 固定版本',[],position=[base[0]+300,base[1]+100])
            store.import_version(raw,[{'path':str(original),'role':'image'}],metadata=meta,_db=db,_created_dirs=created_dirs)
            marked=make('reference','主体框选标注',[{'node_id':raw,'role':'original'}],position=[base[0]+600,base[1]+100])
            store.import_version(marked,[{'path':str(annotated),'role':'image'}],metadata=meta,_db=db,_created_dirs=created_dirs)
            subject=make('subject','框选主体 '+str(count+1),[{'node_id':raw,'role':'reference'},{'node_id':marked,'role':'selection_reference'}],PROMPT,[base[0]+900,base[1]+100])
            store._bump(db)
        return {'subject_id':subject,'annotation_id':marked,'original_id':raw}
    except BaseException:
        import shutil
        for directory in created_dirs: shutil.rmtree(directory,ignore_errors=True)
        raise
