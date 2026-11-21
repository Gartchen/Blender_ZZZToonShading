"""Opt-in light snapshots. No character or captured-frame defaults live here."""
import math


def resolve(profile):
    if profile is None:
        return None
    if profile.get('schema') != 'zzz-toon-light-snapshot/1':
        raise ValueError('Unknown light snapshot schema')
    if not profile.get('source') or not profile.get('scope'):
        raise ValueError('Light snapshots require source and scope provenance')
    basis = profile.get('unity_to_blender_world')
    if not isinstance(basis, (list, tuple)) or len(basis) != 3 or any(len(r) != 3 for r in basis):
        raise ValueError('Light snapshot needs a 3x3 world-basis matrix')
    if any(not math.isfinite(v) for row in basis for v in row):
        raise ValueError('Non-finite light basis')
    if any(abs(sum(basis[i][k]*basis[j][k] for k in range(3))-(i == j)) > 1e-5
           for i in range(3) for j in range(3)):
        raise ValueError('Light basis must be orthogonal; scale/shear cannot compensate lighting')
    result = {'up': tuple(row[1] for row in basis)}
    for key in ('body', 'hair'):
        v = profile.get(key)
        if not isinstance(v, (list, tuple)) or len(v) != 3 or any(not math.isfinite(x) for x in v):
            raise ValueError('Snapshot requires finite body and hair vectors')
        if abs(sum(x*x for x in v)-1.) > 1e-4:
            raise ValueError('Captured light direction must already be unit length')
        # Do not normalize, clamp, rotate with the animated armature, or fit.
        result[key] = tuple(sum(row[k]*v[k] for k in range(3)) for row in basis)
    return result


def direction(b, settings, attribute='zzz_surface_hair'):
    data = resolve(settings.get('native_light_profile'))
    if data is None:
        return None
    attr = b.node('ShaderNodeAttribute'); attr.attribute_name = attribute
    body = b.math('LESS_THAN', attr.outputs['Fac'], .5)
    # Exact native branch: v7.z < .5 is body, otherwise hair (including .5).
    vectors = {}
    for role in ('body', 'hair'):
        node = b.node('ShaderNodeVectorMath', operation='ADD')
        node.inputs[0].default_value = data[role]
        node['zzz_capture_light_role'] = role
        node.label = 'Captured ' + role + ' light (world)'
        vectors[role] = node.outputs[0]
    hair = b.math('SUBTRACT', 1., body)
    # Vector arithmetic avoids colour-node assumptions for negative directions.
    terms = []
    for role, weight in (('body', body), ('hair', hair)):
        node = b.node('ShaderNodeVectorMath', operation='SCALE')
        b.link(vectors[role], node.inputs[0]); b.link(weight, node.inputs['Scale'])
        terms.append(node.outputs[0])
    node = b.node('ShaderNodeVectorMath', operation='ADD')
    for i, term in enumerate(terms): b.link(term, node.inputs[i])
    node.label = 'Native override light: vertex flag < 0.5'
    return node.outputs[0]
