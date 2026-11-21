"""Opt-in UI transparent cloth after opaque LUT/rim, before unchanged bloom.

Uses current geometry and EEVEE depth/coverage; not native half-resolution MSAA.
Exact PS d72591ee44f3a191 output: RGB*A.R, alpha=A.R when AlphaBlendAlphaParams.x<0.
Only the verified neutral saturation/grading branch is supported by this profile.
"""
LAYER='ZZZ_TransparentPass'
FLAG='zzz_transparent_pass'
WRAPPER='ZZZ_TransparentSurface'


def validate(p):
    if p is None:return None
    if p.get('schema')!='zzz-toon-transparent/1' or not p.get('source') or not p.get('scope'):
        raise ValueError('Transparent profile requires schema, source and scope')
    if p.get('ps')!='d72591ee44f3a191' or p.get('mode')!='coverage_after_character_lut':
        raise ValueError('Unsupported transparent shader/resolve')
    if p.get('alpha_blend_params')!=[-1.,1.,0.,0.] or p.get('neutral_grading') is not True:
        raise ValueError('Unverified transparent alpha/grading inputs')
    if not isinstance(p.get('materials'),list) or not p['materials']:
        raise ValueError('Transparent profile requires explicit material selection')
    return p


def clear(scene):
    import bpy
    for mat in bpy.data.materials:
        if not mat.use_nodes:continue
        nt=mat.node_tree;wrap=nt.nodes.get(WRAPPER)
        if not wrap:continue
        original=wrap.inputs[int(wrap.get('original_input',1))].links[0].from_socket
        for link in list(wrap.outputs[0].links):nt.links.new(original,link.to_socket)
        for name in (WRAPPER,WRAPPER+'_flag',WRAPPER+'_replacement'):
            node=nt.nodes.get(name)
            if node:nt.nodes.remove(node)
    layer=scene.view_layers.get(LAYER)
    if layer:layer.use=False
    for vl in scene.view_layers:vl[FLAG]=0.


def setup(scene, materials):
    import bpy
    clear(scene)
    if not materials:return None
    if not scene.render.film_transparent:raise ValueError('Transparent layers require transparent film')
    main=scene.view_layers[0];layer=scene.view_layers.get(LAYER) or scene.view_layers.new(LAYER);layer.use=True;layer.use_pass_combined=True
    for vl in scene.view_layers:vl[FLAG]=float(vl==layer)
    def copy(src,dst):
        dst.exclude=src.exclude;dst.holdout=src.holdout;dst.indirect_only=src.indirect_only
        for ch in src.children:
            if ch.name in dst.children:copy(ch,dst.children[ch.name])
    for ch in main.layer_collection.children:copy(ch,layer.layer_collection.children[ch.name])
    for mat in bpy.data.materials:
        if not mat.use_nodes:continue
        nt=mat.node_tree;out=next((n for n in nt.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output),None)
        if not out or not out.inputs['Surface'].is_linked:continue
        original=out.inputs['Surface'].links[0].from_socket
        attr=nt.nodes.new('ShaderNodeAttribute');attr.name=WRAPPER+'_flag';attr.attribute_type='VIEW_LAYER';attr.attribute_name=FLAG
        replacement=nt.nodes.new('ShaderNodeBsdfTransparent' if mat in materials else 'ShaderNodeHoldout');replacement.name=WRAPPER+'_replacement'
        mix=nt.nodes.new('ShaderNodeMixShader');mix.name=WRAPPER;mix['original_input']=2 if mat in materials else 1;mix.label='Opaque/feature pass or late transparent pass';nt.links.new(attr.outputs['Fac'],mix.inputs[0])
        for index,sock in [(1,replacement.outputs[0] if mat in materials else original),(2,original if mat in materials else replacement.outputs[0])]:nt.links.new(sock,mix.inputs[index])
        nt.links.new(mix.outputs[0],out.inputs['Surface'])
        # Depth holdouts must be deterministic even on originally blended materials.
    return layer


def resolve(scene,tree,src):
    layer=scene.view_layers.get(LAYER)
    if layer is None or not layer.use:return src
    rl=tree.nodes.new('CompositorNodeRLayers');rl.scene=scene;rl.layer=LAYER;rl.label='Current transparent cloth, depth tested'
    from .core import NB
    from .native_lighting import Ops
    class Target:node_tree=tree
    b=NB(Target());o=Ops(b);a=rl.outputs['Alpha'];remain=o.sub(1.,a)
    rgb=o.v('ADD',o.scale(src,remain),rl.outputs['Image'])
    sep=b.node('CompositorNodeSeparateColor');b.link(src,sep.inputs['Image'])
    over=b.node('CompositorNodeSetAlpha');over.label='Native transparent cloth after opaque LUT/rim'
    b.link(rgb,over.inputs['Image']);b.link(o.add(a,o.mul(remain,sep.outputs['Alpha'])),over.inputs['Alpha']);over.inputs['Type'].default_value='Replace Alpha'
    return over.outputs[0]
