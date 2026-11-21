"""Opt-in character LUT from exact DeferredShadingForCharacter PS26.

Runs on the opaque character mask before rim, eye overlay and existing bloom.
Textures are linear RGBA EXR strips, with DDS row zero at Blender UV y=0.
Captures are external scene profiles, never built-in character defaults.
"""
import math,os

def _image(profile):
    import bpy
    size = profile['size']
    if profile.get('source_format') == 'dds-rgba16f':
        import hashlib
        from pathlib import Path
        import numpy as np
        digest = profile['source']
        image = next((im for im in bpy.data.images if im.get('zzz_lut_sha256') == digest and im.packed_file), None)
        if image:
            return image
        raw = Path(profile['texture']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest or len(raw) != 128 + size**3 * 8:
            raise ValueError("Captured LUT has changed")
        image = bpy.data.images.new("ZZZ Capture LUT " + digest[:16], width=size**2, height=size,
                                    alpha=True, float_buffer=True, is_data=True)
        image.file_format = 'OPEN_EXR'
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'CHANNEL_PACKED'
        image.pixels.foreach_set(np.frombuffer(raw, '<f2', offset=128).astype('f'))
        image.update()
        image['zzz_lut_sha256'] = digest
    else:
        image = bpy.data.images.load(profile['texture'], check_existing=True)
        if tuple(image.size) != (size*size, size):
            raise ValueError('LUT dimensions do not match profile')
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'CHANNEL_PACKED'
    if not image.packed_file:
        image.pack()
    return image

def validate(p):
    if p is None:return None
    if not isinstance(p,dict) or p.get('schema')!='zzz-toon-character-lut/1':raise ValueError('Unknown character LUT schema')
    if not p.get('source') or not p.get('scope'):raise ValueError('Character LUT requires source and scope')
    if not isinstance(p.get('enabled'),bool):raise ValueError('Character LUT enabled must be explicit')
    if type(p.get('size')) is not int or not 2<=p['size']<=128:raise ValueError('Invalid LUT size')
    if not isinstance(p.get('exposure'),(int,float)) or not math.isfinite(p['exposure']) or p['exposure']<=0:raise ValueError('Invalid LUT exposure')
    if not isinstance(p.get('texture'),str) or not os.path.isfile(p['texture']):raise ValueError('Character LUT texture is missing')
    if p.get('layout')!='linear-strip-dds-row0-at-uv0':raise ValueError('Unsupported character LUT layout')
    return p

def register_passes(scene):
    vl=scene.view_layers[0]
    if 'zzz_lut_weight' not in vl.aovs:a=vl.aovs.add();a.name='zzz_lut_weight';a.type='VALUE'


def shader(b, src, profile):
    """Same native LUT in a material, before transparent coverage blending."""
    import bpy
    from .native_lighting import Ops
    p=validate(profile)
    if not p or not p['enabled']:return src
    o=Ops(b);size=p['size'];im=_image(p)
    sep=b.node('ShaderNodeSeparateXYZ');b.link(src,sep.inputs[0])
    q=[o.mul(size-1,o.sat(o.add(o.mul(.0734997839,o.m('LOGARITHM',o.m('MAXIMUM',1e-30,o.add(o.mul(x,p['exposure']*5.55555582),.0479959995)),2.)),.386036009))) for x in sep.outputs]
    lo=[o.m('FLOOR',x) for x in q];hi=[o.m('MINIMUM',size-1,o.add(x,1.)) for x in lo];fraction=[o.sub(q[i],lo[i]) for i in range(3)]
    def sample(x,y,z):
        uv=b.node('ShaderNodeCombineXYZ')
        b.link(o.div(o.add(o.add(o.mul(z,size),x),.5),size*size),uv.inputs[0]);b.link(o.div(o.add(y,.5),size),uv.inputs[1])
        # Closest disables derivative mip filtering in EEVEE. Interpolate eight
        # exact texel centres explicitly to reproduce native SampleLevel(...,0).
        tap=b.node('ShaderNodeTexImage');tap.image=im;tap.interpolation='Closest';tap.extension='EXTEND';tap.label='Native transparent LUT LOD0 texel';b.link(uv.outputs[0],tap.inputs['Vector'])
        return tap.outputs['Color']
    slices=[]
    for z in (lo[2],hi[2]):
        rows=[o.cmix(fraction[0],sample(lo[0],y,z),sample(hi[0],y,z)) for y in (lo[1],hi[1])]
        slices.append(o.cmix(fraction[1],rows[0],rows[1]))
    return o.cmix(fraction[2],slices[0],slices[1])

def build(tree,src,weight,alpha,profile):
    import bpy
    from .core import NB
    from .native_lighting import Ops
    p=validate(profile)
    if not p or not p['enabled']:return src
    class Target:node_tree=tree
    b=NB(Target());o=Ops(b);size=p['size']
    im=_image(p)
    texture=b.node('CompositorNodeImage');texture.image=im;texture.label='Captured native character LUT (linear)'
    sep=b.node('ShaderNodeSeparateXYZ');b.link(src,sep.inputs[0])
    q=[o.mul(size-1,o.sat(o.add(o.mul(.0734997839,o.m('LOGARITHM',o.m('MAXIMUM',1e-30,o.add(o.mul(x,p['exposure']*5.55555582),.0479959995)),2.)),.386036009))) for x in sep.outputs]
    blue=o.m('FLOOR',q[2]);fraction=o.sub(q[2],blue)
    def sample(z):
        uv=b.node('ShaderNodeCombineXYZ')
        b.link(o.div(o.add(o.add(o.mul(z,size),q[0]),.5),size*size),uv.inputs[0])
        b.link(o.div(o.add(q[1],.5),size),uv.inputs[1]);uv.inputs[2].default_value=1.
        tap=b.node('CompositorNodeMapUV');b.link(texture.outputs['Image'],tap.inputs['Image']);b.link(uv.outputs[0],tap.inputs['UV'])
        tap.inputs['Extension X'].default_value='Extend';tap.inputs['Extension Y'].default_value='Extend'
        return tap.outputs[0]
    transformed=o.cmix(fraction,sample(blue),sample(o.m('MINIMUM',size-1,o.add(blue,1.))))
    rgb=o.cmix(weight,src,transformed)
    out=b.node('CompositorNodeSetAlpha');b.link(rgb,out.inputs['Image']);b.link(alpha,out.inputs['Alpha']);out.inputs['Type'].default_value='Replace Alpha'
    out.label='Native character LUT before rim / unchanged bloom'
    return out.outputs[0]
