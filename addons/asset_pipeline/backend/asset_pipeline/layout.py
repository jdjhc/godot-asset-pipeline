"""Deterministic layered DAG layout, separate connected components vertically."""
import math


def arrange(nodes, positions, sizes=None):
    sizes = sizes or {}
    ids = set(positions) & {n for n in nodes if not nodes[n].get('archived', False)}
    parents = {n: {e['node_id'] for e in nodes[n].get('inputs', []) if e['node_id'] in ids} for n in ids}
    children = {n: set() for n in ids}
    dimensions = {}
    for n in ids:
        for p in parents[n]: children[p].add(n)
        size = sizes.get(n,[250,300])
        if not isinstance(size,list) or len(size)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 or v>10000 for v in size):
            raise ValueError('无效卡片尺寸')
        dimensions[n] = size
    key = lambda n: (positions[n][1],positions[n][0],n)
    remaining = set(ids)
    components = []
    while remaining:
        root = min(remaining,key=key); component=set(); stack=[root]
        while stack:
            n=stack.pop()
            if n in component: continue
            component.add(n); stack.extend((parents[n]|children[n])-component)
        remaining-=component; components.append(component)
    output={}; base_y=80
    for component in components:
        levels={}; pending=set(component)
        while pending:
            ready=sorted((n for n in pending if not parents[n]&pending),key=key)
            if not ready: raise ValueError('依赖包含循环，无法自动整理')
            for n in ready: levels[n]=max((levels[p]+1 for p in parents[n]),default=0)
            pending-=set(ready)
        layers=[sorted((n for n in component if levels[n]==i),key=key) for i in range(max(levels.values())+1)]
        # Alternating barycenter sweeps reduce branch crossings without changing edges.
        for _ in range(4):
            for forward in (True,False):
                rank={n:i for layer in layers for i,n in enumerate(layer)}
                for level in (range(1,len(layers)) if forward else range(len(layers)-2,-1,-1)):
                    neighbors=parents if forward else children
                    layers[level].sort(key=lambda n:(sum(rank[p] for p in neighbors[n])/len(neighbors[n]) if neighbors[n] else rank[n],rank[n],n))
                    rank.update({n:i for i,n in enumerate(layers[level])})
        heights=[sum(dimensions[n][1] for n in layer)+70*(len(layer)-1) for layer in layers]
        height=max(heights); x=80
        for layer,h in zip(layers,heights):
            y=base_y+(height-h)/2
            for n in layer:
                output[n]=[x,y]; y+=dimensions[n][1]+70
            x+=max(dimensions[n][0] for n in layer)+160
        base_y+=height+150
    return output
