"""Input slots generated from the user's FBX, independent of the outfit catalog."""
import json
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from . import assets, character, common_parts, preview, slot_inspector

ROLE = 'Generic'
CHANNELS = {
    'base': ('_MainTex', '底色 / Base'),
    'normal': ('_LightTex', '法线或脸部光照图 / N or face SDF'),
    'mask': ('_OtherDataTex', '材质遮罩 / M'),
    'aux': ('_OtherDataTex2', '辅助通道 / A'),
    'eye': ('_EyeColorMap', '眼睛查色表 / Eye LUT'),
    **{'cap' + str(i): ('_MatCapTex' + (str(i) if i > 1 else ''), 'MatCap ' + str(i))
       for i in range(1, 6)},
}
KINDS = [('auto', '自动 / Auto'), ('body', '身体或配件 / Body or accessory'),
         ('cloth', '透明布 / Sheer cloth'), ('wing', '羽翼 / Wing'), ('hair', '头发 / Hair'), ('face', '脸 / Face'),
         ('eye', '眼睛 / Eyes'), ('brow', '眉睫 / Brows'), ('caster', '投影网格 / Shadow caster')]
FILES = ('model', 'clip', 'uv', 'page', 'background')
IMAGE_EXT = {'.png', '.tga', '.tif', '.tiff', '.jpg', '.jpeg', '.exr', '.dds', '.bmp', '.webp'}


class ZZZ_GenericMaterial(bpy.types.PropertyGroup):
    part: EnumProperty(items=common_parts.CHOICES)
    kind: EnumProperty(items=[(key, title, '') for key, title in KINDS])
    material: StringProperty(subtype='FILE_PATH')
    base_hint: StringProperty()
    expanded: BoolProperty(default=False)
    for key in CHANNELS:
        __annotations__[key] = StringProperty(subtype='FILE_PATH')


class ZZZ_GenericMesh(bpy.types.PropertyGroup):
    visible: BoolProperty(default=True)
    slots: StringProperty()


class ZZZ_GenericInputs(bpy.types.PropertyGroup):
    __annotations__ = {}
    for key in FILES:
        __annotations__[key] = StringProperty(subtype='FILE_PATH')
    capture: StringProperty(subtype='DIR_PATH')
    skin_id: StringProperty()
    fingerprint: StringProperty()
    inspection_rows: CollectionProperty(type=slot_inspector.ZZZ_InspectRow)
    inspection_index: IntProperty(min=0)
    inspection_files: StringProperty()
    inspection_source: BoolProperty(default=False)
    scope_version: IntProperty(default=0, options={'HIDDEN'})
    materials: CollectionProperty(type=ZZZ_GenericMaterial)
    meshes: CollectionProperty(type=ZZZ_GenericMesh)


def absolute(value):
    return str(Path(bpy.path.abspath(value)).resolve()) if value else ''


def mesh_slots(obj):
    missing = '[' + obj.name + ' / unassigned]'
    return [s.material.name if s.material else missing for s in obj.material_slots] or [missing]


def inspect_model(model, geometry=None):
    objects = character.import_fbx(model, repair_exports=False)
    if sum(obj.type == 'ARMATURE' for obj in objects) != 1:
        raise ValueError('需要一个绑定骨架 / Expected one armature')
    mats, meshes = {}, {}
    for obj in objects:
        if obj.type != 'MESH':
            continue
        names = mesh_slots(obj)
        meshes[obj.name] = names
        if not obj.material_slots or any(not slot.material for slot in obj.material_slots):
            mats['[' + obj.name + ' / unassigned]'] = ''
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or mat.name in mats:
                continue
            images = set()
            for node in mat.node_tree.nodes if mat.node_tree else ():
                if node.type == 'TEX_IMAGE' and node.image and any(
                        link.to_socket.name == 'Base Color' for link in node.outputs['Color'].links):
                    images.add(Path(node.image.filepath.replace('\\', '/')).name or node.image.name)
            mats[mat.name] = next(iter(images)) if len(images) == 1 else ''
    if not meshes or not mats:
        raise ValueError('模型没有可用的材质槽 / No mesh material slots')
    if geometry is not None:
        slot_inspector.cache_geometry(objects, geometry)
    return dict(fingerprint=preview.digest(Path(model)), meshes=meshes, materials=mats)


def index_files(model, extra=()):
    """Only look in adjacent export directories. Ambiguous filenames stay unassigned."""
    parent = Path(model).parent
    roots = [parent, parent/'Textures', parent/'Materials']
    if parent.name.lower() in ('01_model', 'model', 'fbx'):
        roots.extend([parent.parent/'Textures', parent.parent/'Materials'])
        roots.extend(d for d in parent.parent.iterdir() if re.match(r'^\d\d_', d.name))
    for path in extra:
        folder = Path(path).parent
        roots.extend((folder, folder.parent/'Textures'))
    files = {}
    for folder in dict.fromkeys(roots):
        if folder.is_dir():
            for f in folder.iterdir():
                ext = f.suffix.lower()
                if (ext in IMAGE_EXT or ext == '.json') and f.is_file():
                    files.setdefault((f.stem.casefold(), ext == '.json'), set()).add(str(f.resolve()))
    return files


def find(files, name, material=False):
    hits = files.get((name.casefold(), material), ())
    return next(iter(hits)) if len(hits) == 1 else ''


def auto_file(key, kind, native, ref, base, files):
    if ref:
        return find(files, ref)
    if key == 'base':
        return find(files, base)
    if native:
        return ''
    if kind not in ('face', 'eye', 'brow') and base.upper().endswith('_D') and key in ('normal','mask','aux'):
        return find(files, base[:-1] + {'normal':'N','mask':'M','aux':'A'}[key])
    if key == 'normal' and kind == 'face':
        hits = [path for (stem, is_json), paths in files.items() if not is_json and
                re.search(r'(?:^|_)face_lightmap(?:_\d+)?$', stem) for path in paths]
        return hits[0] if len(hits) == 1 else ''
    return find(files, 'Eye_E') if key == 'eye' and kind == 'eye' else ''


@lru_cache(maxsize=128)
def suggestions(model, native, kind, name, hint, overrides):
    files = index_files(model, [p for p in (native, *overrides) if p])
    env, declared = {}, None
    if native:
        data, declared = assets.load_material(native)
        env = dict(data['m_SavedProperties']['m_TexEnvs'])
    if kind == 'auto':
        kind = declared or infer(name)
    base = Path(hint or overrides[0]).stem
    result = {}
    for key, (shader_key, _) in CHANNELS.items():
        tex = env.get(shader_key, {}).get('m_Texture', {})
        ref = tex.get('Name') if not tex.get('IsNull', True) else ''
        file = auto_file(key, kind, native, ref, base, files)
        result[key] = Path(file).name if file else ref or ''
    return kind, result


def infer(name):
    if 'hairshadow' in name.lower().replace('_', ''):
        return 'caster'
    if 'wing' in name.casefold():
        return 'wing'
    if 'lash' in name.casefold() or 'brow' in name.casefold():
        return 'brow'
    kind = character.kind_of(name)
    if kind == 'body' and re.search(r'_T(?:_|$)', name, re.I):
        return 'cloth'
    return kind


def apply_scan(inputs, result, files):
    same = inputs.fingerprint == result['fingerprint']
    old = {r.name: material_record(r) for r in inputs.materials} if same else {}
    visible = {r.name: r.visible for r in inputs.meshes} if same else {}
    inputs.materials.clear()
    inputs.meshes.clear()
    for name, hint in sorted(result['materials'].items()):
        row = inputs.materials.add()
        row.name, row.base_hint = name, hint
        if name in old:
            for k, value in old[name].items():
                setattr(row, k, value)
        else:
            stem = re.sub(r'\.\d{3}$', '', name)
            row.material = find(files, name, True) or find(files, stem, True)
    for name, slots in sorted(result['meshes'].items()):
        row = inputs.meshes.add()
        row.name, row.slots, row.visible = name, json.dumps(slots, ensure_ascii=False), visible.get(name, True)
    inputs.fingerprint = result['fingerprint']
    inputs.scope_version = 2
    inputs.inspection_source = True
    slot_inspector.populate(inputs, result['meshes'])
    inputs.inspection_files = json.dumps(slot_inspector.preflight(dict(materials=[material_record(r) for r in inputs.materials]), files), ensure_ascii=False)


def material_record(row):
    return dict(name=row.name, kind=row.kind, part=row.part, base_hint=row.base_hint, material=absolute(row.material),
                **{key: absolute(getattr(row, key)) for key in CHANNELS})


class ZZZ_OT_scan_generic(bpy.types.Operator):
    bl_idname = 'zzz_toon.scan_generic'
    bl_label = '读取模型槽位 / Read FBX slots'

    def execute(self, context):
        data = context.scene.zzz_toon.assets_generic
        model = Path(absolute(data.model))
        try:
            if not model.is_file() or model.suffix.lower() != '.fbx':
                raise ValueError('请选择 FBX / Select an FBX')
            with tempfile.TemporaryDirectory(prefix='zzz-scan-') as tmp:
                task = Path(tmp)/'scan.json'
                task.write_text(json.dumps({'scan_model': str(model), 'scan_geometry': str(Path(tmp)/'rest.npz')}), encoding='utf8')
                proc = subprocess.run([bpy.app.binary_path, '-b', '--factory-startup', '--python-exit-code', '1',
                                       '-P', str(Path(__file__).with_name('wardrobe_worker.py')), '--', str(task)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if proc.returncode:
                    err = task.with_suffix('.error')
                    raise ValueError(err.read_text(encoding='utf8') if err.exists() else proc.stdout[-500:].decode(errors='replace'))
                result = json.loads(task.with_suffix('.result').read_text(encoding='utf8'))
                import shutil
                shutil.move(str(Path(tmp)/'rest.npz'), str(slot_inspector.geometry_path(result['fingerprint'])))
            suggestions.cache_clear()
            apply_scan(data, result, index_files(model))
        except (ValueError, OSError, RuntimeError) as err:
            self.report({'ERROR'}, str(err))
            return {'CANCELLED'}
        return {'FINISHED'}


def request(settings):
    data = settings.assets_generic
    files = {key: absolute(getattr(data, key)) for key in FILES}
    for key, value in files.items():
        if (key in ('model', 'clip') or value) and not Path(value).is_file():
            raise ValueError('无效文件 / Missing file: ' + key)
    if not data.fingerprint or preview.digest(Path(files['model'])) != data.fingerprint:
        raise ValueError('请先重新读取模型槽位 / Read the current FBX slots first')
    if data.scope_version < 2:
        raise ValueError('请重新读取模型以使用四人交集 / Rescan this FBX for the four-outfit intersection')
    if Path(files['clip']).suffix.lower() not in ('.anim', '.json'):
        raise ValueError('预览待机动画槽需要 Unity .anim 或 AnimationClip JSON')
    capture = absolute(data.capture)
    if capture and not Path(capture).is_dir():
        raise ValueError('找不到抓帧文件夹 / FrameAnalysis directory missing')
    meshes = {row.name: dict(slots=json.loads(row.slots), visible=row.visible) for row in data.meshes}
    if not any(row['visible'] for row in meshes.values()):
        raise ValueError('至少勾选一个部件 / Show at least one mesh')
    return dict(role=ROLE, language=settings.wardrobe_language, fingerprint=data.fingerprint,
                common_four=data.scope_version >= 2, common_six=bool(data.scope_version), capture=capture, skin_id=data.skin_id, materials=[material_record(row) for row in data.materials], meshes=meshes, **files)


def restore(settings, req):
    settings.wardrobe_language = req['language']
    settings.wardrobe_role = settings.wardrobe_selection = ROLE
    data = settings.assets_generic
    data.scope_version = 2 if req.get('common_four') else int(bool(req.get('common_six')))
    data.capture = req.get('capture', '')
    for key in (*FILES, 'fingerprint', 'skin_id'):
        setattr(data, key, req[key] or '')
    for params in req['materials']:
        row = data.materials.add()
        for key, val in params.items():
            setattr(row, key, val)
    data.inspection_source = False
    slot_inspector.populate(data, {n: info['slots'] for n,info in req['meshes'].items()})
    for name, info in req['meshes'].items():
        row = data.meshes.add()
        row.name, row.slots, row.visible = name, json.dumps(info['slots'], ensure_ascii=False), info['visible']


def draw(layout, settings):
    data = settings.assets_generic
    en = settings.wardrobe_language == 'EN'
    for key, label in [('model', 'FBX'), ('clip', 'Idle' if en else '待机动画')]:
        layout.prop(data, key, text=label)
    layout.operator('zzz_toon.scan_generic', text='Read FBX slots' if en else '读取模型槽位', icon='FILE_REFRESH')
    if data.fingerprint:
        slot_inspector.draw(layout, settings)
        if data.scope_version >= 2:
            layout.label(text='Shared wardrobe shading · 4 outfits' if en else '四套服装共用着色')
        if data.scope_version:
            layout.label(text='Face, eyes, brows, hair, Body 1 and 2' if en else '六类共用部件：脸、眼睛、眉睫、头发、身体一和二')
        for row in data.materials:
            box = layout.box()
            box.prop(row, 'expanded', text=row.name, icon='TRIA_DOWN' if row.expanded else 'TRIA_RIGHT', emboss=False)
            if row.expanded:
                shared = common_parts.part(row.name, row.part) if data.scope_version else None
                if shared:
                    box.prop(row, 'part', text='Shared part' if en else '共用部件')
                    box.label(text=dict((k,title) for k,title,_ in common_parts.CHOICES)[shared])
                    if shared in ('exclude','caster'):
                        continue
                else:
                    box.prop(row, 'kind', text='Type' if en else '材质类型')
                box.prop(row, 'material', text='Material JSON' if en else '原始材质 JSON')
                if not shared and (row.kind == 'caster' or (row.kind == 'auto' and infer(row.name) == 'caster')):
                    continue
                try:
                    selected_kind = ('body' if shared in ('body1', 'body2') else shared) or row.kind
                    kind, hints = suggestions(absolute(data.model), absolute(row.material), selected_kind, row.name,
                                              row.base_hint, tuple(absolute(getattr(row,k)) for k in CHANNELS))
                except (OSError, ValueError, KeyError, TypeError) as err:
                    box.label(text=str(err), icon='ERROR')
                    kind, hints = row.kind, {}
                if not shared and row.kind == 'auto':
                    box.label(text=('Suggested type: ' if en else '建议类型：') + dict(KINDS).get(kind, kind))
                for key, (_, label) in CHANNELS.items():
                    if shared and key not in common_parts.CHANNELS[shared]:
                        continue
                    box.prop(row, key, text=label, placeholder=hints.get(key) or ('Choose file' if en else '选择文件'))
        header, body = layout.panel('zzz_generic_meshes', default_closed=True)
        header.label(text='Visible meshes' if en else '显示部件')
        if body:
            for row in data.meshes:
                line = body.column(align=True)
                line.prop(row, 'visible', text=row.name, translate=False)
                line.label(text=', '.join(json.loads(row.slots)), translate=False)
    header, body = layout.panel('zzz_generic_options', default_closed=True)
    header.label(text='Optional inputs' if en else '可选输入')
    if body:
        for key, title in [('capture', 'FrameAnalysis folder' if en else '角色抓帧文件夹'), ('uv', 'UV FBX'), ('page', 'SkinPreviewPage'), ('skin_id', 'Skin ID'),
                           ('background', 'Scrolling background' if en else '滚动背景')]:
            body.prop(data, key, text=title)
    saved = settings.id_data.get('zzz_generic_material_report')
    if saved:
        header, body = layout.panel('zzz_material_results', default_closed=True)
        header.label(text='Material matches and fallbacks' if en else '材质匹配与回退')
        if body:
            report = json.loads(saved)
            labels = ({'matched':'Family matched', 'partial':'Partial', 'fallback':'Fallback', 'skipped':'Skipped'}
                      if en else {'matched':'材质族匹配', 'partial':'部分近似', 'fallback':'回退', 'skipped':'跳过'})
            body.label(text='Visible surfaces · last build' if en else '上一次生成的可见材质')
            runtime = report.get('runtime', {})
            if runtime.get('measured'):
                body.label(text='Matching capture applied' if en else '已应用匹配抓帧')
            elif runtime.get('environment') == 'studio approximation':
                body.label(text='Shared wardrobe studio approximation' if en else '共用服装页环境近似')
            if not report['entity_environment']:
                body.label(text='Entity lighting uses preview inputs' if en else '实体光照使用预览输入')
            for name, entry in report['materials'].items():
                box = body.box()
                box.label(text=name, translate=False)
                part_label = common_parts.PARTS.get(entry.get('part'), entry['kind'])
                box.label(text=f"{labels[entry['status']]} · {part_label}", translate=False)
                for line in entry['details']:
                    box.label(text=line, translate=False)
            for mesh, info in report.get('mesh_binds', {}).items():
                if isinstance(info, dict) and 'translated_vertices' in info:
                    body.label(text=f"{mesh} · {info['translated_vertices']} " +
                               ('vertices rebound' if en else '个顶点已恢复绑定'))
                else:
                    reason = info.get('skipped', '') if isinstance(info, dict) else info
                    body.label(text=f"{mesh} · {reason}", translate=False)
    notes = settings.id_data.get('zzz_generic_notes', '')
    if notes:
        header, body = layout.panel('zzz_generic_notes', default_closed=True)
        header.label(text='Import notes' if en else '导入报告')
        if body:
            for line in json.loads(notes):
                body.label(text=line, translate=False)


CLASSES = (*slot_inspector.CLASSES, ZZZ_GenericMaterial, ZZZ_GenericMesh, ZZZ_GenericInputs, ZZZ_OT_scan_generic)
