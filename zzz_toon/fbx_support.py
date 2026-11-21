"""Recover verified wardrobe draw inputs from the user's local FBX.

The per-outfit recipes store channel names and verification hashes only. Generated
streams stay in the user's Blender data directory. Manual input takes precedence.
"""
import hashlib
from pathlib import Path
import tempfile

import bpy
import numpy as np




def _write_stream(folder, name, arrays):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + '.npz')
    with tempfile.NamedTemporaryFile(dir=folder, suffix='.npz', delete=False) as handle:
        temp = Path(handle.name)
    try:
        np.savez_compressed(temp, **arrays)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return str(path)


def _values(seq, name, width):
    values = np.empty(len(seq) * width, dtype=np.float32)
    seq.foreach_get(name, values)
    return values.reshape(-1, width)


def _per_point(node, width, index, count):
    fields = {child.id: child.props[0] for child in node.elems if child.props}
    key = {b'LayerElementNormal': b'Normals', b'LayerElementTangent': b'Tangents',
           b'LayerElementBinormal': b'Binormals', b'LayerElementColor': b'Colors',
           b'LayerElementUV': b'UV'}[node.id]
    if fields[b'ReferenceInformationType'] != b'Direct':
        raise ValueError('Expected a direct FBX stream')
    data = np.asarray(fields[key], dtype=np.float32).reshape(-1, width)
    if fields[b'MappingInformationType'] in (b'ByVertice', b'ByVertex') and len(data) == count:
        return data
    if fields[b'MappingInformationType'] == b'ByPolygonVertex' and len(data) == len(index):
        result = np.zeros((count, width), dtype=np.float32)
        result[index] = data
        if np.allclose(result[index], data, atol=2e-6, rtol=0):
            return result
    raise ValueError('FBX corners do not define one value per point')


def _geometry(mesh, node):
    fields = {child.id: child for child in node.elems}
    position = _values(mesh.vertices, 'co', 3)
    raw = np.asarray(fields[b'Vertices'].props[0]).reshape(-1, 3)
    if raw.shape != position.shape or not np.allclose(raw, position, rtol=0, atol=2e-5):
        raise ValueError('FBX rest positions differ')
    corners = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', corners)
    indices = np.asarray(fields[b'PolygonVertexIndex'].props[0])
    if len(indices) != len(corners) or not np.all(indices.reshape(-1, 3)[:, 2] < 0):
        raise ValueError('Expected a triangulated FBX')
    indices = np.where(indices < 0, ~indices, indices)
    if not np.array_equal(np.sort(indices.reshape(-1, 3), axis=1),
                          np.sort(corners.reshape(-1, 3), axis=1)):
        raise ValueError('FBX polygon correspondence differs')
    return fields, indices, position, corners


def _surface(mesh, node, rule):
    fields, indices, pos, corners = _geometry(mesh, node)
    def stream(key, width):
        return _per_point(fields[key], width, indices, len(pos))

    n = stream(b'LayerElementNormal', 3)
    t = stream(b'LayerElementTangent', 3)
    if rule['handedness'] == 'negative_binormal':
        binormal = stream(b'LayerElementBinormal', 3)
        hand = -np.sign(np.einsum('ij,ij->i', np.cross(n, t), binormal))
    else:
        mesh.calc_tangents(uvmap='UV0')
        signs = _values(mesh.loops, 'bitangent_sign', 1).ravel()
        hand = np.sign(np.bincount(corners, weights=signs, minlength=len(pos)))
        mesh.free_tangents()
    if hashlib.sha256(hand.astype('i1').tobytes()).hexdigest() != rule['sha256']:
        raise ValueError('FBX tangent handedness differs from the verified draw')

    # Surface shading consumes the sign only. Outline expansion additionally
    # requires the two original packed coordinates.
    packed = np.zeros((len(pos), 3), dtype=np.float32)
    packed[:, 2] = hand
    uv_nodes = {next(c.props[0].decode() for c in layer.elems if c.id == b'Name'): layer
                for layer in node.elems if layer.id == b'LayerElementUV'}
    if 'm_UV1' in rule:
        packed[:, :2] = _per_point(uv_nodes[rule['m_UV1']], 2, indices, len(pos))
    if 'm_UV3' in rule:
        uv3 = _per_point(uv_nodes[rule['m_UV3']], 2, indices, len(pos))[corners]
        for layer in mesh.uv_layers:
            if np.allclose(_values(layer.data, 'uv', 2), uv3, rtol=0, atol=2e-6):
                mesh['zzz_texcoord3_uv'] = layer.name
                break
    return dict(position=pos, loop_vertex=corners, uv0=_values(mesh.uv_layers['UV0'].data, 'uv', 2),
                normal=n, tangent=t, packed=packed, raw_color=stream(b'LayerElementColor', 4),
                valid=np.ones(len(pos), dtype=bool))


def _recover_face(objects, profile, config, folder, generated):
    """Use FBX face attributes only when all rendered corners match the reference."""
    expected = config.get('face_corner_signature')
    if not expected:
        return
    for name, item in profile['mesh_inputs'].items():
        if Path(item['path']).is_file():
            continue
        mesh = objects[name].data
        color = mesh.color_attributes.get('Attribute')
        if not color or color.domain != 'CORNER' or 'UV0' not in mesh.uv_layers:
            continue
        material_ids = {i for i, mat in enumerate(mesh.materials)
                        if mat and mat.name.split('.')[0] in profile['materials']}
        slots = np.array([p.material_index for p in mesh.polygons], dtype=np.int32)
        fix = config['prepare'].get('eye_slot')
        if fix and fix['object'] == name:
            slots[fix['polygons']] = fix['index']
        valid = np.isin(slots, tuple(material_ids)).repeat(3)
        if len(valid) != len(mesh.loops) or not valid.any():
            continue
        colors = _values(color.data, 'color_srgb', 4)
        blue = np.rint(colors[:, 2] * 255).astype(int)
        flags = np.column_stack((blue & 16, blue & 3, np.zeros(len(blue)))).astype('f')
        for layer in mesh.uv_layers:
            uv = _values(layer.data, 'uv', 2)
            data = np.column_stack((flags[valid, :2], colors[valid, 3], uv[valid]))
            signature = hashlib.sha256(np.rint(data * 1000).astype('<i4').tobytes()).hexdigest()
            if signature != expected:
                continue
            corners = np.array([loop.vertex_index for loop in mesh.loops], dtype=np.int32)
            arrays = dict(position=_values(mesh.vertices, 'co', 3), loop_vertex=corners,
                          uv0=_values(mesh.uv_layers['UV0'].data, 'uv', 2),
                          alpha=colors[:, 3], valid=valid, flags=flags, uv3=uv)
            item['path'] = _write_stream(folder, name + '-face', arrays)
            generated.append(name + ' face flags, alpha and directional UV')
            break


def recover(source, fingerprint, objects, config):
    inputs = config['inputs']
    faces = inputs.get('native_face', {})
    receiver = faces.get('receiver', {})
    surface = inputs.get('native_surface', {}).get('mesh_inputs', {})
    outline = (config['prepare'].get('rest_outline') or inputs['native_outline']).get('mesh_inputs', {})
    rules = config.get('fbx_recovery', {})
    required = {name for name in rules if name in surface and not Path(surface[name]['path']).is_file()}
    required.update(name for name, rule in rules.items() if 'm_UV1' in rule and name in outline
                    and not Path(outline[name]['path']).is_file())
    need_red = receiver and not Path(receiver['red_path']).is_file()
    if need_red:
        required.add(receiver['caster'])
    by_name = {o.name: o for o in objects if o.type == 'MESH'}
    folder = Path(bpy.utils.user_resource('DATAFILES', path='ZZZ Outfit Previews/Derived/v1')) / fingerprint
    generated = []
    # A direct original-mesh input is validated against the final prepared
    # mesh by the same loader as an original outline stream.
    for name, item in surface.items():
        original = Path(item['source'])
        if not Path(item['path']).is_file() and original.suffix.lower() == '.json' and original.is_file():
            item['path'] = str(original)
            generated.append(name + ' skinned tangent basis from local mesh JSON')
    for name in config.get('primary_uv_is_back_uv', []):
        mesh = by_name[name].data
        if 'UV3' not in mesh.uv_layers and 'UV0' in mesh.uv_layers:
            # The existing primary-UV fallback is already exact on this mesh.
            # Advertising it as a second UV would switch a shared material to
            # the two-sided derivative path even on meshes lacking that input.
            generated.append(name + ' verified primary UV for back faces')
    if faces:
        _recover_face(by_name, faces, config, folder, generated)
    if not required:
        return generated
    from io_scene_fbx import parse_fbx
    doc, _ = parse_fbx.parse(str(source))
    models = next(n for n in doc.elems if n.id == b'Objects')
    for node in models.elems:
        if node.id != b'Geometry' or node.props[2] != b'Mesh':
            continue
        name = node.props[1].split(b'\0')[0].decode()
        if name not in required:
            continue
        mesh = by_name[name].data
        if need_red and name == receiver['caster']:
            fields, indices, pos, corners = _geometry(mesh, node)
            red = _per_point(fields[b'LayerElementColor'], 4, indices, len(pos))[:, 0]
            receiver['red_path'] = _write_stream(folder, name + '-caster', dict(red=red, tri=corners.reshape(-1, 3)))
            generated.append(name + ' caster red')
            continue
        rule = rules[name]
        arrays = _surface(mesh, node, rule)
        path = _write_stream(folder, name + '-surface', arrays)
        item = surface.get(name)
        if item and not Path(item['path']).is_file():
            item['path'] = path
            if not Path(item['source']).is_file():
                item['source'] = str(source)
            generated.append(name + ' skinned tangent basis')
        if 'm_UV1' in rule and name in outline and not Path(outline[name]['path']).is_file():
            outline[name]['path'] = path
            generated.append(name + ' outline coordinates')
        if 'm_UV3' in rule and mesh.get('zzz_texcoord3_uv'):
            generated.append(name + ' back-face UV')
        if 'm_UV1' not in rule:
            by_name[name]['zzz_surface_only_frame'] = True
    return generated
