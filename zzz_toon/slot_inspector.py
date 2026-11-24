"""Material-slot inventory for the four-outfit wardrobe intersection."""
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import bpy
from bpy.props import IntProperty, StringProperty

from . import assets, common_parts

KEY = 'zzz_slot_inspection_v1'
SOURCE = 'zzz_inspector_source_mesh'
HIGHLIGHT = 'zzz_inspector_highlight'
SWITCHES = ('_UseOverlayTex', '_Emission')


class Observed(dict):
    # Track material-table fields consulted in preparation or shader construction.
    def __init__(self, values, reads):
        super().__init__(values)
        self.reads = reads

    def get(self, key, default=None):
        if key in self:
            self.reads.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.reads.add(key)
        return super().__getitem__(key)


def references(row, files, channels):
    """A null optional texture has no file requirement. Keep missing and ambiguous apart."""
    from . import generic_assets as slots
    name = row['name']
    part = common_parts.part(name, row['part'])
    shared = common_parts.CHANNELS.get(part, ())
    result = dict(part=part, material=row['material'], inputs={}, missing=[], ambiguous=[], outside=[], asset_switches={})
    props = {}
    if row['material']:
        try:
            props = assets.load_material(row['material'])[0]['m_SavedProperties']
        except (ValueError, OSError, KeyError, TypeError) as exc:
            result['missing'].append('Material JSON · ' + str(exc))
    elif part != 'caster':
        result['table_unprovided'] = True
    env = {key: item.get('m_Texture', {}) for key, item in dict(props.get('m_TexEnvs', {})).items()}
    native_refs = {key: tex['Name'] for key, tex in env.items() if tex.get('Name') and not tex.get('IsNull', True)}
    floats = dict(props.get('m_Floats', {}))
    result['asset_switches'] = {k: floats[k] for k in SWITCHES if k in floats}
    kind = 'body' if part in ('body1','body2') else part
    result['source_parameters'] = {'colors':dict(props.get('m_Colors', {})), 'floats':floats}
    required = {'base'} if shared else set()
    if part == 'eye':
        required.add('eye')
    if part == 'face':
        required.add('normal')
    allowed = {channels[k][0] for k in shared}
    for k, (shader, _) in channels.items():
        ref = native_refs.get(shader, '')
        if k not in shared and not row[k] and not ref:
            continue
        file = row[k] or slots.auto_file(k, kind, row['material'], ref, Path(row['base_hint'] or row['base']).stem, files)
        if file and Path(file).is_file():
            result['inputs'][shader] = file
        elif row[k] or ref or k in required:
            hits = files.get((ref.casefold(), False), ()) if ref and not row[k] else ()
            result['ambiguous' if len(hits) > 1 else 'missing'].append(shader + ' · ' + (row[k] or ref or '—'))
        if k not in shared and (ref or row[k]):
            result['outside'].append(shader)
    for shader, ref in native_refs.items():
        if shader in allowed or shader in {ch[0] for ch in channels.values()}:
            continue
        result['outside'].append(shader)
        hits = files.get((ref.casefold(), False), ())
        if len(hits) == 1:
            result['inputs'][shader] = next(iter(hits))
        else:
            result['ambiguous' if hits else 'missing'].append(shader + ' · ' + ref)
    return result


def reachable(material):
    """Walk surface and AOV dependencies, following the connected socket across node groups."""
    images, attributes, seen = set(), set(), set()
    def visit(socket, stack):
        node = socket.node
        key = (socket.as_pointer(), tuple(group.as_pointer() for group in stack))
        if key in seen:
            return
        seen.add(key)
        if node.type == 'TEX_IMAGE' and node.image:
            images.add(node.image.get('zzz_mip_source_slot') or node.image.get('zzz_wardrobe_slot', ''))
        if node.type in ('ATTRIBUTE','VERTEX_COLOR','UVMAP'):
            attributes.add(getattr(node, 'attribute_name', '') or getattr(node, 'layer_name', '') or getattr(node, 'uv_map', ''))
        if node.type == 'GROUP' and node.node_tree:
            inner = next((n for n in node.node_tree.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output), None)
            inputs = [s for s in inner.inputs if s.identifier == socket.identifier] if inner else []
            stack = (*stack, node)
        elif node.type == 'GROUP_INPUT' and stack:
            inputs = [s for s in stack[-1].inputs if s.identifier == socket.identifier]
            stack = stack[:-1]
        else:
            inputs = node.inputs
        for inp in inputs:
            for link in inp.links:
                visit(link.from_socket, stack)
    if material and material.node_tree:
        for node in material.node_tree.nodes:
            if node.type == 'OUTPUT_AOV' or (node.type == 'OUTPUT_MATERIAL' and node.is_active_output):
                for inp in node.inputs:
                    for link in inp.links:
                        visit(link.from_socket, ())
    return images, sorted(attributes - {''})


def preflight(req, files):
    from .generic_assets import CHANNELS
    return {row['name']: references(row, files, CHANNELS) for row in req['materials']}


def completed(req, files, meshes, materials, runtime, flags, reads):
    facts = preflight(req, files)
    for name, fact in facts.items():
        source_values = fact['source_parameters']
        fact['prepared'] = {domain: sorted(set(source_values[domain]) & keys - set(SWITCHES))
                            for domain, keys in reads.get(name, {}).items()}
        if fact['part'] in ('body1','body2') and source_values['floats'].get('_DstBlend') == 10.:
            fact['outside'].append('_DstBlend = 10')
    entries = []
    cache = {}
    evidence = runtime.get('slot_evidence', [])
    for mesh, info in req['meshes'].items():
        obj = meshes[mesh]
        counts = Counter(p.material_index for p in obj.data.polygons)
        for i, name in enumerate(info['slots']):
            fact = facts[name]
            part = fact['part']
            state = ('hidden' if not info['visible'] else 'outside' if part == 'exclude' else
                     'caster' if part == 'caster' else 'shared')
            used, attributes = [], []
            if state == 'shared' and name in materials and i < len(obj.material_slots):
                mat = obj.material_slots[i].material
                if mat not in cache:
                    cache[mat] = reachable(mat)
                images, attrs = cache[mat]
                attributes = [a for a in attrs if a in obj.data.attributes or a in obj.data.uv_layers]
                used = sorted(k for k, token in materials[name]['tex'].items() if token + '.png' in images
                              or (k == '_EyeColorMap' and 'zzz_eye' in attributes))
            frame = [e for e in evidence if (e['mesh'], e['slot']) == (mesh,i)]
            for draw, f in runtime.get('feature_draws', {}).items():
                if (f.get('status'),f.get('object'),f.get('slot')) == ('exact_uv_triangles',mesh,i):
                    frame.append(dict(draw=draw, feature=f['kind'], state='matched', ps=f['ps']))
            native = flags.get('native_face') or {}
            proj = native.get('receiver', {})
            if state == 'caster' and mesh == proj.get('caster') and counts[i] and len(info['slots']) == 1:
                frame.append(dict(draw=runtime['face']['caster'],feature='fringe caster',state='matched',ps=proj['ps']))
            partial = runtime.get('partial_fringe') or {}
            if (mesh, name) == (partial.get('mesh'), partial.get('material')):
                for draw in (flags.get('native_face_layers') or {}).get('source', {}).get('draws', []):
                    frame.append(dict(draw=draw,feature=f"fringe {partial['verified_faces']}/{partial['slot_faces']}",
                                      state='matched',ps=runtime['feature_draws'][draw]['ps']))
            if mesh == runtime.get('face', {}).get('mesh') and name in native.get('materials', {}):
                src = native['source']
                for draw in (runtime['face']['base'], runtime['face']['receiver']):
                    frame.append(dict(draw=draw,feature='face',state='matched',ps=src['shaders']['ps']))
                    if src['shaders']['ps'] == '202c3cc697d36317':
                        if '_BlushTexture' not in fact['outside']:
                            fact['outside'].append('_BlushTexture')
                        frame.append(dict(draw=draw,feature='_BlushTexture',state='inactive',ps=src['shaders']['ps']))
            entries.append(dict(mesh=mesh,slot=i,material=name,state=state,faces=counts[i],used=used,
                                attributes=attributes,frame=frame))
    return dict(fingerprint=req['fingerprint'], capture=req.get('capture',''), materials=facts, slots=entries)


def geometry_path(fingerprint):
    if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('Invalid FBX fingerprint')
    return Path(bpy.app.tempdir) / ('zzz-rest-' + fingerprint + '.npz')


def cache_geometry(objects, path):
    import numpy as np
    arrays = {}
    names = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        prefix = str(len(names))
        names.append(obj.name)
        vertices = np.empty((len(mesh.vertices), 3), 'f')
        mesh.vertices.foreach_get('co', vertices.ravel())
        matrix = np.array(obj.matrix_world)
        arrays[prefix+'v'] = (vertices @ matrix[:3,:3].T + matrix[:3,3]).astype('f')
        tri = np.empty((len(mesh.loop_triangles),3),'i')
        mat = np.empty(len(tri),'i')
        mesh.loop_triangles.foreach_get('vertices', tri.ravel())
        mesh.loop_triangles.foreach_get('material_index', mat)
        arrays[prefix+'t'], arrays[prefix+'m'] = tri, mat
    np.savez_compressed(path, names=names, **arrays)


def rest_highlight(fingerprint, row):
    import numpy as np
    from mathutils import Matrix
    with np.load(geometry_path(fingerprint), allow_pickle=False) as pack:
        key = str(list(pack['names']).index(row.mesh))
        triangles = pack[key+'t'][pack[key+'m'] == row.slot]
        if not len(triangles):
            return None, None
        vertices, flat = np.unique(triangles, return_inverse=True)
        mesh = bpy.data.meshes.new('ZZZ · Inspected faces')
        mesh.from_pydata(pack[key+'v'][vertices].tolist(), [], flat.reshape(-1,3).tolist())
        mesh.update()
        return mesh, Matrix.Identity(4)


class ZZZ_InspectRow(bpy.types.PropertyGroup):
    mesh: StringProperty()
    slot: IntProperty()
    material: StringProperty()


def populate(data, meshes):
    data.inspection_rows.clear()
    data.inspection_index = 0
    for mesh, items in meshes.items():
        for index, mat in enumerate(items):
            row = data.inspection_rows.add()
            row.mesh, row.slot, row.material = mesh, index, mat
            row.name = f'{mesh} [{index}]  {mat}'


def clear_highlight(scene):
    for ob in list(scene.objects):
        if ob.get(HIGHLIGHT) is True:
            mesh = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


_views = {}


def toggle_inspection_view(context, enable, highlight=None):
    for area in context.screen.areas if context.screen else ():
        if area.type != 'VIEW_3D' or (enable and context.area and context.area.type == 'VIEW_3D' and area != context.area):
            continue
        space = area.spaces.active
        key = (context.scene.as_pointer(), space.as_pointer())
        region = space.region_3d
        if enable:
            _views.setdefault(key,(space.shading.type, space.overlay.show_overlays,
                                   space.overlay.show_outline_selected, region.view_perspective,
                                   region.view_location.copy(), region.view_distance, region.view_rotation.copy(),
                                   space.overlay.show_bones, space.overlay.show_extras, space.overlay.show_relationship_lines))
            space.shading.type = 'SOLID'
            space.overlay.show_overlays = space.overlay.show_outline_selected = True
            space.overlay.show_bones = space.overlay.show_extras = space.overlay.show_relationship_lines = False
            if highlight and region.view_perspective == 'CAMERA':
                region.view_perspective = 'PERSP'
            if highlight:
                # Change the editor view only. Locked preview cameras keep their original transform.
                from mathutils import Vector
                corners = [highlight.matrix_world @ Vector(co) for co in highlight.bound_box]
                center = sum(corners, Vector()) / 8.
                region.view_location = center
                region.view_distance = max(.025, max((v-center).length for v in corners) * 3.2)
        elif key in _views:
            shading, overlays, outline, perspective, location, distance, rotation, bones, extras, lines = _views.pop(key)
            space.overlay.show_bones, space.overlay.show_extras, space.overlay.show_relationship_lines = bones, extras, lines
            space.shading.type, space.overlay.show_overlays, space.overlay.show_outline_selected = shading, overlays, outline
            region.view_location, region.view_distance, region.view_rotation = location, distance, rotation
            region.view_perspective = perspective


class ZZZ_OT_clear_inspection(bpy.types.Operator):
    bl_idname = 'zzz_toon.clear_inspection'
    bl_label = '清除高亮 / Clear highlight'

    def execute(self, context):
        clear_highlight(context.scene)
        toggle_inspection_view(context, False)
        return {'FINISHED'}


class ZZZ_OT_locate_slot(bpy.types.Operator):
    bl_idname = 'zzz_toon.locate_slot'
    bl_label = '定位并高亮材质槽 / Locate and highlight slot'
    row_index: IntProperty(min=0)

    def execute(self, context):
        scene, data = context.scene, context.scene.zzz_toon.assets_generic
        if self.row_index >= len(data.inspection_rows):
            return {'CANCELLED'}
        row = data.inspection_rows[self.row_index]
        data.inspection_index = self.row_index
        built = scene.get('zzz_slot_model') == data.fingerprint
        if not built:
            try:
                mesh, world = rest_highlight(data.fingerprint, row)
            except (ValueError, OSError, KeyError):
                self.report({'WARNING'}, '请重新读取 FBX 槽位 / Read FBX slots again')
                return {'CANCELLED'}
            clear_highlight(scene)
            return self.show(context, row, mesh, world)
        found = [o for o in scene.objects if o.get(SOURCE) == row.mesh and not o.get(HIGHLIGHT)]
        if len(found) != 1 or row.slot >= max(1,len(found[0].material_slots)):
            self.report({'WARNING'}, '源网格或槽位已更改 / Source mesh or slot changed')
            return {'CANCELLED'}
        source = found[0]
        clear_highlight(scene)
        # Evaluate skin and shape keys without the shared-scope deletion or outline hull.
        tmp = source.copy()
        tmp.pop(SOURCE, None)
        tmp.hide_viewport = False
        tmp.hide_render = True
        mesh, world = None, None
        try:
            for mod in list(tmp.modifiers):
                if mod.type != 'ARMATURE':
                    tmp.modifiers.remove(mod)
            scene.collection.objects.link(tmp)
            dg = context.evaluated_depsgraph_get()
            posed = tmp.evaluated_get(dg)
            selected = [p.vertices[:] for p in posed.data.polygons if p.material_index == row.slot]
            if selected:
                indices = sorted({v for poly in selected for v in poly})
                lookup = {v:i for i,v in enumerate(indices)}
                mesh = bpy.data.meshes.new('ZZZ · Inspected faces')
                mesh.from_pydata([posed.data.vertices[v].co[:] for v in indices], [],
                                 [tuple(lookup[v] for v in poly) for poly in selected])
                mesh.update()
                world = posed.matrix_world.copy()
        finally:
            bpy.data.objects.remove(tmp, do_unlink=True)
        if source.visible_get():
            source.active_material_index = row.slot
            if context.mode == 'OBJECT':
                source.select_set(True)
                context.view_layer.objects.active = source
        return self.show(context, row, mesh, world)

    def show(self, context, row, mesh, world):
        scene = context.scene
        if mesh is None:
            toggle_inspection_view(context, False)
            self.report({'INFO'}, '该槽没有面 / Slot has no faces')
            return {'FINISHED'}
        ob = bpy.data.objects.new('ZZZ · ' + row.material, mesh)
        ob[HIGHLIGHT] = True
        ob.hide_render = True
        ob.matrix_world = world
        ob.show_in_front, ob.display_type = True, 'WIRE'
        ob.color = (1., .48, .02, 1.)
        scene.collection.objects.link(ob)
        context.view_layer.update()
        toggle_inspection_view(context, True, ob)
        if context.mode == 'OBJECT':
            for other in list(context.selected_objects):
                other.select_set(False)
            ob.select_set(True)
            context.view_layer.objects.active = ob
        return {'FINISHED'}


class ZZZ_OT_check_slot_inputs(bpy.types.Operator):
    bl_idname = 'zzz_toon.check_slot_inputs'
    bl_label = '检查输入文件 / Check input files'

    def execute(self, context):
        from . import generic_assets as slots
        data = context.scene.zzz_toon.assets_generic
        rows = [slots.material_record(row) for row in data.materials]
        extras = [row[k] for row in rows for k in ('material', *slots.CHANNELS) if row[k]]
        try:
            files = slots.index_files(slots.absolute(data.model), extras)
            facts = preflight(dict(materials=rows), files)
        except (ValueError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        data.inspection_files = json.dumps(facts, ensure_ascii=False)
        data.inspection_source = True
        return {'FINISHED'}


class ZZZ_UL_inspection(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        line = layout.row(align=True)
        line.label(text=item.name, translate=False)
        if data.fingerprint:
            line.operator('zzz_toon.locate_slot', text='', icon='VIEWZOOM', emboss=False).row_index = index


def draw(layout, settings):
    data = settings.assets_generic
    if not data.inspection_rows:
        return
    en = settings.wardrobe_language == 'EN'
    header, box = layout.panel('zzz_all_material_slots', default_closed=False)
    header.label(text=('All model slots' if en else '模型全部材质槽') + f' ({len(data.inspection_rows)})')
    if not box:
        return
    box.template_list('ZZZ_UL_inspection','',data,'inspection_rows',data,'inspection_index',rows=6)
    index = data.inspection_index
    if not (0 <= index < len(data.inspection_rows)):
        return
    scene = settings.id_data
    row = data.inspection_rows[index]
    box.label(text=f"[{row.slot}]  {row.mesh}", icon='MESH_DATA', translate=False)
    box.label(text=row.material, icon='MATERIAL', translate=False)
    generated = scene.get('zzz_slot_model') == data.fingerprint and KEY in scene
    if generated:
        box.prop(data,'inspection_source', text='Show current input check' if en else '查看当前输入检查')
    built = generated and not data.inspection_source
    box.operator('zzz_toon.clear_inspection', icon='X')
    if built:
        report = decoded(scene[KEY])
        entry = next((e for e in report['slots'] if (e['mesh'],e['slot'],e['material']) ==
                      (row.mesh,row.slot,row.material)), None)
        fact = report['materials'].get(row.material)
        box.label(text='Saved build · capture evidence' if en else '生成时快照 · 所选抓帧证据')
    else:
        box.operator('zzz_toon.check_slot_inputs', icon='FILE_REFRESH')
        box.label(text=('Current input files' if en else '当前输入文件') if generated else ('Scanned FBX · rest pose' if en else '已扫描的 FBX · 静止姿态'))
        if not data.inspection_files:
            box.label(text='Run Check input files to inspect edits' if en else '点击检查输入文件以查看当前配置')
        fact = decoded(data.inspection_files).get(row.material) if data.inspection_files else None
        entry = None
    if not fact:
        return
    box.label(text=common_parts.PARTS.get(fact['part'], '投影辅助 / Caster' if fact['part']=='caster' else '交集外 / Outside'))
    if fact.get('table_unprovided'):
        box.label(text='Material table not supplied · bindings unverified' if en else '未提供原始材质表 · 绑定待核实')
    if fact['material']:
        box.label(text='JSON · '+Path(fact['material']).name, translate=False)
    if entry:
        status = {'hidden':'已隐藏 / Hidden', 'outside':'交集外 / Outside', 'caster':'投影辅助 / Caster', 'shared':'共用 / Shared'}
        box.label(text=f"{status[entry['state']]} · {entry['faces']} faces")
        if report['capture']:
            box.label(text=Path(report['capture']).name, translate=False)
        for key in entry['used']:
            box.label(text=('Used · ' if en else '着色使用 · ') + key + ' · ' + Path(fact['inputs'].get(key,'')).name)
        if entry['attributes']:
            box.label(text='Mesh · '+', '.join(entry['attributes']), translate=False)
    for key, file in fact['inputs'].items():
        if not entry or key not in entry['used']:
            box.label(text=('Located file · ' if en else '找到文件 · ') + key + ' · '+Path(file).name)
    for key in fact['outside']:
        box.label(text=('Outside · ' if en else '交集外 · ') + key, icon='INFO')
    for key, value in fact['asset_switches'].items():
        box.label(text=f"{'Outside · asset setting' if en else '交集外 · 原表开关'} · {key} = {value:g}")
    if fact.get('source_parameters') and any(fact['source_parameters'].values()):
        subheader, sub = box.panel('zzz_selected_source_fields', default_closed=True)
        subheader.label(text='Material table fields' if en else '原始材质表字段')
        if sub:
            for domain, keys in fact['source_parameters'].items():
                prepared = set(fact.get('prepared', {}).get(domain, []))
                for key,value in sorted(keys.items()):
                    text = ('原表 / Asset' if key not in prepared else '构建读取 / Read')
                    sub.label(text=f"{text} · {key} = {value}", translate=False)
    for field, label in [('missing','缺资源 / Missing'),('ambiguous','多个候选 / Ambiguous')]:
        for key in fact[field]:
            box.label(text=label+' · '+key, icon='ERROR')
    labels = {'inactive':'本次绘制未生效 / Inactive in draw','enabled':'本次绘制已启用 / Enabled in draw',
              'matched':'绘制匹配 / Matched draw'}
    for f in entry['frame'] if entry else ():
        box.label(text=f"{f['draw']} · {f['feature']} · {labels[f['state']]}", translate=False)
    if not entry or not entry['frame']:
        box.label(text='No verified draw for this slot' if en else '该槽没有已核实的绘制证据')


@lru_cache(maxsize=2)
def decoded(raw):
    return json.loads(raw)


CLASSES = (ZZZ_InspectRow, ZZZ_OT_check_slot_inputs, ZZZ_OT_locate_slot, ZZZ_OT_clear_inspection, ZZZ_UL_inspection)
