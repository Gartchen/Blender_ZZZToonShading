"""Runtime inputs for Generic wardrobe builds, matched to visible source geometry."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import struct

import numpy as np

from . import capture_import, native_face_layers, outfit_import, wardrobe_grade


def _triangle_keys(uv):
    # Preserve duplicate triangles, independent of FBX winding or split vertex indices.
    return [tuple(sorted(tuple(point) for point in tri)) for tri in uv.reshape(-1, 3, 2)]


def _eye_draw(files):
    parsed = capture_import._draw_triangles(files)
    if parsed is None:
        raise ValueError('Unknown eye vertex layout')
    indices, uv, active = parsed
    layout = capture_import._header(files['vb1.txt'])
    color = re.search(r'SemanticName: COLOR\s+SemanticIndex: 0\s+Format: (R32G32B32A32_FLOAT|R8G8B8A8_UNORM)\s+InputSlot: 1\s+AlignedByteOffset: (\d+)', layout)
    if not color:
        raise ValueError('Unknown eye COLOR0 layout')
    stride = int(re.search(r'^stride: (\d+)', layout, re.M)[1])
    start = int(color[2])
    if stride <= 0 or start + (4 if color[1] == 'R8G8B8A8_UNORM' else 16) > stride:
        raise ValueError('Invalid stride for eye COLOR0')
    raw = np.fromfile(files['vb1.buf'], 'B').reshape(-1, stride)
    ids = indices[active].ravel()
    if color[1] == 'R8G8B8A8_UNORM':
        red = raw[ids, start]
    else:
        # UIEye VS converts COLOR.r * 255 to an integer (truncation).
        src = raw[:, start:start + 4].copy().view('<f4').ravel()[ids]
        if not np.isfinite(src).all() or np.any((src < 0) | (src > 1)):
            raise ValueError('Invalid eye lookup byte')
        red = (src * np.float32(255)).astype(np.uint8)
    triplets = uv[ids].astype(np.float32).reshape(-1, 3, 2)
    if not np.isfinite(triplets).all():
        raise ValueError('Invalid eye UV')
    keys = _triangle_keys(triplets)
    lookup = defaultdict(set)
    for key, coords, values in zip(keys, triplets, red.reshape(-1, 3)):
        for coord, value in zip(coords, values):
            lookup[key, tuple(coord)].add(int(value))
    if any(len(values) != 1 for values in lookup.values()):
        raise ValueError('Ambiguous eye lookup bytes at shared UV corners')
    return Counter(keys), {k: v.pop() for k, v in lookup.items()}


def _eye_plan(mesh, names, materials, counts, lookup):
    eyes = [i for i, n in enumerate(names) if materials.get(n, {}).get('kind') == 'eye']
    layer = mesh.uv_layers.get('UV0') or mesh.uv_layers.active
    if len(eyes) != 1 or not layer or len(mesh.loops) != len(mesh.polygons)*3:
        return None
    eye = eyes[0]
    info = materials[names[eye]]
    ref = info['params'].get('shader_ref') or {}
    if (not info['tex'].get('_EyeColorMap')
            or ((ref.get('m_PathID') or ref.get('Name')) and not native_face_layers.generic_ui_eye({'generic_casters': []}, info['params']))):
        return None
    uv = np.empty((len(mesh.loops), 2), 'f')
    layer.data.foreach_get('uv', uv.ravel())
    keys = _triangle_keys(uv)
    faces = [i for i, k in enumerate(keys) if k in counts]
    if Counter(keys[i] for i in faces) != counts:
        return None
    old = np.empty(len(mesh.polygons), 'i')
    mesh.polygons.foreach_get('material_index', old)
    allow = {eye, *(i for i,n in enumerate(names) if materials.get(n, {}).get('kind') == 'face')}
    if not set(old[faces]).issubset(allow) or np.count_nonzero(old == eye) != np.count_nonzero(old[faces] == eye):
        return None
    values = np.full(len(mesh.loops), -1., 'f')
    for i in faces:
        for j in range(3*i, 3*i+3):
            values[j] = lookup[keys[i], tuple(uv[j])]
    return faces, eye, values, int(np.count_nonzero(old[faces] != eye))


def restore_eyes(draws, meshes, materials):
    if len(draws) != 1:
        raise ValueError('Expected one wardrobe eye draw')
    event, files = next(iter(draws.items()))
    counts, lookup = _eye_draw(files)
    candidates = []
    for obj in meshes:
        plan = _eye_plan(obj.data, obj.data.get('zzz_slots', ()), materials, counts, lookup)
        if plan:
            candidates.append((obj, plan))
    if len(candidates) != 1:
        raise ValueError('No unique mesh matches every captured eye UV triangle')
    obj, (faces, slot, values, changed) = candidates[0]
    mesh = obj.data
    attr = mesh.attributes.get('zzz_eye_index')
    if attr and (attr.domain != 'CORNER' or attr.data_type != 'FLOAT'):
        raise ValueError('Existing eye index has an incompatible format')
    if attr is None:
        attr = mesh.attributes.new('zzz_eye_index', 'FLOAT', 'CORNER')
    attr.data.foreach_set('value', values)
    for index in faces:
        mesh.polygons[index].material_index = slot
    return dict(draw=event, mesh=obj.name, corners=len(faces)*3, reassigned_faces=changed)


def _partial_fringe(rows, raw_draws, meshes, materials):
    """Authorize coverage only on triangles unique in both the capture and visible FBX."""
    candidates = {(v.get('candidate_object'),v.get('candidate_material')) for v in rows.values()}
    if len(candidates) != 1 or any(v['status'] != 'unmatched' for v in rows.values()):
        return None
    name, mat = candidates.pop()
    info = materials.get(mat, {})
    if info.get('kind') != 'hair' or not info['tex'].get('_OtherDataTex2') or not info.get('params',{}).get('shader_ref'):
        return None
    signatures = []
    shared_with_other_ranges = set()
    for event in rows:
        stream = capture_import._draw_triangles(raw_draws[event][1])
        if stream is None:
            return None
        tri, uv, active = stream
        keys = _triangle_keys(uv[tri].astype('f4'))
        signatures.append(Counter(keys[active]))
        shared_with_other_ranges.update(keys[:active.start])
        shared_with_other_ranges.update(keys[active.stop:])
    if any(sig != signatures[0] for sig in signatures[1:]):
        return None
    captured = signatures[0]
    counts = Counter()
    polygons = {}
    dest = None
    for obj in meshes:
        if obj.type != 'MESH' or not obj.visible_camera:
            continue
        mesh = obj.data
        uv = mesh.uv_layers.get('UV0')
        if uv is None or len(mesh.loops) != 3*len(mesh.polygons):
            continue
        coords = np.empty((len(mesh.loops),2),'f4')
        uv.data.foreach_get('uv',coords.ravel())
        keys = _triangle_keys(coords)
        counts.update(k for k in keys if k in captured)
        if obj.name == name:
            dest = mesh
            slot = list(mesh.get('zzz_slots',())).index(mat)
            polygons = {p.index:k for p,k in zip(mesh.polygons,keys) if p.material_index == slot}
    if dest is None:
        return None
    selected = [i for i,k in polygons.items() if captured[k] == counts[k] == 1 and k not in shared_with_other_ranges]
    if not selected:
        return None
    attribute = 'zzz_verified_fringe'
    attr = dest.attributes.get(attribute)
    if attr and (attr.domain != 'CORNER' or attr.data_type != 'FLOAT'):
        return None
    if attr is None:
        attr = dest.attributes.new(attribute,'FLOAT','CORNER')
    values = np.zeros((len(dest.polygons),3),'f4')
    values[selected] = 1.
    attr.data.foreach_set('value',values.ravel())
    return dict(mesh=name,material=mat,attribute=attribute,verified_faces=len(selected),slot_faces=len(polygons))


def configure(folder, arm, meshes, materials, flags, notes, *, include_overlay=True, shared_four=False):
    measured = {}
    report = {}
    if folder:
        eyes, faces = {}, {}
        raw_features = {} if shared_four else None
        observations = [] if shared_four else None
        measured, matched, warnings = capture_import.read_frame(Path(folder), [arm, *meshes], eye_draws=eyes, face_draws=faces,
                                                               include_overlay=include_overlay, shared_draws=raw_features, slot_evidence=observations)
        if not matched or 'native_globals' not in measured:
            raise ValueError('所选抓帧与可见模型未匹配 / No matching body draw in selected capture')
        notes.extend(warnings)
        report.update(folder=str(folder), body_draws=matched)
        features = measured.pop('capture_features', {})
        try:
            report['eyes'] = restore_eyes(eyes, meshes, materials)
        except (ValueError, KeyError, IndexError, OSError) as exc:
            notes.append('眼部沿用资产输入 / Using FBX eye inputs: ' + str(exc))
        if shared_four and report.get('eyes', {}).get('reassigned_faces'):
            # Compare actual material slots after recovering the native eye triangles.
            features = capture_import.capture_space.match_features(raw_features, meshes, capture_import._draw_triangles)
        fringe = [v for v in features.values() if v['kind'] == 'fringe']
        hair_programs = native_face_layers.HAIR_COVERAGE_PS if shared_four else {'050d63a297594dff'}
        head = measured.get('native_environment', {}).get('registration', {}).get('bone')
        has_eye = any(v['kind'] == 'eye' and v['status'] == 'exact_uv_triangles'
                      and materials.get(v['material'],{}).get('kind') == 'eye' for v in features.values())
        eligible = head and head in arm.data.bones and has_eye and fringe and all(v['ps'] in hair_programs for v in fringe)
        if eligible:
            fringe_rows = {e:v for e,v in features.items() if v['kind'] == 'fringe'}
            bound = all(v['status'] == 'exact_uv_triangles' and materials.get(v.get('material'),{}).get('kind') == 'hair'
                        and materials[v['material']]['tex'].get('_OtherDataTex2') for v in fringe)
            partial = _partial_fringe(fringe_rows, raw_features, meshes, materials) if shared_four and not bound else None
            if bound or partial:
                hair_materials = [partial['material']] if partial else sorted({v['material'] for v in fringe})
                profile = dict(schema='zzz-toon-face-layers/1', bone=head,
                               source={'frame':str(folder),'draws':list(fringe_rows),
                                       'pixel_programs':sorted({v['ps'] for v in fringe})}, scope='Matched wardrobe draws',
                               eye_ps='720a454f81097a64', hair_ps=fringe[0]['ps'], inverse_head_row=1,
                               composite='premultiplied_features_v1', hair_materials=hair_materials)
                if partial:
                    profile['coverage_attribute'] = partial['attribute']
                    report['partial_fringe'] = partial
                measured['native_face_layers'] = profile
        if shared_four:
            report['feature_draws'] = features
            report['slot_evidence'] = observations
        from . import capture_face
        try:
            measured['native_face'], report['face'] = capture_face.match(
                folder, faces, features, meshes, flags['generic_casters'], materials, measured.get('native_environment'), shared_four=shared_four)
        except (ValueError, KeyError, IndexError, OSError, struct.error) as exc:
            notes.append('脸部与刘海投影沿用原路径 / Face and projection fallback: ' + str(exc))
        if 'native_light_profile' not in measured:
            flags.pop('native_light_profile', None)
        flags.update(measured)
        report['measured'] = sorted(measured)
        notes.append('已匹配抓帧绘制 / Matched body draws: ' + ', '.join(matched))
    if not folder:
        # All three outfit captures agree on the palette, ambient and Bloom. Point and
        # gradient positions vary slightly; the user-facing report labels the studio estimate.
        common = json.loads((Path(__file__).with_name('preview_data')/'index.json').read_bytes())['common']
        for key in ('native_globals', 'native_bloom', 'native_character_ambient'):
            flags[key] = common[key]
        origin = np.array(arm.matrix_world.translation)
        gradient = np.array([-.4, .3, 0.])
        flags['native_environment'] = dict(schema='zzz-toon-environment/1',
            source='Three-outfit studio approximation', scope='Asset-only wardrobe preview',
            main_color=[1., 1., 1.], entity_color=[1., 1., 1.], entity_range=50.,
            entity_position=(origin + [20., -15., 1.]).tolist(),
            gradient_enabled=True, gradient_direction=gradient.tolist(),
            gradient_offset=float(-.5 + gradient @ origin), gradient_color=[.702, .779, .839, .2],
            ambient_color=common['native_character_ambient'])
        if 'native_light_profile' not in flags:
            # The directions vary by outfit. The shared studio directions are a
            # preview approximation until a SkinPreviewPage or a matching draw is supplied.
            body = np.array([.6, .45, -.65]); hair = np.array([.58, -.10, -.8])
            flags['native_light_profile'] = outfit_import.light_profile(
                body/np.linalg.norm(body), hair/np.linalg.norm(hair), 'Three-outfit studio approximation',
                'Asset-only wardrobe preview')
            report['part_light'] = 'studio approximation'
        else:
            report['part_light'] = 'outfit page'
        report['environment'] = 'studio approximation'
        notes.append('共用服装页环境近似 / Shared wardrobe studio approximation')
    else:
        report['part_light'] = 'captured' if 'native_light_profile' in measured else 'scene key'
        report['environment'] = 'captured' if 'native_environment' in measured else 'unregistered'
    if 'native_character_lut' not in flags:
        flags['native_character_lut'] = dict(schema='zzz-toon-character-lut/1', enabled=True, size=32,
            exposure=1., layout='linear-strip-dds-row0-at-uv0', source_format=wardrobe_grade.MODEL,
            source='Analytic fit to measured wardrobe LUT', scope='Approximate wardrobe preview')
        notes.append('服装页颜色使用共用近似曲线 / Fitted wardrobe color response')
        report['grade'] = 'shared wardrobe approximation'
    else:
        report['grade'] = 'captured PS26 LUT'
    return report
