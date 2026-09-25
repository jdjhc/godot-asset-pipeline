"""Named canvases are views over the shared asset library, never file owners."""
import json
import math
import uuid
from .store import _json


def state(store):
    with store._connection() as db:
        return {'canvases': [dict(r) for r in db.execute('SELECT * FROM canvases ORDER BY rowid')],
                'placements': [dict(r) for r in db.execute('SELECT * FROM canvas_nodes ORDER BY canvas_id,node_id')]}


def require(store, canvas_id):
    with store._connection() as db:
        if not db.execute('SELECT 1 FROM canvases WHERE id=?', (canvas_id,)).fetchone():
            raise ValueError('画布不存在，请刷新后重试')


def assign_new(store, canvas_id, ids):
    if not ids: return
    with store._connection(write=True) as db:
        for node_id in ids:
            node = store._node(db, node_id)
            db.execute('DELETE FROM canvas_nodes WHERE node_id=?', (node_id,))
            db.execute('INSERT INTO canvas_nodes VALUES (?,?,?)', (canvas_id,node_id,_json(node['position'])))
        store._bump(db)


def undo(service, redo=False):
    source = service.manual_redo if redo else service.manual_undo
    dest = service.manual_undo if redo else service.manual_redo
    entry = source[-1]
    expected, target = (entry['before'],entry['after']) if redo else (entry['after'],entry['before'])
    if state(service.store) != expected:
        raise ValueError('画布已被后续操作修改，不能覆盖撤销')
    with service.store._connection(write=True) as db:
        db.execute('DELETE FROM canvas_nodes')
        db.execute('DELETE FROM canvases')
        db.executemany('INSERT INTO canvases VALUES (?,?)', [(x['id'],x['name']) for x in target['canvases']])
        db.executemany('INSERT INTO canvas_nodes VALUES (?,?,?)', [(x['canvas_id'],x['node_id'],x['position']) for x in target['placements']])
        service.store._bump(db)
    source.pop(); dest.append(entry)
    return {'ok': True}


def dispatch(service, method, p):
    store = service.store
    before = state(store)
    if not hasattr(service,'manual_undo'): service.manual_undo=[]; service.manual_redo=[]
    canvas_id = p.get('canvas_id','')
    if method != 'canvas.create': require(store, canvas_id)
    with store._connection(write=True) as db:
        if method in ('canvas.create','canvas.rename'):
            name = p.get('name','').strip()
            if not name or len(name)>80: raise ValueError('画布名称需为 1～80 个字符')
            if db.execute('SELECT 1 FROM canvases WHERE name=? AND id!=?',(name,canvas_id)).fetchone():
                raise ValueError('画布名称已存在')
            if method == 'canvas.create':
                canvas_id = 'canvas_'+uuid.uuid4().hex
                db.execute('INSERT INTO canvases VALUES (?,?)',(canvas_id,name))
            else: db.execute('UPDATE canvases SET name=? WHERE id=?',(name,canvas_id))
        elif method == 'canvas.layout':
            from .layout import arrange
            positions = {row['node_id']:json.loads(row['position']) for row in db.execute('SELECT node_id,position FROM canvas_nodes WHERE canvas_id=?',(canvas_id,))}
            arranged = arrange(store._all(db), positions, p.get('sizes',{}))
            for node_id,pos in arranged.items():
                db.execute('UPDATE canvas_nodes SET position=? WHERE canvas_id=? AND node_id=?',(_json(pos),canvas_id,node_id))
        elif method == 'canvas.delete':
            if db.execute('SELECT COUNT(*) FROM canvases').fetchone()[0] <= 1:
                raise ValueError('至少保留一张画布，请先新建另一张')
            db.execute('DELETE FROM canvas_nodes WHERE canvas_id=?',(canvas_id,))
            db.execute('DELETE FROM canvases WHERE id=?',(canvas_id,))
        elif method in ('canvas.add','canvas.move','canvas.remove'):
            ids = p.get('node_ids',[])
            for i,node_id in enumerate(ids):
                node = store._node(db,node_id)
                if method == 'canvas.remove':
                    db.execute('DELETE FROM canvas_nodes WHERE canvas_id=? AND node_id=?',(canvas_id,node_id)); continue
                pos = p.get('position',node['position'])
                if not isinstance(pos,list) or len(pos)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in pos):
                    raise ValueError('无效卡片位置')
                if method == 'canvas.move' and not db.execute('SELECT 1 FROM canvas_nodes WHERE canvas_id=? AND node_id=?',(canvas_id,node_id)).fetchone():
                    raise ValueError('卡片不在当前画布')
                position = _json([pos[0]+i*280,pos[1]])
                if method == 'canvas.add':
                    db.execute('INSERT OR IGNORE INTO canvas_nodes VALUES (?,?,?)',(canvas_id,node_id,position))
                else: db.execute('UPDATE canvas_nodes SET position=? WHERE canvas_id=? AND node_id=?',(position,canvas_id,node_id))
        else: raise ValueError('未知画布操作')
        store._bump(db)
    after=state(store)
    if before != after:
        service.manual_undo.append({'type':'canvas','before':before,'after':after})
        service.manual_redo.clear()
    return {'canvas_id':canvas_id}
