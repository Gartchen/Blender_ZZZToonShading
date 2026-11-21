"""Explicit UIEye PS14 / UI hair PS1121 blending corrections.

The eye/brow view-layer compositor remains a coverage approximation, not a
Unity stencil/depth implementation. No character name or fitted gain is used.
"""
import json


def validate(profile):
    if profile is None:
        return None
    if profile.get('schema') != 'zzz-toon-face-layers/1':
        raise ValueError('Unknown face-layer schema')
    if not profile.get('source') or not profile.get('scope') or not profile.get('bone'):
        raise ValueError('Face layers require source, scope and an explicit head bone')
    if profile.get('eye_ps') != '720a454f81097a64' or profile.get('hair_ps') != '050d63a297594dff':
        raise ValueError('Face-layer shader variants have not been verified')
    if profile.get('inverse_head_row') != 1:
        raise ValueError('Only the verified head local Y binding is supported')
    if profile.get('composite') not in (None, 'premultiplied_features_v1'):
        raise ValueError('Unknown feature compositor')
    if not isinstance(profile.get('hair_materials'),list) or not profile['hair_materials'] or any(not isinstance(n,str) or not n for n in profile['hair_materials']):
        raise ValueError('Face layers require explicitly identified transparent hair draw materials')
    return profile


def bind(arm, scene, profile):
    profile = validate(profile)
    if profile is None:
        return None
    import bpy
    if arm is None or profile['bone'] not in arm.pose.bones:
        raise ValueError('Face-layer head bone not found: ' + profile['bone'])
    name = 'ZZZ_FaceLayers_' + arm.name
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, None)
        scene.collection.objects.link(ob)
    ob.parent = arm
    ob.parent_type = 'BONE'
    ob.parent_bone = profile['bone']
    ob.matrix_parent_inverse.identity()
    ob.matrix_basis.identity()
    ob.empty_display_size = .015
    ob.hide_render = True
    ob['zzz_face_layers_profile'] = json.dumps(profile, sort_keys=True)
    return ob


def apply_eye_inputs(ob, profile):
    """Restore explicit numeric lookup bytes, validating the destination mesh.

    A sparse -1 means unspecified, never an inferred index. The original
    vertex colour is preserved because it may carry other shader inputs.
    """
    item = (profile or {}).get('eye_inputs', {}).get(ob.name.split('.')[0])
    if item is None:
        return None
    import numpy as np
    me = ob.data
    with np.load(item['path']) as a:
        pos = np.array([v.co[:] for v in me.vertices])
        loops = np.array([l.vertex_index for l in me.loops])
        uv = np.array([l.uv[:] for l in me.uv_layers['UV0'].data])
        if (pos.shape != a['position'].shape or not np.allclose(pos,a['position'],atol=2e-6,rtol=0)
                or not np.array_equal(loops,a['loop_vertex']) or uv.shape != a['uv0'].shape
                or not np.allclose(uv,a['uv0'],atol=2e-6,rtol=0)):
            raise ValueError('Eye input topology/UV mismatch: ' + ob.name)
        indices = a['index'].copy()
    if (indices.shape != (len(me.loops),) or not np.isfinite(indices).all()
            or not np.equal(indices,np.rint(indices)).all() or indices.min() < -1 or indices.max() > 255):
        raise ValueError('Eye lookup indices must be byte values or explicit -1')
    at = me.attributes.get('zzz_eye_index')
    if at is not None:me.attributes.remove(at)
    at = me.attributes.new('zzz_eye_index','FLOAT','CORNER')
    at.data.foreach_set('value',indices.astype(np.float32))
    return {'verified_corners':int((indices>=0).sum()),'source':item.get('source')}


def transmittance(b, opacity, head):
    """1-alpha = (1-A.R)*sat((dot(inverseHead.row1,V)-.3)*1.42857146).

    Inverse rows are computed from the full matrix, not normalized columns;
    signed components and nonuniform scale are preserved.
    """
    if head is None:
        raise ValueError('Native hair alpha requires the verified head binding')
    from .native_lighting import Ops
    o = Ops(b)
    cols = []
    for col in range(3):
        n = b.node('ShaderNodeCombineXYZ')
        for row in range(3):
            d = n.inputs[row].driver_add('default_value').driver
            d.type = 'AVERAGE'
            v = d.variables.new(); v.name = 'basis'; v.type = 'SINGLE_PROP'
            v.targets[0].id = head
            # Driver RNA matrix array indices are column-major. mathutils'
            # Python matrix[row][col] view is not the RNA path convention.
            # A rendered rotated/nonuniform-scale fixture checks this mapping.
            v.targets[0].data_path = f'matrix_world[{col}][{row}]'
        cols.append(n.outputs[0])
    row1 = o.scale(o.v('CROSS_PRODUCT', cols[2], cols[0]),
                   o.div(1., o.v('DOT_PRODUCT', cols[0], o.v('CROSS_PRODUCT', cols[1], cols[2]))))
    geo = b.node('ShaderNodeNewGeometry')
    factor = o.sat(o.mul(o.sub(o.v('DOT_PRODUCT', row1, geo.outputs['Incoming']), .300000012), 1.42857146))
    result = o.mul(o.sub(1., opacity), factor)
    result.node.label = 'Native hair transmittance: map * head-view factor'
    return result


def premultiply(b, color, alpha):
    from .native_lighting import Ops
    result = Ops(b).scale(color, alpha)
    result.node.label = 'UIEye PS14: output RGB times alpha'
    return result
