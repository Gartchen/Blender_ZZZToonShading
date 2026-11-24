"""Build a wardrobe scene from explicit FBX slot bindings and local assets."""
import hashlib
import json
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from . import anim, assets, character, clip_io, common_parts, generic_assets as slots, outfit_import, preview, wardrobe_page


def neutral_material():
    # These are editable preview defaults for an unknown material, in linear RGB.
    colors = {}
    for suffix in ('', '2', '3', '4', '5'):
        for name, rgb in (('_ShadowColor', (.62, .65, .72)), ('_ShallowColor', (.86, .87, .91)),
                          ('_OutlineColor', (.22, .22, .24)), ('_SpecularColor', (.45, .45, .45))):
            colors[name + suffix] = dict(zip('rgba', (*rgb, 1.)))
    return {'zzz_color_encoding': 'linear', 'm_SavedProperties': {
            'm_TexEnvs': {}, 'm_Colors': colors, 'm_Floats': {}}}


def material_inputs(row, files, images, notes, bound_files=None, part=None, reads=None, four=False):
    name = row['name']
    native = row['material']
    if native:
        data, declared = assets.load_material(native)
    else:
        data, declared = neutral_material(), None
        notes.append(name + ' · 近似参数 / Preview defaults')
    kind = row['kind'] if row['kind'] != 'auto' else declared or slots.infer(name)
    if part:
        kind = 'body' if part in ('body1','body2') else part
    if kind == 'caster':
        return None
    props = data['m_SavedProperties']
    env = dict(props['m_TexEnvs'])
    floats = props['m_Floats'] = dict(props['m_Floats'])
    if reads is not None:
        from .slot_inspector import Observed
        props['m_Colors'] = Observed(dict(props['m_Colors']), reads['colors'])
        props['m_Floats'] = floats = Observed(floats, reads['floats'])
    mapped = {}
    base_stem = Path(row['base_hint'] or row['base']).stem
    for key, (shader_key, _) in slots.CHANNELS.items():
        if part and key not in common_parts.CHANNELS[part]:
            continue
        tex = env.get(shader_key, {}).get('m_Texture', {})
        ref = tex.get('Name') if not tex.get('IsNull', True) else ''
        file = row[key]
        if file and not Path(file).is_file():
            raise ValueError(name + ' / ' + key + ' · 找不到 / Missing: ' + file)
        if not file:
            file = slots.auto_file(key, kind, native, ref, base_stem, files)
        if not file:
            if key == 'base':
                raise ValueError(name + ' · 请填底色 / Assign base color')
            if ref:
                notes.append(name + ' / ' + shader_key + ' · 缺图 / Missing: ' + ref)
            continue
        # The core uses a logical PNG key; the file it loads may have any supported name or extension.
        token = Path(file).stem[:28] + '_' + hashlib.sha256(file.encode('utf8')).hexdigest()[:16]
        images[token + '.png'] = file
        if bound_files is not None:
            bound_files[shader_key] = file
        mapped[shader_key] = {'m_Texture': {'Name': token, 'IsNull': False}}
        if key.startswith('cap') and not native:
            floats['_MatCap'] = 1.
    props['m_TexEnvs'] = {**{k:v for k,v in env.items() if k not in {ch[0] for ch in slots.CHANNELS.values()}}, **mapped}
    if part:
        props['m_TexEnvs'] = mapped
        floats['_UseOverlayTex'] = 0.
        floats['_Emission'] = 0.
    if four and part in ('body1','body2'):
        # Both body categories use the opaque PS1119 pass.
        floats['_DstBlend'] = 0.
    if kind == 'cloth':
        if row['kind'] == 'cloth' or not native:
            floats.update(_DstBlend=10., _SrcBlend=1., _Surface=0.)
        kind = 'body'
    elif not part and kind == 'body' and row['kind'] == 'body':
        floats['_DstBlend'] = 0.
    return character.material_info(data, kind)


# Material families observed in wardrobe exports. One family can serve multiple parts.
def family_matches(ref, kind, blend):
    from . import native_emission, native_face_layers
    if native_emission.avatar_ui(ref):
        return kind in {'body', 'wing', 'hair'}
    if native_face_layers.generic_ui_eye({'generic_casters': []}, {'shader_ref': ref}):
        return kind == 'eye'
    if not ref.get('Name'):
        if ref.get('m_PathID') == 2497034685900247571:
            return kind in {'face', 'brow'}
        if ref.get('m_PathID') == -97646000100089233:
            return kind in {'body', 'wing'} and blend
    return False


def import_report(rows, materials, bound_files, warnings, build, flags):
    result = {}
    for row in rows:
        name = row['name']
        info = materials[name]
        actual = build['materials'].get(name)
        ref = info['params'].get('shader_ref') or {}
        issues = list(warnings[name])
        for warning in build.get('material_warnings', []):
            prefix, _, msg = warning.partition(': ')
            if prefix == name or prefix.endswith(' / ' + name):
                issues.append(msg)
        kind = actual['kind'] if actual else info['kind']
        blend = bool(actual and actual['blend'])
        matched = family_matches(ref, kind, blend)
        if not actual:
            issues.append('未生成此材质 / Material was skipped')
        if row['material'] and not matched:
            issues.append('原始着色器与所选分支的对应关系未验证 / Unverified shader-to-branch match')
        if actual and actual.get('opacity_source') == 'legacy_constant':
            issues.append('透明度取预览常数 / Constant opacity')
        if kind == 'face' and name not in (flags.get('native_face') or {}).get('materials', {}):
            issues.append('脸部使用通用明暗 / Generic face shading')
        if kind == 'eye':
            if not info['tex'].get('_EyeColorMap'):
                issues.append('缺少眼睛查色表 / No eye lookup texture')
            elif not flags.get('native_environment') or flags['native_environment'].get('source') == 'Three-outfit studio approximation':
                issues.append('眼部使用预览光照 / Preview eye lighting')
        if kind in {'hair', 'brow'} and not flags.get('native_face_layers'):
            issues.append('眉睫与头发使用通用遮挡 / Generic brow and hair coverage')
        if blend and kind in {'body', 'wing'}:
            issues.append('透明层以 Blender 覆盖混合 / Blender coverage resolve')
        status = ('skipped' if not actual else 'fallback' if not row['material'] or not matched
                  else 'partial' if issues else 'matched')
        result[name] = dict(status=status, family_matched=matched, kind=kind, blend=blend,
                            shader_name=ref.get('Name') or '', shader_id=str(ref.get('m_PathID') or ''),
                            material_file=row['material'], type_selection=row['kind'],
                            texture_files=bound_files[name], details=issues)
    return dict(schema=1, scope='visible surfaces', materials=result,
                page_light=bool(flags.get('native_light_profile')),
                entity_environment=bool(flags.get('native_environment')))


def camera_limits(scene, arm, meshes, materials):
    from .wardrobe import bounds
    lo, hi = (Vector(v) for v in bounds(scene, meshes))
    center = (lo + hi)*.5
    body_height = hi.z - lo.z
    head = next((b for b in arm.pose.bones if b.name.casefold() in ('bip001 head','head','j_bip_c_head')), None)
    face = arm.matrix_world @ head.head if head else Vector((center.x, center.y, lo.z + .87*body_height))
    proportion = max(.1, face.z - lo.z)
    aspect = scene.render.resolution_x / scene.render.resolution_y
    def stop(x, y, z):
        matrix = Matrix.Rotation(1.5707963267948966, 4, 'X')
        matrix.translation = (x, y, z)
        return dict(world=[list(row) for row in matrix], lens=50., shift_x=0., shift_y=0.)
    far_d = max(hi.x-lo.x, body_height*aspect)*1.08*50/36
    near_d = proportion*.39*aspect*50/36
    near_y = face.y - near_d
    near_z = face.z - .06*proportion
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        if not any(materials.get(key, {}).get('kind') in ('face', 'hair') for key in obj.data.get('zzz_slots', ())):
            continue
        evaluated = obj.evaluated_get(dg)
        points = [evaluated.matrix_world @ Vector(p) for p in evaluated.bound_box]
        for pt in points:
            # Keep the top of the hair inside the close stop, including a small margin.
            if pt.z < face.z + proportion*.38:
                near_z = max(near_z, pt.z - (pt.y-near_y)*36/50/aspect*.46)
    return dict(far=stop(center.x, lo.y-far_d, center.z),
                near=stop(face.x, near_y, near_z),
                bounds=[list(lo), list(hi)], focus_axis=[face.x, face.y])


def build(scene, req):
    from . import wardrobe
    source = Path(req['model'])
    if preview.digest(source) != req['fingerprint']:
        raise ValueError('模型已更改，请重新读取 / FBX changed; read its slots again')
    character.setup_scene(scene)
    objs = character.import_fbx(str(source), req['uv'] or None, per_mesh_binds=True)
    arm, = [obj for obj in objs if obj.type == 'ARMATURE']
    meshes = {obj.name:obj for obj in objs if obj.type == 'MESH'}
    if meshes.keys() != req['meshes'].keys():
        raise ValueError('模型部件已更改 / FBX mesh list changed')
    shown, used, caster_meshes = [], set(), []
    casters = {r['name'] for r in req['materials'] if r['kind'] == 'caster' or
               (r['kind'] == 'auto' and slots.infer(r['name']) == 'caster')}
    parts = {r['name']:common_parts.part(r['name'], r.get('part','auto')) for r in req['materials']} if req.get('common_six') else {}
    if parts:
        casters = {n for n,p in parts.items() if p == 'caster'}
    for name, obj in meshes.items():
        info = req['meshes'][name]
        names = slots.mesh_slots(obj)
        if info['slots'] != names:
            raise ValueError('材质槽已更改 / FBX material slots changed: ' + name)
        excluded = [i for i,n in enumerate(names) if parts.get(n)=='exclude']
        if not info['visible'] or (excluded and len(excluded)==len(names)):
            obj.hide_render = obj.hide_viewport = True
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        if not obj.material_slots:
            obj.data.materials.append(bpy.data.materials.new(names[0]))
        for index, slot in enumerate(obj.material_slots):
            if slot.link == 'OBJECT' or not slot.material:
                mat = slot.material or bpy.data.materials.new(names[index])
                slot.link = 'DATA'
                slot.material = mat
        if excluded:
            common_parts.mask_slots(obj, excluded)
            # Eevee may still compile the original FBX shader for an empty slot.
            placeholder = bpy.data.materials.get('ZZZ · Excluded surface') or bpy.data.materials.new('ZZZ · Excluded surface')
            for index in excluded:
                obj.data.materials[index] = placeholder
        if casters.intersection(names):
            if set(names) - casters:
                raise ValueError(name + ' · 投影与可见材质须分开网格 / Separate caster and visible geometry')
            caster_meshes.append(name)
            obj.visible_camera = False
        obj.data['zzz_slots'] = names
        used.update(n for n in names if parts.get(n)!='exclude')
        shown.append(obj)
    if not any(obj.visible_camera for obj in shown):
        raise ValueError('至少显示一个角色部件 / Show at least one character mesh')
    notes, images, materials = [], {}, {}
    rows = [r for r in req['materials'] if r['name'] in used and r['name'] not in casters]
    extras = [row[key] for row in rows
              for key in ('material', *(common_parts.CHANNELS[parts[row['name']]] if parts else slots.CHANNELS))
              if row[key]]
    files = slots.index_files(source, extras)
    warnings, bound_files, reads = {}, {}, {}
    for row in rows:
        name = row['name']
        warnings[name], bound_files[name] = [], {}
        if req.get('common_four'):
            reads[name] = dict(colors=set(), floats=set())
        materials[name] = material_inputs(row, files, images, warnings[name], bound_files[name], parts.get(name),
                                          reads.get(name), bool(req.get('common_four')))
        notes.extend(warnings[name])
    flags = dict(wardrobe_texture_files=images, generic_casters=caster_meshes,
                 game_light=(1.,1.,1.,1.), key_world=(.5,-.65,.55))
    if req.get('common_four'):
        flags['four_intersection'] = True
    page_file = Path(req['page']) if req['page'] else outfit_import.discover_page(source)
    if page_file:
        light, status = outfit_import.page_light(page_file, req['skin_id'])
        if light:
            flags['native_light_profile'] = light
        notes.append(status)
    from . import generic_capture
    runtime = generic_capture.configure(req.get('capture'), arm, shown, materials, flags, notes,
                                        include_overlay=not bool(parts), shared_four=bool(req.get('common_four')))
    if 'native_light_profile' not in flags:
        notes.append('预览用主光 / Approximate preview lighting')
    clip = clip_io.load(Path(req['clip']))
    tracks, shapes = anim.apply_clip(arm, clip, action_name=Path(req['clip']).stem)
    if not tracks:
        raise ValueError('待机动画没有绑定到所选模型的骨架')
    scene.render.fps, scene.render.fps_base = 60, 1
    scene.frame_start, scene.frame_end = 1, max(1, round(clip['stop']*60))
    scene.frame_set(1)
    scene.sync_mode = 'FRAME_DROP'
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 100
    scene.render.film_transparent = True
    preview.camera(scene, meshes=[obj for obj in shown if obj.visible_camera])
    report = character.setup_character([arm, *shown], '', '', 'GAME', flags, scene=scene, hide=(),
                                       material_bindings=materials, skin_smooth=False)
    character.setup_eye_pass(scene, shown)
    character.setup_compositor(scene)
    notes.extend(report.get('material_warnings', []))
    if len(caster_meshes) > 1:
        notes.append('多组投影网格，只使用第一组 / Only the first shadow caster is used')
    # Bounds sample the animation at multiple times; the receiver is a screen
    # image and has no effect on geometry or camera stops.
    from . import stencil_runtime
    receiver = scene.pop(stencil_runtime.KEY, None)
    try:
        limits = camera_limits(scene, arm, shown, materials)
    finally:
        if receiver is not None:
            scene[stencil_runtime.KEY] = receiver
    cam = scene.camera
    cam.data.sensor_fit, cam.data.sensor_width = 'HORIZONTAL', 36
    cam[wardrobe.KEY] = json.dumps(limits)
    settings = scene.zzz_toon
    slots.restore(settings, req)
    settings.wardrobe_zoom = 0.
    scene.name = source.stem + ' · Wardrobe'
    page = json.loads((wardrobe.DATA/'page.json').read_text(encoding='utf8'))
    wardrobe_page.attach(scene, wardrobe.DATA, page, slots.ROLE, Path(req['background']) if req['background'] else None)
    diagnostics = import_report(rows, materials, bound_files, warnings, report, flags)
    diagnostics['page_light'] = runtime.get('part_light') == 'outfit page'
    diagnostics['entity_environment'] = runtime.get('environment') == 'captured'
    if parts:
        diagnostics['scope'] = 'common_six'
        diagnostics['shared_four'] = bool(req.get('common_four'))
        diagnostics['excluded_materials'] = sorted(n for n,p in parts.items() if p=='exclude')
        for n,entry in diagnostics['materials'].items():
            entry['part'] = parts[n]
            partial = runtime.get('partial_fringe', {})
            if partial.get('material') == n:
                entry['details'].append(f"已验证刘海面 / Verified fringe faces: {partial['verified_faces']}/{partial['slot_faces']}")
            if parts[n] in ('body1', 'body2') and 'native_surface' not in flags:
                entry['details'].append('变形法线与材质采样待核对 / Deformed normals and sampling unverified')
            if parts[n] == 'eye' and not runtime.get('eyes'):
                entry['details'].append('眼部缺少抓帧逐角索引验证 / Eye corner indices unverified')
            if entry['details'] and entry['status'] == 'matched':
                entry['status'] = 'partial'
    diagnostics['runtime'] = runtime
    diagnostics['mesh_binds'] = json.loads(arm['zzz_import_report']).get('mesh_binds', {})
    if req.get('common_four'):
        from . import slot_inspector
        for source_name, obj in meshes.items():
            obj[slot_inspector.SOURCE] = source_name
        all_paths = [row[k] for row in req['materials'] for k in ('material', *slots.CHANNELS) if row[k]]
        inventory = slot_inspector.completed(req, slots.index_files(source, all_paths), meshes, materials, runtime, flags, reads)
        scene[slot_inspector.KEY] = json.dumps(inventory, ensure_ascii=False)
        scene['zzz_slot_model'] = req['fingerprint']
    scene['zzz_generic_material_report'] = json.dumps(diagnostics, ensure_ascii=False)
    scene['zzz_generic_notes'] = json.dumps(notes, ensure_ascii=False)
    scene['zzz_wardrobe'] = json.dumps(dict(role=slots.ROLE, clip=req['clip'], bones=tracks, shape_curves=shapes,
                                             frames=scene.frame_end, camera=limits, materials=list(materials)))
    if receiver is not None:
        stencil_runtime._handler(scene)
    wardrobe.setup_view()
