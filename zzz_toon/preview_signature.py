"""Recognize the actual generated late-cloth/Bloom nodes in an edited project."""
import hashlib
import json

import bpy

# Both default outfits' original 5.3 compositor graphs have this digest.
WARDROBE_LATE_PASSES='43a087730e8c67ee192b63aadbc4e49cccbe14f813598023247dde93d28a4701'


def matches(scene,bloom,over):
    if scene.zzz_toon.wardrobe_role not in ('Remielle','Velina') or over.node.label!='Native transparent cloth after opaque LUT/rim':
        return False
    try:
        add=over.node.inputs['Image'].links[0].from_node
        opaque=add.inputs[0].links[0].from_node.inputs[0].links[0].from_socket
        cloth=add.inputs[1].links[0].from_node
        if cloth.type!='R_LAYERS' or cloth.layer!='ZZZ_TransparentPass' or cloth.mute or cloth.scene not in (None,scene):
            return False
        return pipeline(scene,bloom,opaque,cloth)==WARDROBE_LATE_PASSES
    except (AttributeError,IndexError,KeyError,TypeError):
        return False


def pipeline(scene,output,opaque,cloth):
    """Hash only the subgraph replaced by the fullscreen GPU implementation."""
    stop={opaque:'opaque',cloth.outputs['Image']:'cloth',cloth.outputs['Alpha']:'coverage'}
    links={}
    drivers={}
    driven_nodes={}
    cache={}
    inherited=set(bpy.types.Node.bl_rna.properties.keys())

    def simple(value):
        if value is None or isinstance(value,(bool,str,int,float)):
            return value
        return list(value)

    def sockets(tree):
        if tree not in links:
            links[tree]={l.to_socket:l.from_socket for l in tree.links if l.is_valid and not l.is_muted}
            animation=tree.animation_data
            if animation and (animation.action or animation.nla_tracks):
                raise ValueError('Animated compositor')
            drivers[tree]={f.data_path:f for f in animation.drivers} if animation else {}
            driven_nodes[tree]={}
            for path in drivers[tree]:
                prefix,_,field=path.rpartition('"].')
                driven_nodes[tree].setdefault(prefix+'"]',set()).add(field)
        return links[tree]

    def digest(items):
        return hashlib.sha256(json.dumps(items,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()

    def read(socket):
        if socket in stop:
            return stop[socket]
        if socket in cache:
            return cache[socket]
        node=socket.node
        tree=node.id_data
        incoming=sockets(tree)
        if node.mute:
            raise ValueError('Muted node')
        kind=node.bl_idname
        animated=driven_nodes[tree].get(node.path_from_id(),set())
        if animated and (kind!='ShaderNodeValue' or animated!={'outputs[0].default_value'}):
            raise ValueError('Animated node')
        fields=[]
        for prop in node.bl_rna.properties:
            name=prop.identifier
            if name in inherited or name in ('node_tree','is_active_output'):
                continue
            if prop.type in ('POINTER','COLLECTION'):
                raise ValueError('Unsupported external data')
            fields.append((name,simple(getattr(node,name))))
        values=[]
        for item in node.inputs:
            if item in incoming:
                values.append(read(incoming[item]))
            else:
                if item.path_from_id('default_value') in drivers[tree]:
                    raise ValueError('Animated input')
                values.append(simple(getattr(item,'default_value',None)))
        index=list(node.outputs).index(socket)
        if kind=='CompositorNodeGroup':
            end=next(n for n in node.node_tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output)
            values.append(read(sockets(node.node_tree)[end.inputs[index]]))
        elif kind=='ShaderNodeValue':
            curve=drivers[tree].get(socket.path_from_id('default_value'))
            if curve is None:
                values.append(socket.default_value)
            else:
                d=curve.driver
                if curve.mute or d.use_self:
                    raise ValueError('Modified driver')
                if curve.modifiers:
                    # A newly added Blender driver has a default identity generator.
                    if len(curve.modifiers)!=1:
                        raise ValueError('Modified driver')
                    m=curve.modifiers[0]
                    if (m.type!='GENERATOR' or m.mode!='POLYNOMIAL' or m.use_additive or
                            m.use_restricted_range or m.use_influence or tuple(m.coefficients)!=(0.,1.)):
                        raise ValueError('Modified driver')
                variables=[]
                for var in d.variables:
                    target=var.targets[0]
                    if var.type!='SINGLE_PROP' or target.id!=scene:
                        raise ValueError('Different driver target')
                    variables.append((var.name,target.data_path))
                values.append((d.type,d.expression,variables))
        cache[socket]=digest((kind,index,fields,values))
        return cache[socket]

    try:
        return read(output)
    except (AttributeError,IndexError,KeyError,StopIteration,TypeError,ValueError,RecursionError):
        return None
