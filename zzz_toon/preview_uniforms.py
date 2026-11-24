"""Keep animated head vectors on each draw object, outside EEVEE's material graph."""
import bpy


def head_vectors(tree):
    data=tree.animation_data
    if not data or not data.drivers:
        return []
    paths={f.data_path:f for f in data.drivers}
    vectors=[]
    for node in tree.nodes:
        if node.type!='COMBXYZ' or not node.outputs[0].is_linked:
            continue
        channels=[]
        for socket in node.inputs:
            curve=paths.get(socket.path_from_id('default_value'))
            if not curve or curve.mute or curve.driver.type!='AVERAGE' or len(curve.driver.variables)!=1:
                break
            var=curve.driver.variables[0]
            target=var.targets[0]
            if not target.id or var.type not in ('TRANSFORMS','SINGLE_PROP'):
                break
            channels.append((var.type,target.id,target.data_path,target.transform_type,target.transform_space))
        if len(channels)==3:
            vectors.append((node.name,tuple(channels)))
    return vectors


def bind(session,obj,material,vectors,props):
    for node_name,channels in vectors:
        key=props.setdefault(channels,'_zzz_preview_head_'+str(len(props)))
        if (obj,key) not in session.uniforms:
            if key in obj:
                raise ValueError('Preview attribute already exists on '+obj.name+': '+key)
            if obj.animation_data is None:
                session.restore.callback(obj.animation_data_clear)
            obj[key]=[0.,0.,0.]
            session.restore.callback(obj.pop,key,None)
            for i,(kind,target,path,axis,space) in enumerate(channels):
                prop='["'+key+'"]'
                driver=obj.driver_add(prop,i).driver
                session.restore.callback(obj.driver_remove,prop,i)
                driver.type='AVERAGE'
                var=driver.variables.new()
                var.type=kind
                src=var.targets[0]
                src.id=target
                if kind=='SINGLE_PROP':
                    src.data_path=path
                else:
                    src.transform_type=axis
                    src.transform_space=space
            session.uniforms.add((obj,key))
        tree=material.node_tree
        node=tree.nodes.get(node_name)
        if not node:
            continue
        attr=tree.nodes.new('ShaderNodeAttribute')
        attr.attribute_type='OBJECT'
        attr.attribute_name=key
        for link in list(node.outputs[0].links):
            tree.links.new(attr.outputs['Vector'],link.to_socket)
        for socket in node.inputs:
            socket.driver_remove('default_value')
        tree.nodes.remove(node)
