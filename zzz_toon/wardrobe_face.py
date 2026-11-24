"""Wardrobe face outline. Captured directions are transported with the animated face surface."""
import json
from pathlib import Path

import bpy
import numpy as np

from . import core, light_profiles, native_outline, outline_runtime, preview
from .native_lighting import Ops


def direction(b, op, geometry, position):
    def components(key):
        attr=b.node('GeometryNodeInputNamedAttribute',data_type='FLOAT_VECTOR')
        attr.inputs['Name'].default_value=key
        split=b.node('ShaderNodeSeparateXYZ')
        b.link(attr.outputs[0],split.inputs[0])
        return split.outputs

    indices=components('zzz_face_outline_neighbors')
    weights=components('zzz_face_outline_basis')
    edges=[]
    for index in indices[:2]:
        sample=b.node('GeometryNodeSampleIndex',data_type='FLOAT_VECTOR',domain='POINT')
        b.link(geometry,sample.inputs['Geometry'])
        b.link(position,sample.inputs['Value'])
        b.link(index,sample.inputs['Index'])
        edges.append(op.v('SUBTRACT',sample.outputs[0],position))
    u=op.v('NORMALIZE',edges[0])
    n=op.v('NORMALIZE',op.v('CROSS_PRODUCT',edges[0],edges[1]))
    v=op.v('CROSS_PRODUCT',n,u)
    return op.v('ADD',op.v('ADD',op.scale(u,weights[0]),op.scale(v,weights[1])),op.scale(n,weights[2]))


def shade(mat,profile,settings):
    b=core.NB(mat)
    o=Ops(b)
    tex=next(n for n in mat.node_tree.nodes if n.type=='TEX_IMAGE' and n.image and 'Face' in n.image.name)
    emission=next(n for n in mat.node_tree.nodes if n.type=='EMISSION')
    p=profile['material']
    attr=b.node('ShaderNodeAttribute');attr.attribute_name='zzz_face_outline_code'
    value=b.rgb(p['_OutlineColor'])
    for threshold,name in [(.8,'_OutlineColor2'),(.6,'_OutlineColor3')]:
        value=b.mixc(o.m('LESS_THAN',attr.outputs['Fac'],threshold),value,b.rgb(p[name]))
    normal=b.node('ShaderNodeAttribute');normal.attribute_name='native_outline_normal'
    view=b.node('ShaderNodeVectorTransform',vector_type='VECTOR',convert_from='WORLD',convert_to='CAMERA')
    b.link(normal.outputs['Vector'],view.inputs[0])
    world=b.node('ShaderNodeVectorTransform',vector_type='VECTOR',convert_from='CAMERA',convert_to='WORLD')
    b.link(o.v('MULTIPLY',view.outputs[0],(1.,1.,-1.)),world.inputs[0])
    light=light_profiles.direction(b,settings,attribute='zzz_outline_hair')
    ndl=o.v('DOT_PRODUCT',o.v('NORMALIZE',world.outputs[0]),light)
    env=settings['native_environment']
    position=b.node('ShaderNodeAttribute');position.attribute_name='native_outline_position'
    delta=o.v('SUBTRACT',tuple(env['entity_position']),position.outputs['Vector'])
    att=o.sat(o.sub(1.,o.div(o.v('DOT_PRODUCT',delta,delta),max(1e-30,env['entity_range']**2))))
    lc=o.cmix(att,tuple(env['main_color']),tuple(env['entity_color']))
    # All three captured face outline colors have alpha one in these two outfits.
    rgb=native_outline.color(b,tex.outputs['Color'],0.,value,1.,tuple(p['_Color'][:3]),ndl,
                             settings['native_outline']['visibility'],tuple(p['_PostOutlineTint'][:3]),lc,
                             tuple(p['_CharacterAmbient'][:3]),tuple(settings['native_outline']['vertex_ambient']))
    b.link(rgb,emission.inputs['Color'])


def apply(scene,role,session=None):
    base=Path(__file__).with_name('wardrobe_data')/role
    file=base/'face_outline.json'
    if not file.is_file():
        return
    profile=json.loads(file.read_text())
    obj=scene.objects.get(profile['mesh'])
    mod=obj.modifiers.get('ZZZ_Outline') if obj else None
    if not mod or not mod.node_group or mod.node_group.get('zzz_wardrobe_face'):
        return
    old=obj.data
    mat=next((m for m in old.materials if m and m.name.startswith('ZZZ_Outline_') and m.node_tree),None)
    if mat is None:
        return
    from .wardrobe_slots import selected
    override=selected(scene.zzz_toon,role).face_outline
    source = Path(bpy.path.abspath(override)) if override else base/'face_outline.npz'
    if not source.is_file():
        return
    with np.load(source) as a:
        vertices=np.empty(len(old.vertices)*3,'f');old.vertices.foreach_get('co',vertices)
        loops=np.empty(len(old.loops),'i');old.loops.foreach_get('vertex_index',loops)
        uv=np.empty(len(loops)*2,'f');old.uv_layers['UV0'].data.foreach_get('uv',uv)
        if (vertices.size!=a['position'].size or not np.array_equal(loops,a['loops'].ravel())
                or not np.allclose(vertices,a['position'].ravel(),rtol=0.,atol=2e-5)
                or not np.allclose(uv,a['uv'].ravel(),rtol=0.,atol=2e-5)):
            if override:
                raise ValueError('脸部轮廓数据与所选模型的顶点、索引或 UV 不匹配')
            return
        me=old
        if session:
            me=old.copy()
            session.restore.callback(bpy.data.meshes.remove,me)
            session.keep(obj,'data',me)
        put=outline_runtime.put
        put(me,'zzz_face_outline_neighbors',a['neighbors'])
        put(me,'zzz_face_outline_basis',a['coeff'])
        rgba=a['rgba']
        put(me,'zzz_ol_rg',np.c_[rgba[:,:2],np.zeros(len(rgba))])
        put(me,'zzz_face_outline_code',.9-.2*(np.rint(rgba[:,2]*255).astype(int)&3),kind='FLOAT')
        put(me,'zzz_face_outline_surface',a['face_mask'],kind='BOOLEAN',domain='FACE')
    settings=preview._load(preview.DATA/role/'preview.json', require_files=False)['inputs']
    material=mat.copy()
    material.name='ZZZ_Outline_'+role+'_Face_Capture'
    if session:session.restore.callback(bpy.data.materials.remove,material)
    shade(material,profile,settings)
    g=outline_runtime.geometry(scene,profile['width'],profile['width_adjust'],profile['depth_bias'],
                               profile,material,face_direction=direction)
    g['zzz_wardrobe_face']=True
    if session:
        session.restore.callback(bpy.data.node_groups.remove,g)
        session.keep(mod,'node_group',g)
    else:
        mod.node_group=g

