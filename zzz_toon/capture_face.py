"""Shared UI face and stencil inputs, with reflected VS22, PS259/261 and caster VS0 layouts.
Only the paired opaque base / projected receiver with verified neutral auxiliary inputs is supported.
"""
from collections import Counter, defaultdict
from itertools import combinations
import hashlib
import re
import struct
from pathlib import Path

import numpy as np

from . import capture_import, native_face
from .outfit_import import BASIS

FACE = ('bd0cf06981a8f054', '3969ce67a7c8092e')
# Both programs share the same base instructions. The extra blush block is accepted
# only when its captured inputs reduce to the identity.
CORE_FACE_PROGRAMS = {FACE, (FACE[0], '202c3cc697d36317')}
CASTER = ('bcddceab76ee10ce', 'c07b54c6554b2591')


def _field(header, semantic, index, fmt, slot):
    match = re.search(rf'SemanticName: {semantic}\s+SemanticIndex: {index}\s+Format: {fmt}\s+InputSlot: {slot}\s+AlignedByteOffset: (\d+)', header)
    if not match:
        raise ValueError('Unsupported vertex stream: ' + semantic)
    return int(match[1])


def _vertices(files):
    header = capture_import._header(files['vb1.txt'])
    stride = re.search(r'^stride: (\d+)', header, re.M)
    if not stride or not 0 < int(stride[1]) <= 512:
        raise ValueError('Invalid vertex stride')
    raw = np.fromfile(files['vb1.buf'], 'B').reshape(-1, int(stride[1]))
    offset = _field(header, 'COLOR', 0, 'R32G32B32A32_FLOAT', 1)
    rgba = raw[:, offset:offset+16].copy().view('<f4')
    if rgba.shape[1] != 4 or not np.isfinite(rgba).all():
        raise ValueError('Invalid vertex color')
    return header, raw, rgba


def face_corner_indices(source_tri, source_uv, destination_uv, payload):
    """Map split/reordered FBX vertices without guessing at seams or changed UV islands."""
    keys = [tuple(sorted(map(tuple, source_uv[t]))) for t in source_tri]
    targets = [tuple(sorted(map(tuple, t))) for t in destination_uv]
    original, imported = Counter(keys), Counter(targets)
    missing = list((original-imported).elements())
    extra = list((imported-original).elements())
    if len(keys) != len(targets):
        raise ValueError('Face triangle counts differ')
    entries = defaultdict(dict)
    for key, tri in zip(keys, source_tri):
        for index in tri:
            point = tuple(source_uv[index])
            previous = entries[key].setdefault(point, index)
            if not np.array_equal(payload[previous], payload[index]):
                raise ValueError('Ambiguous face input at overlapping UV corners')

    # FBX can triangulate an originally convex quad along its other diagonal.
    # Require identical four UVs, the same perimeter, and constant shader inputs
    # across that quad so interpolating across either diagonal is equivalent.
    edges = defaultdict(list)
    for key in missing:
        for edge in combinations(key, 2):
            edges[edge].append(key)
    replacements = set()
    remaining = Counter(extra)
    for a in missing:
        if a in replacements:
            continue
        candidates = []
        for edge in combinations(a, 2):
            for b in edges[edge]:
                points = set(a) | set(b)
                if len(points) != 4 or b in replacements:
                    continue
                cross = tuple(sorted(points - set(edge)))
                one, two = (tuple(sorted((*cross, v))) for v in edge)
                if not (remaining[one] and remaining[two]):
                    continue
                def side(p0, p1, v):
                    return (p1[0]-p0[0])*(v[1]-p0[1])-(p1[1]-p0[1])*(v[0]-p0[0])
                if (side(*edge,cross[0])*side(*edge,cross[1]) >= 0 or
                        side(*cross,edge[0])*side(*cross,edge[1]) >= 0):
                    continue
                candidates.append((b,one,two))
        if len(candidates) != 1:
            raise ValueError('Unverified change in face triangulation')
        b,one,two = candidates[0]
        values = [i for key in (a,b) for i in entries[key].values()]
        if not np.all(payload[values] == payload[values[0]]):
            raise ValueError('Changed face diagonal carries varying inputs')
        for key in (one,two):
            if key in entries:
                raise ValueError('Ambiguous replacement face')
            entries[key] = {**entries[a], **entries[b]}
            remaining[key] -= 1
        replacements.update((a,b))
    if any(remaining.values()) or len(replacements) != len(missing):
        raise ValueError('Face UV boundaries differ')
    return np.array([entries[key][tuple(point)] for key, tri in zip(targets, destination_uv) for point in tri],dtype='i4')



def _face_stream(obj, slot, files, *, shared_four=False):
    mesh = obj.data
    parsed = capture_import._draw_triangles(files)
    if parsed is None or len(mesh.loops) != len(mesh.polygons)*3 or 'UV0' not in mesh.uv_layers:
        raise ValueError('Missing face triangle layout')
    indices, uv, active = parsed
    header, raw, color = _vertices(files)
    offset = _field(header, 'TEXCOORD', 3, 'R32G32_FLOAT', 1)
    uv3 = raw[:, offset:offset+8].copy().view('<f4')
    if uv3.shape[1] != 2 or not np.isfinite(uv3).all():
        raise ValueError('Invalid secondary face UV')
    loop = np.array([v.vertex_index for v in mesh.loops], dtype='i4')
    base_uv = np.array([v.uv[:] for v in mesh.uv_layers['UV0'].data], dtype='f4')
    visible = np.array([p.material_index == slot for p in mesh.polygons])
    target = loop.reshape(-1,3)[visible]
    # Repeated UV islands can carry different COLOR and UV3. Require the complete
    # original vertex indices, polygon order AND per-corner UV before indexing.
    valid = np.repeat(visible, 3)
    if (target.shape == indices[active].shape and len(target)
            and np.array_equal(np.sort(target, axis=1), np.sort(indices[active], axis=1))
            and np.array_equal(uv[target], base_uv.reshape(-1,3,2)[visible])):
        ids = loop[valid]
    elif shared_four:
        bits = np.floor(color[:,2]*255+.5).astype('i4') & 19
        ids = face_corner_indices(indices[active], uv, base_uv.reshape(-1,3,2)[visible],
                                  np.column_stack((uv3, bits, color[:,3])))
    else:
        raise ValueError('Face vertex indices or UV correspondence differs')
    if np.any((color[ids,2:] < 0.) | (color[ids,2:] > 1.)):
        raise ValueError('Invalid face flags or alpha')
    blue = np.floor(color[ids,2]*255 + .5).astype('i4')
    secondary = np.zeros_like(base_uv)
    flags = np.zeros((len(loop),3), 'f4')
    alpha = np.zeros(len(loop), 'f4')
    secondary[valid] = uv3[ids]
    flags[valid,0], flags[valid,1] = blue & 16, blue & 3
    alpha[valid] = color[ids,3]
    return dict(position=np.array([v.co[:] for v in mesh.vertices], dtype='f4'), loop_vertex=loop,
                uv0=base_uv, uv3=secondary, flags=flags, alpha=alpha, valid=valid)


def _caster_stream(obj, files):
    mesh = obj.data
    head = capture_import._header(files['ib.txt'])
    dtype = {'DXGI_FORMAT_R16_UINT':'<u2', 'DXGI_FORMAT_R32_UINT':'<u4'}.get(
        next((line.split(': ')[-1] for line in head.splitlines() if line.startswith('format:')), ''))
    if not dtype or 'topology: trianglelist' not in head:
        raise ValueError('Invalid caster index stream')
    tri = np.fromfile(files['ib.buf'], dtype).reshape(-1,3)
    if not re.search(r'^first index: 0$', head, re.M) or not re.search(rf'^index count: {tri.size}$', head, re.M):
        raise ValueError('Partial caster index stream')
    mesh.calc_loop_triangles()
    imported = np.array([t.vertices[:] for t in mesh.loop_triangles], dtype='i4')
    _, _, colors = _vertices(files)
    if (len(colors) != len(mesh.vertices) or not len(tri)
            or not np.array_equal(np.sort(imported, axis=1), np.sort(tri, axis=1))):
        raise ValueError('Caster topology differs from visible asset')
    return dict(red=colors[:,0], tri=tri)


def _vec(data, offset, count=1, integer=False):
    values = struct.unpack_from(f'<{count}'+('i' if integer else 'f'), data, offset)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite face constant')
    return list(values)


def _pixel(files):
    cb0 = capture_import._buffer(files, 'ps-cb0', 3392)
    cb3 = capture_import._buffer(files, 'ps-cb3', 1376)
    post = ['Shallow', 'ShallowFade', 'Shadow', 'Front', 'Sss', 'ShadowFade']
    skin = ['Front', 'Shallow', 'ShallowFade', 'Shadow', 'Sss', 'ShadowFade']
    p = {}
    for i, key in enumerate(['_Post'+s+'Tint' for s in post] + ['_Skin'+s+'Tint' for s in skin]):
        p[key] = _vec(cb0, 48+16*i, 4)
    p.update(_CharacterUISelfShadow=_vec(cb0,396), _IsStencilReceiverPass=_vec(cb0,3140),
             _AvatarMainLightColor=_vec(cb0,3152,4),
             _EntityInfo=_vec(capture_import._buffer(files,'ps-cb2',656),640,4))
    for i,key in enumerate(('_Color','_ShallowColor','_ShallowColor2','_ShallowColor3',
                            '_ShadowColor','_ShadowColor2','_ShadowColor3')):
        p[key] = _vec(cb3,464+16*i,4)
    p.update(_OutlineColor3=_vec(cb3,608,4), _OverrideMainLightBody=_vec(cb3,800,4),
             _OverrideMainLight=_vec(cb3,1168,integer=True))
    for key,offset in (('_AlbedoSmoothness',1180),('_AlbedoSmoothness2',1184),('_AlbedoSmoothness3',1188),
                       ('_UseOverlayTex',1212),('_NoseLineHoriDisp',1224),('_NoseLineLkDnDisp',1228)):
        p[key] = _vec(cb3,offset)
    return p


def _neutral_rgba(path, *, visibility):
    # Inspect every declared mip of a 2D, single-slice RGBA8 neutral texture.
    data = path.read_bytes()
    if (len(data) < 148 or data[:4] != b'DDS ' or data[84:88] != b'DX10'
            or struct.unpack_from('<II', data, 12) != (4, 4)
            or struct.unpack_from('<IIII', data, 128) not in ((28,3,0,1),(29,3,0,1))):
        return False
    levels = max(1, struct.unpack_from('<I', data, 28)[0])
    if levels > 3 or len(data) != 148 + sum(4 * (4 >> level)**2 for level in range(levels)):
        return False
    return all(v == 255 for v in data[148::4]) if visibility else not any(data[148:])


def _verify_neutral_inputs(files, pair):
    if not _neutral_rgba(files['ps-t0.dds'], visibility=True):
        raise ValueError('Face visibility is not neutral at every mip')
    if pair != FACE:
        selection, mode = _vec(capture_import._buffer(files, 'ps-cb3', 1376), 71*16, 2)
        if (selection not in (0.,1.,2.,3.) or mode != 0.
                or not _neutral_rgba(files['ps-t6.dds'], visibility=False)):
            raise ValueError('Active or unverified blush lies outside the shared face calculation')


def _material(files, env, transform, source):
    p = _pixel(files)
    cb = capture_import._buffer(files,'vs-cb2',656)
    packed = _vec(cb,32,4)
    index = env['source']['entity_index']
    if (packed[2] != index or packed[0] != 0 or any(_vec(cb,144,64))
            or p['_UseOverlayTex'] != [0.] or p['_CharacterUISelfShadow'] != [0.]):
        raise ValueError('Face entity, additional lights or overlay outside supported branch')
    records = files['ps-t1.buf'].read_bytes()
    if len(records)%128 or index < 0 or (index+1)*128 > len(records):
        raise ValueError('Incomplete face entity records')
    entity = _vec(records,index*128,32)
    if not np.allclose((transform @ [*entity[4:7],1])[:3], env['entity_position'], atol=1e-5, rtol=0):
        raise ValueError('Face and body refer to different entities')
    middle = _vec(cb,0,4)
    return dict(source=source, pixel=p, vertex={'_MiddlePointPosition':middle}, entity=entity,
                environment=env, middle_world=(transform@[*middle[:3],1])[:3].tolist(),
                override_body_world=(transform[:3,:3]@p['_OverrideMainLightBody'][:3]).tolist(),
                visibility=1., blush_neutral=True, vertex_ambient=[0.,0.,0.])


def _caster_constants(files):
    c0 = capture_import._buffer(files,'vs-cb0',2720)
    c2 = capture_import._buffer(files,'vs-cb2',64)
    c3 = files['vs-cb3.buf'].read_bytes()
    if len(c3) not in (608,656):
        raise ValueError('Unrecognized caster entity layout')
    inputs = dict(_AvatarMainLightPosition=_vec(c0,2672,4),_StencilShadowBlendDebugMode=_vec(c0,2688),
                  _ClipSpaceOffset=_vec(c0,2704,4),_PackedParams0=_vec(c3,16,4),
                  _OverrideHairShadowMainLight=_vec(c2,32,integer=True),_OverrideHairShadowMainLightDir=_vec(c2,48,4))
    for name,offset in (('_Length',0),('_XOffsetNew',4),('_YOffset',8),('_ZOffset',12),('_XOffsetEnter',24),('_YOffsetEnter',28)):
        inputs[name] = _vec(c2,offset)
    return inputs


def _store(kind, arrays):
    import bpy
    from .fbx_support import _write_stream
    digest = hashlib.sha256()
    for key,value in sorted(arrays.items()):
        data = np.ascontiguousarray(value)
        digest.update(str((key,data.dtype.str,data.shape)).encode())
        digest.update(data.tobytes())
    root = Path(bpy.utils.user_resource('DATAFILES',path='ZZZ Outfit Previews',create=True))/'Derived'/'face-v1'
    return _write_stream(root,kind+'-'+digest.hexdigest(),arrays)


def match(folder, draws, features, objects, casters, materials, env, *, shared_four=False):
    if not env or not env.get('registration',{}).get('bone'):
        raise ValueError('No captured head registration')
    if shared_four:
        # An overlap only nominates a candidate. A complete validated mapping
        # is required before it can supply face shading inputs.
        features = dict(features)
        meshes = {o.name:o for o in objects}
        for event, entry in features.items():
            if event not in draws or draws[event][0] not in CORE_FACE_PROGRAMS or entry['status'] != 'unmatched':
                continue
            name = entry.get('candidate_material')
            obj = meshes.get(entry.get('candidate_object'))
            if obj is None or materials.get(name, {}).get('kind') != 'face':
                continue
            slot = list(obj.data['zzz_slots']).index(name)
            try:
                _face_stream(obj, slot, draws[event][1], shared_four=True)
            except ValueError:
                continue
            features[event] = dict(entry, status='verified_corner_remap', object=obj.name, material=name, slot=slot)
    pairs = [(event,row) for event,row in features.items()
             if (row['vs'],row['ps']) in (CORE_FACE_PROGRAMS if shared_four else {FACE}) and row['status'] in ('exact_uv_triangles','verified_corner_remap')
             and materials.get(row.get('material'),{}).get('kind')=='face']
    if len(pairs)!=2 or len(casters)!=1:
        raise ValueError('Expected one matched opaque face, receiver and projection mesh')
    (base,row),(end,last) = sorted(pairs)
    if (row['object'],row['material'],row['slot'])!=(last['object'],last['material'],last['slot']):
        raise ValueError('Receiver belongs to another face mesh')
    middle = [event for event,(pair,_) in draws.items() if base<event<end and pair==CASTER]
    if len(middle)!=1:
        raise ValueError('Ambiguous projection draw')
    cast, = middle
    files,receiver = draws[base][1],draws[end][1]
    pair = draws[base][0]
    if pair != draws[end][0]:
        raise ValueError('Base and receiver use different programs')
    if not shared_four and files['ps-t0.dds'].stat().st_size != 212:
        raise ValueError('Legacy face scope requires a single 4x4 level')
    for bound in ((files, receiver) if shared_four else (files,)):
        _verify_neutral_inputs(bound, pair)
    pixels = _pixel(receiver)
    if pixels.pop('_IsStencilReceiverPass') != [1.]:
        raise ValueError('Later draw is not a stencil receiver')
    original = _pixel(files)
    if original.pop('_IsStencilReceiverPass') != [0.] or original!=pixels:
        raise ValueError('Face draws differ beyond receiver mode')
    # Check current object-to-world and its inverse. Previous-frame matrices
    # can legitimately differ between the base and receiver draw.
    if capture_import._buffer(files,'vs-cb1',464)[:128] != capture_import._buffer(receiver,'vs-cb1',464)[:128]:
        raise ValueError('Receiver head transform differs from the base pass')
    for resource in ('vb1.buf','ib.buf','vs-cb2.buf','ps-t1.buf'):
        if files[resource].read_bytes()!=receiver[resource].read_bytes():
            raise ValueError('Base and receiver bindings differ: '+resource)
    # Capture refers to this exact ordering and stencil reference. The rasterizer
    # reconstructs coverage at the current pose; it never imports a screen mask.
    log = (Path(folder)/'log.txt').read_text(encoding='utf-8',errors='replace')
    for event, ref, count in ((base,128,3*row['triangles']), (cast,132,0), (end,132,3*last['triangles'])):
        if count:
            draw = rf'DrawIndexed\(IndexCount:{count}, StartIndexLocation:0, BaseVertexLocation:0\)'
        else:
            draw = r'DrawIndexedInstanced\(IndexCountPerInstance:(\d+), InstanceCount:1, StartIndexLocation:0, BaseVertexLocation:0, StartInstanceLocation:0\)'
        if not re.search(rf'^{event} {draw}',log,re.M):
            raise ValueError('Unverified base vertex, draw range or instance count')
        if not re.search(rf'^{event} OMSetDepthStencilState\([^\r\n]*StencilRef:{ref}\)',log,re.M):
            raise ValueError('Unsupported or unrecorded stencil state')
    name = row['material']
    if not all(materials[name]['tex'].get(key) for key in ('_MainTex','_LightTex')):
        raise ValueError('Missing face color or light texture')
    meshes = {o.name:o for o in objects}
    face_data = _face_stream(meshes[row['object']],row['slot'],files,shared_four=shared_four)
    hair_data = _caster_stream(meshes[casters[0]],draws[cast][1])
    if not re.search(rf'^{cast} DrawIndexedInstanced\(IndexCountPerInstance:{hair_data["tri"].size},',log,re.M):
        raise ValueError('Incomplete projected triangle range')
    reg = env['registration']
    transform = np.eye(4)
    transform[:3,:3] = BASIS
    transform[:3,3] = np.asarray(reg['blender_point'])-transform[:3,:3]@reg['unity_point']
    source = dict(draw=base, shaders=dict(zip(('vs','ps'),pair)),frame=str(folder))
    material = _material(files,env,transform,source)
    projection = dict(schema='zzz-toon-stencil-coverage/1',source=dict(draw=cast,receiver=end,frame=str(folder)),
                      scope='Current pose, reconstructed depth coverage',caster=casters[0],vs=CASTER[0],ps=CASTER[1],
                      inputs=_caster_constants(draws[cast][1]),unity_to_blender=transform.tolist(),
                      excluded_modifier='ZZZ_CasterOffset',red_path=_store('caster',hair_data))
    result = dict(schema='zzz-toon-native-face/1',scope='Matched original face and caster draws',source=source,
                  bone=reg['bone'],materials={name:material},receiver=projection,
                  mesh_inputs={row['object']:dict(path=_store('face',face_data),source=row['status'])})
    native_face.validate(result)
    return result, dict(base=base,caster=cast,receiver=end,mesh=row['object'],
                        correspondence=row['status'],corners=int(face_data['valid'].sum()),caster_vertices=len(hair_data['red']))
