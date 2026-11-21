"""Opt-in animated native outline hull and PS12 material.

Named attributes are model inputs. Captured scene globals stay in explicit
profiles. Armature direction probes carry N/T through the same linear skinning
as positions; they are hidden meshes, not frozen capture-pose geometry.
"""
import hashlib
import math,json
from pathlib import Path
import numpy as np
from .native_lighting import Ops

def clear(ob):
    import bpy
    for md in list(ob.modifiers):
        if md.name.startswith('ZZZ_NativeOutlineNormals') or (md.type=='NODES' and md.node_group and md.node_group.get('zzz_native_outline')):
            g=md.node_group;ob.modifiers.remove(md)
            if g and g.users==0:bpy.data.node_groups.remove(g)
    for helper in list(ob.children):
        if helper.get('zzz_outline_helper'):
            me=helper.data;bpy.data.objects.remove(helper,do_unlink=True)
            if me.users==0:bpy.data.meshes.remove(me)
    if 'zzz_native_outline' in ob:del ob['zzz_native_outline']
    ob.pop('zzz_surface_tbn_v34',None)
    ob.pop('zzz_native_surface_profile',None)

def validate(p):
    if p is None:return None
    if not isinstance(p,dict) or p.get('schema')!='zzz-toon-outline/1':raise ValueError('Unknown outline schema')
    if not p.get('source') or not p.get('scope'):raise ValueError('Outline source and scope are required')
    for key,n in [('post_tint',3),('vertex_ambient',3),('clip_offset',2)]:
        if len(p.get(key,[]))!=n or not all(math.isfinite(x) for x in p[key]):raise ValueError('Invalid outline '+key)
    if not isinstance(p.get('visibility'),(int,float)) or not 0<=p['visibility']<=1:raise ValueError('Explicit outline visibility must be in [0,1]')
    return p

def put(me,name,data,kind='FLOAT_VECTOR',domain='POINT'):
    if name in me.attributes:me.attributes.remove(me.attributes[name])
    a=me.attributes.new(name,kind,domain);a.data.foreach_set('vector' if kind=='FLOAT_VECTOR' else 'value',np.asarray(data).ravel())

def mesh_outline_inputs(path, expected_digest, mesh, loops):
    """Read original Unity rest streams for an index-preserving X-reflected FBX."""
    data = path.read_bytes()
    if expected_digest and hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("Original outline Mesh digest differs: " + str(path))
    source = json.loads(data.decode("utf-8-sig"))
    count = len(mesh.vertices)
    if any(poly.loop_total != 3 for poly in mesh.polygons):
        raise ValueError("Original outline Mesh requires triangulated faces")
    tri = np.asarray(source["m_Indices"])
    if tri.size != len(loops) or tri.dtype.kind not in "iu" or not len(loops):
        raise ValueError("Original outline index stream differs")
    # Reversed winding with any cyclic start corner is valid. Preserve face order.
    tri = tri.reshape(-1, 3)[:, ::-1]
    corners = loops.reshape(-1, 3)
    matches = np.zeros(len(tri), dtype=bool)
    for shift in range(3):
        matches |= np.all(np.roll(tri, shift, axis=1) == corners, axis=1)
    if not matches.all():
        raise ValueError("Original outline triangle order or winding differs")

    def field(key, width):
        values = np.asarray(source[key], dtype=float)
        if values.size != count * width or not np.isfinite(values).all():
            raise ValueError("Invalid original outline stream: " + key)
        return values.reshape(count, width)

    reflect = np.array([-1., 1., 1.])
    tangent = field("m_Tangents", 4)
    if not np.all(np.isclose(abs(tangent[:, 3]), 1., rtol=0., atol=1e-5)):
        raise ValueError("Invalid original outline tangent handedness")
    return dict(position=field("m_Vertices", 3)*reflect,
                normal=field("m_Normals", 3)*reflect, tangent=tangent[:, :3]*reflect,
                packed=np.column_stack((field("m_UV1", 2), -tangent[:, 3])),
                raw_color=field("m_Colors", 4), loop_vertex=loops, uv0=field("m_UV0", 2)[loops])


def load_inputs(ob,p):
    me=ob.data;item=p.get('mesh_inputs',{}).get(ob.name.split('.')[0])
    if item:
        path=Path(item['path'])
        positions=np.array([v.co[:] for v in me.vertices])
        loops=np.array([l.vertex_index for l in me.loops],dtype=np.int32)
        if path.suffix.lower()=='.json':
            a=mesh_outline_inputs(path,item.get('source_sha256'),me,loops)
        else:
            with np.load(path) as archive:
                a={k:archive[k] for k in ('position','loop_vertex','uv0','normal','tangent','packed','raw_color') if k in archive}
        if a['position'].shape!=positions.shape or not np.allclose(a['position'],positions,rtol=0,atol=2e-5):raise ValueError('Outline mesh correspondence changed: '+ob.name)
        if 'loop_vertex' not in a or 'uv0' not in a:raise ValueError('Outline NPZ requires topology and UV correspondence evidence')
        uv=me.uv_layers.get('UV0')
        if not np.array_equal(loops,a['loop_vertex']) or uv is None:raise ValueError('Outline topology changed: '+ob.name)
        values=np.array([v.uv[:] for v in uv.data])
        if values.shape!=a['uv0'].shape or not np.allclose(values,a['uv0'],rtol=0,atol=2e-5):raise ValueError('Outline UV correspondence changed: '+ob.name)
        for key in ('normal','tangent','packed'):
            if a[key].shape!=positions.shape or not np.isfinite(a[key]).all():raise ValueError('Invalid outline '+key)
        if 'raw_color' in a and (a['raw_color'].shape!=(len(positions),4) or not np.isfinite(a['raw_color']).all()):raise ValueError('Invalid outline vertex colour')
        for key,src in [('zzz_ol_rest_n','normal'),('zzz_ol_rest_t','tangent'),('zzz_ol_packed','packed')]:put(me,key,a[src])
        if 'raw_color' in a:
            raw=a['raw_color'];put(me,'zzz_ol_rg',np.c_[raw[:,:2],np.zeros(len(raw))])
            put(me,'zzz_outline_hair',((np.rint(raw[:,2]*255).astype(int)>>5)&1).astype(float),kind='FLOAT')
        ob['zzz_outline_input_source']=json.dumps(item,sort_keys=True)
    if not all(k in me.attributes for k in ('zzz_ol_rest_n','zzz_ol_rest_t','zzz_ol_packed')):return False
    if 'zzz_ol_rg' in me.attributes and 'zzz_outline_hair' in me.attributes:return True
    ca=me.color_attributes.get('Attribute')
    if ca is None:raise ValueError('Native outline requires original vertex colour R/G: '+ob.name)
    col=np.empty(len(ca.data)*4);ca.data.foreach_get('color_srgb',col);col=col.reshape(-1,4)
    # Preserve raw byte-like values. A color attribute read directly in GN would
    # linearize R/G and incorrectly reduce line width and change depth bias.
    put(me,'zzz_ol_rg',np.c_[col[:,:2],np.zeros(len(col))],domain=ca.domain)
    put(me,'zzz_outline_hair',((np.rint(col[:,2]*255).astype(int)>>5)&1).astype(float),kind='FLOAT',domain=ca.domain)
    return True

def prepare_skin(ob):
    import bpy
    from mathutils import Matrix
    me=ob.data;count=len(me.vertices)
    def read(key):
        v=np.empty(count*3);me.attributes[key].data.foreach_get('vector',v);return v.reshape(-1,3)
    n,t,packed=map(read,('zzz_ol_rest_n','zzz_ol_rest_t','zzz_ol_packed'))
    arms=[m for m in ob.modifiers if m.type=='ARMATURE' and m.show_viewport]
    if not arms:
        normal=n*np.sqrt(np.maximum(0,1-np.sum(packed[:,:2]**2,1)))[:,None]+t*packed[:,:1]+np.cross(n,t)*packed[:,1:2]*packed[:,2:3]
        put(me,'zzz_native_oln',normal);return None
    if len(arms)!=1 or arms[0].use_deform_preserve_volume or arms[0].use_bone_envelopes:raise ValueError('Native outline supports one linear vertex-group Armature: '+ob.name)
    if me.shape_keys and len(me.shape_keys.key_blocks)>1:raise ValueError('Morph-normal outline input is not implemented: '+ob.name)
    arm=arms[0];before=list(ob.modifiers)[:list(ob.modifiers).index(arm)]
    if before:raise ValueError('Native outline probes require Armature first: '+ob.name)
    name='ZZZ_OutlineProbe_'+ob.name
    old=bpy.data.objects.get(name)
    if old:
        if not old.get('zzz_outline_helper'):raise ValueError('Reserved outline helper name already exists')
        oldmesh=old.data;bpy.data.objects.remove(old,do_unlink=True)
        if oldmesh.users==0:bpy.data.meshes.remove(oldmesh)
    points=np.array([v.co[:] for v in me.vertices]);hm=bpy.data.meshes.new(name)
    hm.from_pydata(np.concatenate((points,points+n,points+t)).tolist(),[],[])
    helper=bpy.data.objects.new(name,hm);ob.users_collection[0].objects.link(helper)
    helper.parent=ob;helper.matrix_parent_inverse=Matrix.Identity(4);helper.matrix_basis=Matrix.Identity(4)
    helper.hide_render=True;helper.hide_set(True);helper['zzz_outline_helper']=True
    for g in ob.vertex_groups:helper.vertex_groups.new(name=g.name)
    buckets={}
    for v in me.vertices:
        for w in v.groups:buckets.setdefault((w.group,w.weight),[]).extend((v.index,v.index+count,v.index+2*count))
    for (group,weight),indices in buckets.items():helper.vertex_groups[group].add(indices,weight,'REPLACE')
    md=helper.modifiers.new('NativeDirectionSkin','ARMATURE');md.object=arm.object;md.use_vertex_groups=True;md.use_bone_envelopes=False;md.use_deform_preserve_volume=False
    md.vertex_group=arm.vertex_group;md.invert_vertex_group=arm.invert_vertex_group
    g=bpy.data.node_groups.new('ZZZ_NativeOutlineNormals_'+ob.name,'GeometryNodeTree');g.is_modifier=True
    g.interface.new_socket('Geometry',in_out='INPUT',socket_type='NodeSocketGeometry');g.interface.new_socket('Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    from .core import NB
    class Target:node_tree=g
    b=NB(Target());o=Ops(b);gi=b.node('NodeGroupInput');go=b.node('NodeGroupOutput')
    oi=b.node('GeometryNodeObjectInfo');oi.transform_space='RELATIVE';oi.inputs['Object'].default_value=helper
    idx=b.node('GeometryNodeInputIndex');pos=b.node('GeometryNodeInputPosition')
    def sample(offset):
        s=b.node('GeometryNodeSampleIndex',data_type='FLOAT_VECTOR',domain='POINT');b.link(oi.outputs['Geometry'],s.inputs['Geometry']);b.link(pos.outputs[0],s.inputs['Value']);b.link(o.add(idx.outputs[0],offset),s.inputs['Index']);return s.outputs[0]
    base=sample(0);nn=o.v('NORMALIZE',o.v('SUBTRACT',sample(count),base));tt=o.v('NORMALIZE',o.v('SUBTRACT',sample(2*count),base))
    attr=b.node('GeometryNodeInputNamedAttribute',data_type='FLOAT_VECTOR');attr.inputs['Name'].default_value='zzz_ol_packed';sp=b.node('ShaderNodeSeparateXYZ');b.link(attr.outputs[0],sp.inputs[0])
    z=o.m('SQRT',o.m('MAXIMUM',0.,o.sub(1.,o.add(o.mul(sp.outputs[0],sp.outputs[0]),o.mul(sp.outputs[1],sp.outputs[1])))))
    normal=o.v('ADD',o.v('ADD',o.scale(tt,sp.outputs[0]),o.scale(o.v('CROSS_PRODUCT',nn,tt),o.mul(sp.outputs[1],sp.outputs[2]))),o.scale(nn,z))
    st=b.node('GeometryNodeStoreNamedAttribute',data_type='FLOAT_VECTOR',domain='POINT');st.inputs['Name'].default_value='zzz_native_oln';b.link(gi.outputs[0],st.inputs['Geometry']);b.link(normal,st.inputs['Value']);b.link(st.outputs[0],go.inputs[0])
    md=ob.modifiers.new('ZZZ_NativeOutlineNormals','NODES');md.node_group=g
    index=list(ob.modifiers).index(arm)+1
    with bpy.context.temp_override(object=ob,active_object=ob):bpy.ops.object.modifier_move_to_index(modifier=md.name,index=index)
    return helper

def geometry(scene,width,adjust,zoffset,profile,material,face_direction=None):
    import bpy
    from .core import NB
    g=bpy.data.node_groups.new('ZZZ_NativeOutline','GeometryNodeTree');g.is_modifier=True
    g['zzz_native_outline']=True
    g.interface.new_socket('Geometry',in_out='INPUT',socket_type='NodeSocketGeometry');g.interface.new_socket('Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    class Target:node_tree=g
    b=NB(Target());o=Ops(b);gi=b.node('NodeGroupInput');go=b.node('NodeGroupOutput')
    def attr(key):
        n=b.node('GeometryNodeInputNamedAttribute',data_type='FLOAT_VECTOR');n.inputs['Name'].default_value=key;return n.outputs[0]
    def sep(v):
        n=b.node('ShaderNodeSeparateXYZ');b.link(v,n.inputs[0]);return n.outputs
    def combine(x,y,z):
        n=b.node('ShaderNodeCombineXYZ')
        for i,v in enumerate((x,y,z)):
            if isinstance(v,(int,float)):n.inputs[i].default_value=v
            else:b.link(v,n.inputs[i])
        return n.outputs[0]
    def transform(kind,v,m):
        n=b.node('FunctionNodeTransform'+kind);b.link(v,n.inputs[0]);b.link(m,n.inputs[1]);return n.outputs[0]
    def matrix(kind,m):
        n=b.node('FunctionNode'+kind+'Matrix');b.link(m,n.inputs[0]);return n.outputs[0]
    own=b.node('GeometryNodeSelfObject');info=b.node('GeometryNodeObjectInfo');info.transform_space='ORIGINAL';b.link(own.outputs[0],info.inputs['Object']);world=info.outputs['Transform'];inverse=matrix('Invert',world)
    active=b.node('GeometryNodeInputActiveCamera');cam=b.node('GeometryNodeObjectInfo');cam.transform_space='ORIGINAL';b.link(active.outputs[0],cam.inputs['Object'])
    # Blender ignores camera scale when forming its projection/view transform.
    rigid=b.node('FunctionNodeCombineTransform');b.link(cam.outputs['Location'],rigid.inputs['Translation']);b.link(cam.outputs['Rotation'],rigid.inputs['Rotation']);rigid.inputs['Scale'].default_value=(1,1,1)
    cm=rigid.outputs[0];view=matrix('Invert',cm)
    pos=b.node('GeometryNodeInputPosition');wp=transform('Point',pos.outputs[0],world);vp=transform('Point',wp,view)
    direction=face_direction(b,o,gi.outputs[0],pos.outputs[0]) if face_direction else attr('zzz_native_oln')
    wn=o.v('NORMALIZE',transform('Direction',direction,matrix('Transpose',inverse)));vn=transform('Direction',wn,view)
    xyz=sep(vp);normal=sep(vn);rg=sep(attr('zzz_ol_rg'))
    def driven(label,expression,paths):
        n=b.node('ShaderNodeValue');n.label=label;d=n.outputs[0].driver_add('default_value').driver;d.expression=expression
        for name,path in paths.items():
            v=d.variables.new();v.name=name;v.type='SINGLE_PROP';v.targets[0].id_type='SCENE';v.targets[0].id=scene;v.targets[0].data_path=path
        return n.outputs[0]
    paths={k:'camera.data.'+v for k,v in {'lens':'lens','sw':'sensor_width','sh':'sensor_height','fit':'sensor_fit','ortho':'ortho_scale','kind':'type'}.items()}
    paths.update({k:'render.'+v for k,v in {'rx':'resolution_x','ry':'resolution_y','ax':'pixel_aspect_x','ay':'pixel_aspect_y'}.items()})
    aspect='(rx*ax/(ry*ay))';vertical='(fit==2 or (fit==0 and rx*ax<ry*ay))'
    focal=driven('Native projection m11',f'2*lens/(sh if fit==2 else sw)*(1 if {vertical} else {aspect})',paths)
    ratio=driven('Pixel aspect ratio',aspect,paths);height=driven('Orthographic height',f'ortho*(1 if {vertical} else 1/{aspect})',paths);orthographic=driven('Orthographic camera','kind==1',paths)
    q=o.div(o.mul(-1.,xyz[2]),focal);a=o.sat(o.mul(10.,q));bb=o.sat(o.sub(q,.1));c=o.sat(o.sub(o.mul(.5,q),1.05))
    w=o.mul(.00105,a);w=o.mix(bb,w,.005);w=o.mix(c,w,.0035);w=o.mix(orthographic,w,o.mul(.005,height));w=o.mul(o.mul(width*adjust,rg[0]),w)
    invlen=o.div(1.,o.m('SQRT',o.add(o.add(o.mul(normal[0],normal[0]),o.mul(normal[1],normal[1])),1e-10)))
    x=o.add(xyz[0],o.mul(o.mul(normal[0],invlen),w));y=o.add(xyz[1],o.mul(o.mul(normal[1],invlen),w))
    expanded=combine(x,y,xyz[2]);off=profile['clip_offset']
    # Unity's clip offset is already represented by the Blender projection.
    # It affects the native view-ray length; do not translate the model twice.
    ray=combine(o.sub(x,o.mul(o.div(off[0],o.div(focal,ratio)),xyz[2])),o.add(y,o.mul(o.div(off[1],focal),xyz[2])),xyz[2])
    factor=o.add(1.,o.div(o.mul(zoffset,o.sub(rg[1],.501999974)),o.m('MAXIMUM',1e-30,o.v('LENGTH',ray))))
    expanded=o.scale(expanded,factor);newpos=transform('Point',transform('Point',expanded,cm),inverse)
    geo=gi.outputs[0]
    for name,val in [('native_outline_normal',wn),('native_outline_position',wp)]:
        st=b.node('GeometryNodeStoreNamedAttribute',data_type='FLOAT_VECTOR',domain='POINT');st.inputs['Name'].default_value=name;b.link(geo,st.inputs['Geometry']);b.link(val,st.inputs['Value']);geo=st.outputs[0]
    setpos=b.node('GeometryNodeSetPosition');b.link(geo,setpos.inputs['Geometry']);b.link(newpos,setpos.inputs['Position'])
    expanded_mesh=setpos.outputs[0]
    if face_direction:
        selection=b.node('GeometryNodeInputNamedAttribute',data_type='BOOLEAN')
        selection.inputs['Name'].default_value='zzz_face_outline_surface'
        faces=b.node('GeometryNodeSeparateGeometry',domain='FACE')
        b.link(expanded_mesh,faces.inputs['Geometry']);b.link(selection.outputs[0],faces.inputs['Selection'])
        expanded_mesh=faces.outputs['Selection']
    flip=b.node('GeometryNodeFlipFaces');b.link(expanded_mesh,flip.inputs[0]);sm=b.node('GeometryNodeSetMaterial');sm.inputs['Material'].default_value=material;b.link(flip.outputs[0],sm.inputs['Geometry'])
    join=b.node('GeometryNodeJoinGeometry');b.link(gi.outputs[0],join.inputs[0]);b.link(sm.outputs[0],join.inputs[0]);b.link(join.outputs[0],go.inputs[0])
    return g

def material(name,te,prm,p,settings):
    import bpy
    from . import core,native_outline,light_profiles
    mat=bpy.data.materials.get(name) or bpy.data.materials.new(name);mat.use_nodes=True;mat.node_tree.nodes.clear();b=core.NB(mat);o=Ops(b)
    t=b.tex(core.img(te['_MainTex']));tm=b.tex(core.img(te['_OtherDataTex'],True));s=b.node('ShaderNodeSeparateColor');b.link(tm.outputs['Color'],s.inputs[0])
    colors=prm.get('NativeOutlineColor',prm['OutlineColor']);mc=prm.get('NativeMaterialColor',prm['Color'])
    # OutlineColor is an HDR property: all 50 captured slot vectors match the
    # raw asset, including values above 1. Do not apply shadow-tint conversion.
    def select(values):
        value=b.rgb(values[0])
        for threshold,c in zip((.8,.6,.4,.2),values[1:]):value=b.mixc(o.m('LESS_THAN',s.outputs[0],threshold),value,b.rgb(c))
        return value
    oc=select(colors);alpha=select([(c[3],)*3+(1.,) for c in colors]);sa=b.node('ShaderNodeSeparateXYZ');b.link(alpha,sa.inputs[0])
    n=b.node('ShaderNodeAttribute');n.attribute_name='native_outline_normal';normal=o.v('NORMALIZE',n.outputs['Vector'])
    view=b.node('ShaderNodeVectorTransform',vector_type='VECTOR',convert_from='WORLD',convert_to='CAMERA')
    b.link(normal,view.inputs['Vector'])
    world=b.node('ShaderNodeVectorTransform',vector_type='VECTOR',convert_from='CAMERA',convert_to='WORLD')
    b.link(o.v('MULTIPLY',view.outputs['Vector'],(1.,1.,-1.)),world.inputs['Vector'])
    light=light_profiles.direction(b,settings,attribute='zzz_outline_hair')
    if light is None:raise ValueError('Native outline requires an explicit native light profile')
    env=settings.get('native_environment')
    if env is None:raise ValueError('Native outline requires explicit environment colour inputs')
    # Native outline does not use the body's world-gradient branch.
    pos=b.node('ShaderNodeAttribute');pos.attribute_name='native_outline_position';delta=o.v('SUBTRACT',tuple(env['entity_position']),pos.outputs['Vector'])
    att=o.m('MAXIMUM',0.,o.sub(1.,o.div(o.v('DOT_PRODUCT',delta,delta),max(env['entity_range']**2,1e-30))))
    lc=o.cmix(att,tuple(env['main_color']),tuple(env['entity_color']))
    col=native_outline.color(b,t.outputs['Color'],o.mul(prm['floats'].get('_Metallic',0.),s.outputs[1]),oc,sa.outputs[0],tuple(mc[:3]),o.v('DOT_PRODUCT',o.v('NORMALIZE',world.outputs['Vector']),light),p['visibility'],tuple(p['post_tint']),lc,tuple(env['ambient_color']),tuple(p['vertex_ambient']))
    em=b.node('ShaderNodeEmission');b.link(col,em.inputs['Color']);out=b.node('ShaderNodeOutputMaterial');b.link(em.outputs[0],out.inputs['Surface']);mat.use_backface_culling=True
    for aov,value in [('zzz_lut_weight',1.)]:
        a=b.node('ShaderNodeOutputAOV');a.aov_name=aov;a.inputs['Value'].default_value=value
    depth=b.node('ShaderNodeCameraData');a=b.node('ShaderNodeOutputAOV');a.aov_name='zzz_view_z';b.link(depth.outputs['View Z Depth'],a.inputs['Value'])
    mat['zzz_native_outline']=json.dumps(p,sort_keys=True);return mat

def apply(ob,scene,te,prm,profile,settings):
    p=validate(profile)
    if not p or not load_inputs(ob,p):return False
    if scene.camera is None or scene.camera.data.type!='PERSP':raise ValueError('Native outline production path currently requires a perspective camera')
    prepare_skin(ob);mat=material('ZZZ_NativeOutline_'+ob.name,te,prm,p,settings);fl=prm['floats']
    required=('_OutlineWidth','_OutlineWidthUIAdjustment','_MaxOutlineZOffset')
    if any(k not in fl for k in required):raise ValueError('Native outline material lacks width/depth parameters')
    group=geometry(scene,fl['_OutlineWidth'],fl['_OutlineWidthUIAdjustment'],fl['_MaxOutlineZOffset'],p,mat)
    md=ob.modifiers.new('ZZZ_Outline','NODES');md.node_group=group;ob['zzz_native_outline']=True;return True
