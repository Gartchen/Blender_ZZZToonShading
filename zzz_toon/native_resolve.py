"""Opaque rim depth resolve for DeferredShadingForCharacter (captured fragment 26).

This is a rendering pass, placed before the existing eye mix and bloom. It does
not change lens, light, color management or bloom. Float AOV/filtering differs
from the game's GBuffer format; face-specific rim and Unity shadow maps remain
outside this implementation. Blender 5.3 compositor API required.
"""
from .native_lighting import Ops, inputs

AOVS={'zzz_rim':'COLOR','zzz_rim_skin':'VALUE','zzz_rim_surface':'VALUE',
      'zzz_rim_ndv':'VALUE','zzz_rim_cross':'COLOR','zzz_rim_uv':'COLOR','zzz_view_z':'VALUE'}

def register_passes(scene):
    main=scene.view_layers[0]
    for name,kind in AOVS.items():
        if name not in main.aovs:
            a=main.aovs.add();a.name=name;a.type=kind

def build(scene,tree,rl,src,p):
    from .core import NB
    # NB needs only node_tree; reuse it to keep exact math operations consistent.
    class Target:node_tree=tree
    b=NB(Target());o=Ops(b);g=inputs(p)
    if 'zzz_rim' not in rl.outputs:return src
    def split(value):
        n=b.node('ShaderNodeSeparateXYZ');b.link(value,n.inputs[0]);return n.outputs
    def vector(x,y,z=0.):
        n=b.node('ShaderNodeCombineXYZ')
        for i,v in enumerate((x,y,z)):
            if isinstance(v,(int,float)):n.inputs[i].default_value=v
            else:b.link(v,n.inputs[i])
        return n.outputs[0]
    # Driver-backed dimensions remain correct when output resolution changes.
    def driven(label,expression,variables):
        n=b.node('ShaderNodeValue');n.label=label
        d=n.outputs[0].driver_add('default_value').driver;d.expression=expression
        for name,owner,path in variables:
            v=d.variables.new();v.name=name;v.type='SINGLE_PROP'
            v.targets[0].id_type='SCENE' if owner==scene else 'CAMERA'
            v.targets[0].id=owner;v.targets[0].data_path=path
        return n.outputs[0]
    w=driven('Render width','x*p/100',[('x',scene,'render.resolution_x'),('p',scene,'render.resolution_percentage')])
    h=driven('Render height','x*p/100',[('x',scene,'render.resolution_y'),('p',scene,'render.resolution_percentage')])
    # Native projection matrix m11 = vertical focal scale. No camera mutation.
    if scene.camera and scene.camera.data.type=='PERSP':
        cam=scene.camera.data
        vertical=cam.sensor_fit=='VERTICAL' or (cam.sensor_fit=='AUTO' and scene.render.resolution_y>scene.render.resolution_x)
        focal=driven('Projection m11','2*l/s',[('l',cam,'lens'),('s',cam,'sensor_height' if vertical else 'sensor_width')])
        if not vertical:focal=o.mul(focal,o.div(w,h))
        projection=o.mul(.36397022,focal)
    else:projection=.5
    uv=split(rl.outputs['zzz_rim_uv']);cross=split(rl.outputs['zzz_rim_cross'])
    raw_z=rl.outputs['zzz_view_z']
    z=o.mix(o.m('LESS_THAN',raw_z,1e-6),raw_z,1e6)
    def sample(dx,dy):
        # Pixel-centre UV, clamped edges, bilinear LOD0 approximation.
        n=b.node('CompositorNodeMapUV')
        # Map UV's third component is coverage, not spatial Z. Zero silently
        # discards every sample, so always supply one for valid depth taps.
        b.link(z,n.inputs['Image']);b.link(vector(o.add(uv[0],o.div(dx,w)),o.add(uv[1],o.div(dy,h)),1.),n.inputs['UV'])
        n.inputs['Extension X'].default_value='Extend';n.inputs['Extension Y'].default_value='Extend'
        return split(n.outputs[0])[0]
    depth_bias=o.add(.02,o.mul(.0125,z))
    skin_sum=0.
    for i in range(1,9):
        for sx,sy in [(1,1),(-1,-1),(1,-1),(-1,1)]:
            d=sample(sx*i*g['rim_width'],sy*i*g['rim_width'])
            step=o.smooth(o.mul(100.,o.sub(o.sub(d,z),depth_bias)))
            skin_sum=o.add(skin_sum,o.mul(9-i,step))
    skin_mask=o.m('MINIMUM',1.,o.mul(1/36,skin_sum))
    border_x=o.smooth(o.div(o.sub(1.,o.m('ABSOLUTE',o.sub(o.mul(2.,uv[0]),1.))),o.mul(.03,o.div(h,w))))
    border_y=o.smooth(o.div(o.sub(1.,o.m('ABSOLUTE',o.sub(o.mul(2.,uv[1]),1.))),.03))
    border=o.m('MINIMUM',border_x,border_y)
    radius=o.div(o.mul(o.mul(o.mul(.0015,w),o.sub(4.,o.mul(1.2,o.sat(o.sub(z,2.5))))),projection),o.add(z,3.))
    radius=o.mul(g['rim_width'],o.m('MAXIMUM',1.,radius))
    # PS26 uses opposite comparisons at zero. Native target UV and Blender
    # Window UV increase along the same axes in the registered character pass.
    sx=o.mix(o.m('LESS_THAN',cross[1],0.),-1.,.999989986)
    sy=o.mix(o.m('GREATER_THAN',cross[0],0.),1.,-.999989986)
    dx=o.mul(sx,radius);dy=o.mul(sy,radius)
    taps=[(dx,o.mul(-1.,dy)),(o.mul(-1.,dx),dy),(dx,dy),(o.mul(-1.,dx),o.mul(-1.,dy)),
          (o.mul(2.,dx),dy),(o.mul(-2.,dx),o.mul(-1.,dy)),(0.,dy),(0.,o.mul(-1.,dy))]
    furthest=sample(*taps[0])
    for tap in taps[1:]:furthest=o.m('MAXIMUM',furthest,sample(*tap))
    slope=o.sub(15.,o.mul(14.,o.sat(o.mul(5.,rl.outputs['zzz_rim_ndv']))))
    lo=o.mul(slope,o.add(.03,o.mul(.0125,z)));hi=o.mul(slope,o.add(.06,o.mul(.0125,z)))
    body_mask=o.smooth(o.div(o.sub(o.sub(furthest,z),lo),o.sub(hi,lo)))
    mask=o.mul(border,o.mix(rl.outputs['zzz_rim_skin'],body_mask,skin_mask))
    mask=o.mul(mask,rl.outputs['zzz_rim_surface'])
    rim=o.scale(o.v('MULTIPLY',rl.outputs['zzz_rim'],rl.outputs['zzz_rim']),25.)
    result=o.v('ADD',src,o.scale(rim,mask))
    a=b.node('CompositorNodeSetAlpha');b.link(result,a.inputs['Image']);b.link(rl.outputs['Alpha'],a.inputs['Alpha'])
    a.inputs['Type'].default_value='Replace Alpha'
    a.label='Native character rim depth resolve (before bloom)'
    return a.outputs['Image']
