"""Shared wardrobe surfaces for the Generic input. Classification uses material slots, never outfit names."""
import re

PARTS = {
    'face': '脸 / Face', 'eye': '眼睛 / Eyes', 'brow': '眉睫 / Brows and lashes',
    'hair': '头发 / Hair', 'body1': '第一套身体 / Body 1', 'body2': '第二套身体 / Body 2',
}
CHOICES = [('auto', '自动 / Auto', '')] + [(k, v, '') for k,v in PARTS.items()] + [
    ('caster','投影辅助 / Shadow caster',''), ('exclude','范围外 / Outside scope','')]
BASE = ('base','normal')
SURFACE = BASE + ('mask','aux')
CHANNELS = {'face':BASE, 'brow':BASE, 'eye':BASE+('eye',), 'hair':SURFACE,
            'body1':SURFACE+tuple('cap'+str(i) for i in range(1,6)),
            'body2':SURFACE+tuple('cap'+str(i) for i in range(1,6))}


def part(name, choice='auto'):
    if choice != 'auto':
        if choice not in PARTS and choice not in ('caster','exclude'):
            raise ValueError('Unknown wardrobe part: '+choice)
        return choice
    name = re.sub(r'\.\d{3}$','',name.casefold())
    tokens = set(re.split(r'[^a-z0-9]+',name))
    if 'hairshadow' in tokens or {'hair','shadow'} <= tokens:
        return 'caster'
    if tokens & {'brow','brows','eyebrow','eyebrows','lash','lashes','eyelash','eyelashes'}:
        return 'brow'
    if tokens & {'eye','eyes'}:
        return 'eye'
    if 'face' in tokens:
        return 'face'
    if 'hair' in tokens:
        return 'hair'
    body = re.search(r'(?:^|_)body_?([12])(?:_|$)',name)
    if body and 't' not in tokens:
        return 'body'+body[1]
    return 'exclude'


def mask_slots(obj, indices):
    """Keep original mesh indices for capture matching. Filter out-of-scope polygons ahead of outlines."""
    import bpy
    group = bpy.data.node_groups.new('ZZZ · Shared material slots', 'GeometryNodeTree')
    for direction in ('INPUT','OUTPUT'):
        group.interface.new_socket(name='Geometry',in_out=direction,socket_type='NodeSocketGeometry')
    nodes, link = group.nodes, group.links.new
    source = nodes.new('NodeGroupInput')
    index = nodes.new('GeometryNodeInputMaterialIndex')
    selected = None
    for value in indices:
        equal = nodes.new('FunctionNodeCompare')
        equal.data_type, equal.operation = 'INT', 'EQUAL'
        a,b = [s for s in equal.inputs if s.enabled and s.name in ('A','B')]
        link(index.outputs[0],a)
        b.default_value = value
        flag = equal.outputs['Result']
        if selected is not None:
            union = nodes.new('FunctionNodeBooleanMath')
            union.operation = 'OR'
            link(selected,union.inputs[0])
            link(flag,union.inputs[1])
            flag = union.outputs[0]
        selected = flag
    delete = nodes.new('GeometryNodeDeleteGeometry')
    delete.domain, delete.mode = 'FACE','ALL'
    link(source.outputs['Geometry'],delete.inputs['Geometry'])
    link(selected,delete.inputs['Selection'])
    link(delete.outputs['Geometry'],nodes.new('NodeGroupOutput').inputs['Geometry'])
    modifier = obj.modifiers.new('ZZZ · Shared material slots','NODES')
    modifier.node_group = group
    obj.modifiers.move(len(obj.modifiers)-1,0)
