"""Manual graph operations. No paid submission is performed here."""
import json
import math
import uuid
from pathlib import Path
from .store import _json, _now

FIELDS = ('label','kind','prompt','params','inputs','position','archived')

def configs(store):
    return {n['id']:{k:n.get(k,False if k=='archived' else None) for k in FIELDS} for n in store.snapshot()['nodes']}

def dispatch(service, method, p):
    store=service.store
    if not hasattr(service,'manual_undo'): service.manual_undo=[]; service.manual_redo=[]
    if method in ('manual.undo','manual.redo'):
        origin=service.manual_undo if method.endswith('undo') else service.manual_redo
        dest=service.manual_redo if method.endswith('undo') else service.manual_undo
        if not origin: raise ValueError('没有可撤销/重做的画布操作（历史保留至后台重启）')
        if isinstance(origin[-1],dict) and origin[-1].get('type')=='canvas':
            from .canvases import undo
            return undo(service,method.endswith('redo'))
        before,after=origin[-1]
        current=configs(store)
        touched={k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
        expected=after if method.endswith('undo') else before
        target=before if method.endswith('undo') else after
        for key in touched:
            if key in expected and current.get(key)!=expected[key]: raise ValueError('节点已被其他操作修改，不能覆盖撤销')
        active={j['node_id'] for j in store.snapshot()['jobs'] if j['status'] in ('queued','submitting','running','downloading','paused','submission_uncertain')}
        if touched&active: raise ValueError('相关节点有未完成任务，请先处理任务')
        with store._connection(write=True) as db:
            for key in touched:
                node=store._node(db,key)
                node.update(target.get(key,{'archived':True}))
                node['stale']=True
                store._save_node(db,node,True)
                store._invalidate(db,key)
            store._bump(db)
        # Missing newly-created nodes are represented as archived for subsequent redo checks.
        origin.pop()
        if method.endswith('undo'): dest.append((configs(store),after))
        else: dest.append((before,configs(store)))
        return {'ok':True}
    if method=='manual.templates':
        folder=store.project/'.asset_pipeline/templates'; folder.mkdir(exist_ok=True)
        return [{'id':x.stem,'label':json.loads(x.read_text())['label']} for x in sorted(folder.glob('*.json'))]
    if method=='manual.plan':
        root=p['node_id']; nodes={n['id']:n for n in store.snapshot()['nodes']}
        ids=[root]+store.plan(root)['affected']
        ids=[i for i in ids if nodes[i]['kind']!='reference' and not nodes[i].get('archived')]
        if p.get('mode')=='stale': ids=[i for i in ids if nodes[i]['stale'] or not nodes[i]['current_version']]
        ordered=[]
        while ids:
            ready=[i for i in ids if all(e['node_id'] not in ids for e in nodes[i]['inputs'])]
            if not ready: raise ValueError('依赖环')
            ordered.extend(ready); ids=[i for i in ids if i not in ready]
        return {'node_ids':ordered,'labels':[nodes[i]['label'] for i in ordered], 'notice':'将创建独立修复分支，保留原节点及版本；创建后仍需点击整体开始。'}
    before=configs(store)
    if method=='manual.create':
        result=store.create_node(p['node'])
    elif method=='manual.update':
        result=store.update_node(p['node_id'],p['patch'])
    elif method=='manual.archive':
        ids=p['node_ids']; snapshot=store.snapshot()
        if any(j['node_id'] in ids and j['status'] in ('running','queued','submitting','downloading','paused','submission_uncertain') for j in snapshot['jobs']): raise ValueError('节点有未完成任务，不能归档')
        with store._connection(write=True) as db:
            for id in ids:
                n=store._node(db,id); n['archived']=bool(p.get('archived',True)); store._save_node(db,n,False)
            store._bump(db)
        result={'ok':True}
    elif method=='manual.import':
        paths=p['paths']
        for path in paths: store._source_path(path)
        position=p.get('position',[100,100])
        if not isinstance(position,list) or len(position)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in position):
            raise ValueError('无效导入位置')
        result=[]
        for i,path in enumerate(paths):
            model=Path(path).suffix.lower() in ('.glb','.gltf','.fbx','.obj','.tscn')
            n=store.create_node({'label':Path(path).stem,'kind':'model' if model else 'reference','position':[position[0]+(i%3)*280,position[1]+(i//3)*320]})
            store.import_version(n['id'],[{'path':path,'role':'model' if model else ('video' if Path(path).suffix.lower() in ('.mp4','.mov','.mkv','.webm','.avi','.ogv') else 'image')}],metadata={'source':'manual_import'})
            result.append(n)
    elif method=='manual.template_save':
        ids=p['node_ids']; allnodes={n['id']:n for n in store.snapshot()['nodes']}
        if not ids: raise ValueError('请选择节点')
        nodes=[{k:v for k,v in allnodes[i].items() if k in FIELDS or k=='id'} for i in ids]
        folder=store.project/'.asset_pipeline/templates'; folder.mkdir(exist_ok=True)
        id='template_'+uuid.uuid4().hex
        (folder/(id+'.json')).write_text(_json({'label':p.get('label','自定义流程'),'nodes':nodes}))
        return {'id':id}
    elif method in ('manual.copy','manual.template_load','manual.rebuild'):
        allnodes={n['id']:n for n in store.snapshot()['nodes']}
        if method=='manual.template_load':
            id=p['template_id']
            if not id.startswith('template_') or not id[9:].isalnum(): raise ValueError('无效模板')
            source=json.loads((store.project/'.asset_pipeline/templates'/(id+'.json')).read_text())['nodes']
        else:
            ids=p['node_ids']
            source=[allnodes[i] for i in ids]
        ids={n['id'] for n in source}; mapped={}; pending=list(source)
        while pending:
            ready=[n for n in pending if all(e['node_id'] not in ids or e['node_id'] in mapped for e in n.get('inputs',[]))]
            if not ready: raise ValueError('依赖环')
            for n in ready:
                edges=[{**e,'node_id':mapped.get(e['node_id'],e['node_id'])} for e in n.get('inputs',[]) if e['node_id'] in ids or e['node_id'] in allnodes]
                config={k:n[k] for k in ('kind','prompt','params','position') if k in n}
                config.update(label=n['label']+' · 副本',inputs=edges,position=[n.get('position',[0,0])[0]+100,n.get('position',[0,0])[1]+180])
                created=store.create_node(config); mapped[n['id']]=created['id']; pending.remove(n)
                if method!='manual.rebuild' and n.get('current_version'):
                    v=next(v for v in n['versions'] if v['id']==n['current_version'])
                    store.import_version(created['id'],v['files'],metadata={'source':'copied_version','original_node':n['id'],'original_version':v['id']})
        result={'mapping':mapped}
        if method=='manual.rebuild':
            ordered=list(mapped.values()); external={}; recipes={}
            for id in ordered:
                node=store.get_node(id); recipes[id]=store._recipe(node)
                for e in node['inputs']:
                    if e['node_id'] not in ordered: external[e['node_id']]=store.get_node(e['node_id'])['current_version']
            batch={'id':'batch_'+uuid.uuid4().hex,'label':'手动修复分支','spec':{},'concurrency':1,'assets':[],'node_ids':ordered,'status':'planned','attempts':{},'completed_versions':{},'recipes':recipes,'external_versions':external,'errors':{},'error':None,'created_at':_now(),'updated_at':_now()}
            if not ordered: raise ValueError('没有需要生成的节点')
            with store._connection(write=True) as db:
                db.execute('INSERT INTO batches(id,data) VALUES (?,?)',(batch['id'],_json(batch))); store._bump(db)
            result['batch_id']=batch['id']
    else: raise ValueError('未知手动操作 '+method)
    after=configs(store)
    if before!=after: service.manual_undo.append((before,after)); service.manual_redo.clear()
    return result
