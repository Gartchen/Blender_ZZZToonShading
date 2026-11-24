"""Opt-in scene-depth feature resolve for the verified UI eye/hair variants.

Feature RGB is already premultiplied. Resolve H*(1-T*A)+T*F, before LUT.
This is a Blender depth approximation, not a replay of unknown D3D stencil
descriptors. Generated outline shells are omitted from feature depth; their
real surfaces remain occluders. Other non-feature surfaces are opaque holdouts.
"""
LAYER = 'ZZZ_FeaturePass'
FLAG = 'zzz_feature_pass'
MODE = 'premultiplied_features_v1'
WRAPPER = 'ZZZ_FeatureSurface'


def enabled(profile):
    return (profile or {}).get('composite') == MODE


def setup(scene, features, hair):
    import bpy
    if not scene.render.film_transparent:
        raise ValueError('Feature layers require transparent film for holdout alpha')
    if not features or not hair:
        raise ValueError('Feature layers require explicit eye/brow and transparent-hair materials')
    main = scene.view_layers[0]
    if 'zzz_hair' not in main.aovs:
        aov = main.aovs.add()
        aov.name = 'zzz_hair'
        aov.type = 'VALUE'
    layer = scene.view_layers.get(LAYER) or scene.view_layers.new(LAYER)
    layer.use = True
    layer.use_pass_combined = True
    for vl in scene.view_layers:
        vl[FLAG] = float(vl == layer)
        if vl.name == 'ZZZ_EyePass':
            vl.use = False
    def copy_collections(src, dst):
        dst.exclude = src.exclude
        dst.holdout = src.holdout
        dst.indirect_only = src.indirect_only
        for child in src.children:
            other = dst.children.get(child.name)
            if other:
                copy_collections(child, other)
    for child in main.layer_collection.children:
        copy_collections(child, layer.layer_collection.children[child.name])
    # Include materials used by generated geometry-node outline helpers too.
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        tree = mat.node_tree
        out = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
        if not out or not out.inputs['Surface'].is_linked:
            continue
        wrapper = tree.nodes.get(WRAPPER)
        if wrapper:
            old = wrapper.inputs[1].links[0].from_socket
            tree.links.new(old, out.inputs['Surface'])
            for name in (WRAPPER, WRAPPER+'_flag', WRAPPER+'_replacement'):
                node = tree.nodes.get(name)
                if node:
                    tree.nodes.remove(node)
        if mat in features:
            continue
        old = out.inputs['Surface'].links[0].from_socket
        attr = tree.nodes.new('ShaderNodeAttribute')
        attr.name = WRAPPER+'_flag'
        attr.attribute_type = 'VIEW_LAYER'
        attr.attribute_name = FLAG
        generated_outline = 'Outline' in mat.name and mat.name.startswith(('ZZZ_', 'Native'))
        node = tree.nodes.new('ShaderNodeBsdfTransparent' if mat in hair or generated_outline else 'ShaderNodeHoldout')
        node.name = WRAPPER+'_replacement'
        mix = tree.nodes.new('ShaderNodeMixShader')
        mix.name = WRAPPER
        mix.label = 'Main: unchanged / Feature: depth coverage'
        tree.links.new(attr.outputs['Fac'], mix.inputs[0])
        tree.links.new(old, mix.inputs[1])
        tree.links.new(node.outputs[0], mix.inputs[2])
        tree.links.new(mix.outputs[0], out.inputs['Surface'])
    return layer


def from_character(scene, objs, profile):
    from .character import slots
    features, hair = set(), set()
    for ob in objs:
        if ob.type != 'MESH' or ob.hide_render:
            continue
        for mat in ob.data.materials:
            if mat and mat.use_nodes and any(n.type == 'OUTPUT_AOV' and n.aov_name in ('zzz_eyes', 'zzz_brows') for n in mat.node_tree.nodes):
                features.add(mat)
        for i, name in enumerate(slots(ob.data)):
            if name in profile['hair_materials']:
                hair.add(ob.data.materials[i])
    return setup(scene, features, hair)


def resolve(tree, main, feature, source=None):
    from . import core
    from .native_lighting import Ops
    class Target:
        node_tree = tree
    op = Ops(core.NB(Target()))
    h = source if source is not None else main.outputs['Image']
    t, a, f = main.outputs['zzz_hair'], feature.outputs['Alpha'], feature.outputs['Image']
    rgb = op.v('ADD', op.scale(h, op.sub(1., op.mul(t, a))), op.scale(f, t))
    alpha = tree.nodes.new('CompositorNodeSetAlpha')
    alpha.label = 'Premultiplied features; original silhouette alpha'
    tree.links.new(rgb, alpha.inputs['Image'])
    tree.links.new(main.outputs['Alpha'], alpha.inputs['Alpha'])
    alpha.inputs['Type'].default_value = 'Replace Alpha'
    return alpha.outputs[0]
