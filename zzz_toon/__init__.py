"""ZZZ Toon: Zenless Zone Zero style toon shading for game-format characters (EEVEE)."""
bl_info = {
    "name": "ZZZ Toon",
    "author": "ZZZToonShading project",
    "version": (0, 52, 0),
    "blender": (5, 3, 0),
    "location": "3D View > Sidebar > ZZZ Toon",
    "description": "从模型、贴图和待机动画生成角色服装预览，支持三个已验证预设和通用导入",
    "category": "Material",
}

import bpy, os, json
from pathlib import Path
from bpy.props import StringProperty, EnumProperty, FloatVectorProperty, FloatProperty, BoolProperty, PointerProperty
from . import core, character, native_lighting, outfit_import, capture_import, preview, capture_preview, wardrobe
from . import fullscreen, wardrobe_slots, wardrobe_language, generic_assets


ROLE_ITEMS = [(r, r, "") for r in wardrobe.ROLES] + [(generic_assets.ROLE, "通用 / Generic", "")]


class ZZZToonSettings(bpy.types.PropertyGroup):
    wardrobe_language: EnumProperty(name="Language / 语言",items=[("ZH","中文",""),("EN","English","")],default="ZH")
    wardrobe_selection: EnumProperty(name="角色",items=ROLE_ITEMS)
    wardrobe_role: EnumProperty(name="角色",items=ROLE_ITEMS)
    assets_generic: PointerProperty(type=generic_assets.ZZZ_GenericInputs)
    assets_remielle: PointerProperty(type=wardrobe_slots.CLASSES[0])
    assets_velina: PointerProperty(type=wardrobe_slots.CLASSES[1])
    assets_vivian: PointerProperty(type=wardrobe_slots.CLASSES[2])
    # Read the old fields when migrating projects saved through v0.45.1.
    wardrobe_model: StringProperty(name="角色模型 FBX",subtype="FILE_PATH")
    wardrobe_textures: StringProperty(name="贴图目录",subtype="DIR_PATH")
    wardrobe_idle: StringProperty(name="预览待机动画",subtype="FILE_PATH",description="AnimeStudio AnimationClip .anim 或已解析的 .json")
    wardrobe_background: StringProperty(name="替换滚动背景",subtype="FILE_PATH",description="留空使用内置服装预览背景")
    wardrobe_output: StringProperty(name="工程输出目录",subtype="DIR_PATH",description="留空保存在 Blender 用户数据目录")
    wardrobe_result: StringProperty(name="生成的工程",subtype="FILE_PATH")
    wardrobe_status: StringProperty(options={'SKIP_SAVE'})
    wardrobe_zoom: FloatProperty(name="全身 ↔ 近景",min=0,max=1,default=0,update=wardrobe.set_zoom)
    preview_defaults: BoolProperty(name="Use outfit preview defaults", default=True,
        description="Use the bundled measured outfit profile for verified assets and shared preview settings for other exports")
    preview_pose: BoolProperty(name="Start in reference pose and camera", default=True,
        description="Use the verified presentation on import when available. Disable to keep the FBX pose")
    use_outfit_profile: BoolProperty(name="Use matching bundled outfit", default=True,
        description="Disable to import as a new outfit using shared settings and the supplied files")
    advanced: BoolProperty(name="Advanced shading settings", default=False)
    asset_dir: StringProperty(name="Character asset folder", subtype="DIR_PATH",
        description="Folder containing one prefab FBX, Textures and Materials")
    frame_dir: StringProperty(name="FrameAnalysis folder", subtype="DIR_PATH",
        description="Optional raw capture of this outfit. Uses matching geometry and verified shader variants")
    space_registration: StringProperty(name="Capture registration (optional)", subtype="FILE_PATH",
        description="Measured Unity and Blender point pair for this capture. Otherwise use the imported head joint")
    outfit_page: StringProperty(name="Outfit page export", subtype="FILE_PATH",
        description="Optional AnimeStudio UIAvatarShowSettings .dat or resolved JSON")
    skin_id: StringProperty(name="SkinID", description="Outfit identifier for page overrides")
    frame_weapons: BoolProperty(name="Show weapons matched in capture", default=True)
    fbx: StringProperty(name="Prefab FBX", subtype="FILE_PATH",
                        description="The character's UI prefab exported as FBX (vertex colours + UV0)")
    fbx_uv: StringProperty(name="FBX with all UVs", subtype="FILE_PATH",
                           description="Optional second export with UV2 / UV3 (baked outline normals)")
    tex_dir: StringProperty(name="Textures", subtype="DIR_PATH")
    character_overlay: StringProperty(name="Character overlay (optional)", subtype="FILE_PATH",
        description="Shared single-level CharacterOverlayTex for GAME materials requesting it; defaults to the texture directory")
    mat_dir: StringProperty(name="Material JSONs", subtype="DIR_PATH")
    light_snapshot: StringProperty(name="Light snapshot (optional)", subtype="FILE_PATH",
        description="Explicit world-basis snapshot for GAME body/hair lighting; never a universal preset")
    globals_snapshot: StringProperty(name="Shader globals (optional)", subtype="FILE_PATH",
        description="GAME shadow tints, entity and rim inputs for this capture, outfit or scene (JSON)")
    head_sphere: StringProperty(name="Head sphere (optional)", subtype="FILE_PATH",
        description="Measured head-bone local center and world radius for native shape highlights")
    environment_snapshot: StringProperty(name="Environment snapshot (optional)", subtype="FILE_PATH",
        description="Explicit registered world-space light colour, gradient and character ambient for GAME")
    character_lut: StringProperty(name="Character LUT (optional)", subtype="FILE_PATH",
        description="Explicit captured native character color LUT; does not change camera, lights or bloom")
    outline_snapshot: StringProperty(name="Outline snapshot (optional)", subtype="FILE_PATH",
        description="Native outline with verified mesh normals and explicit scene inputs; no fitted width scale")
    surface_profile: StringProperty(name="Native surface (optional)", subtype="FILE_PATH",
        description="Verified original N/T and linear skinning; explicit mesh inputs, no captured pose defaults")
    sampling_profile: StringProperty(name="Mip sampling (optional)", subtype="FILE_PATH",
        description="Authored mip chains and explicit bias for the render camera; not arbitrary viewport projection")
    face_layers: StringProperty(name="Face layers (optional)", subtype="FILE_PATH",
        description="Explicit UI eye/hair profile; optionally uses scene-depth premultiplied feature layers")
    native_face: StringProperty(name="Native face (optional)", subtype="FILE_PATH",
        description="Verified face inputs; optional receiver profile updates projected shadow coverage on frame/load/render")
    bloom_profile: StringProperty(name="Bloom profile (optional)", subtype="FILE_PATH",
        description="Scene Bloom configuration; generates glow from the current render and replaces legacy Bloom")
    transparent_profile: StringProperty(name="Transparent cloth (optional)", subtype="FILE_PATH",
        description="Explicit UI cloth variant: material LUT and coverage after opaque character rendering")
    repair_exports: BoolProperty(name="Repair verified FBX export errors", default=True,
                                  description="Repair only duplicate bind matrices and exact cluster permutations")
    preset: EnumProperty(name="Preset", items=[
        ("GAME", "Character preview", "Material data + the reconstructed game shading functions (shadow ramp, metal, "
                                "specular, rim); accepts explicit scene lighting and shader globals"),
        ("UI_FIT", "Outfit screen (fitted)", "Older look: ramps and tints fitted on the first character's video")])
    key_dir: FloatVectorProperty(name="Key light", size=3, default=core.P["key_world"], subtype="DIRECTION",
                                 description="World direction toward the key light")
    outline_scale: FloatProperty(name="Outline width", default=1.0, min=0.0, max=5.0)
    skin_smooth: BoolProperty(name="Smooth skin silhouette", default=True)
    hide_weapon: BoolProperty(name="Hide weapon", default=True)
    eye_through: FloatProperty(name="Eyes through hair", default=1.0, min=0.0, max=2.0,
                               description="Gain on the hair opacity map (1 = asset value, 0 = off)")
    bloom: BoolProperty(name="Bloom (compositor)", default=True,
                        description="Glare / Bloom node in the scene compositor, like the game's post process")


def _objs(context):
    sel = [o for o in context.selected_objects]
    arm = next((o for o in sel if o.type == "ARMATURE"), None)
    if arm is None:
        rigs={o.find_armature() for o in sel if o.type=='MESH'}
        if len(rigs)==1:
            candidate=next(iter(rigs))
            if candidate and candidate.get(preview.RIG_KEY):
                arm=candidate
        if arm is None and sel and all(o.type=='MESH' for o in sel):
            roots={next((p for p in _parents(o) if p.type=='ARMATURE' and p.get(capture_preview.STAGE)),None)
                   for o in sel}
            if len(roots)==1:
                arm=roots.pop()
    if arm:
        return [arm] + [o for o in arm.children_recursive if not o.get('zzz_outline_helper')]
    return [o for o in sel if not o.get('zzz_outline_helper')]


def _parents(obj):
    while obj.parent:
        obj=obj.parent
        yield obj


def _globals(settings):
    if settings.preset == "GAME" and settings.globals_snapshot:
        return native_lighting.load_globals(bpy.path.abspath(settings.globals_snapshot))
    return None


def _paths(s, importing=False):
    def path(value):
        return Path(bpy.path.abspath(value)).resolve() if value else None
    fbx, uv = path(s.fbx), path(s.fbx_uv)
    tex, mats = path(s.tex_dir), path(s.mat_dir)
    if s.asset_dir:
        root = path(s.asset_dir)
        fbx = outfit_import.asset_paths(root)
        uv = fbx.with_stem(fbx.stem + "_uv")
        uv = uv if uv.is_file() else None
        tex, mats = root/"Textures", root/"Materials"
        if not tex.is_dir() or not mats.is_dir():
            raise ValueError("Character asset folder must contain Textures and Materials directories")
    if importing and (not fbx or not fbx.is_file()):
        raise ValueError("Prefab FBX not found")
    frame = path(s.frame_dir) if s.preset == "GAME" else None
    if frame and not frame.is_dir():
        raise ValueError("FrameAnalysis folder not found")
    registration = None
    if frame:
        source = path(s.space_registration) if s.space_registration else frame/"space_registration.json"
        if s.space_registration or source.is_file():
            registration = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(registration, dict):
                raise ValueError("Capture registration must be a JSON object")
    page = path(s.outfit_page) if s.preset == "GAME" else None
    if s.asset_dir and not page and s.preset == "GAME":
        page = outfit_import.discover_page(fbx)
    lighting, note = outfit_import.page_light(page, s.skin_id.strip()) if page else (None, None)
    choice = preview.select(fbx,uv,mats,tex,frame,s.use_outfit_profile) if s.preview_defaults and s.preset=="GAME" else None
    return dict(fbx=fbx, uv=uv, tex=str(tex or ""), mats=str(mats or ""), frame=frame, preview=choice, fresh=importing,
                page=str(page or ""), page_light=lighting, page_status=note, registration=registration)


def _apply(context, objs, globals_snapshot, paths):
    s = context.scene.zzz_toon
    k = s.outline_scale
    ov = {n: character.DEFAULT_P[n] * k for n in ("outline_body", "outline_hair", "outline_face")}
    choice=paths.get('preview')
    fresh=paths.get('fresh',False)
    rig=next((o for o in objs if o.type=='ARMATURE'),None)
    if not fresh and (not rig or not rig.get('zzz_preview_defaults')):
        choice=None
    if choice:
        ov.update(choice['common'])
    if paths['page_light']:
        ov['native_light_profile'] = paths['page_light']
    matched, notes, auto = {}, [], {}
    if paths['frame']:
        try:
            auto, matched, notes = capture_import.read_frame(paths['frame'], objs, paths['registration'])
        except (OSError, ValueError) as err:
            notes.append(f"Capture unavailable: {err}")
        if 'native_globals' in auto and 'native_light_profile' not in auto:
            ov.pop('native_light_profile', None)
            notes.append("Matched draw disables the part-light override; using scene key direction")
        ov.update({key:value for key,value in auto.items() if key != 'capture_features'})
        if 'native_bloom' in ov and not s.bloom:
            ov['native_bloom'] = dict(ov['native_bloom'], enabled=False)
    known=choice and choice['outfit'] and (fresh or rig.get(preview.RIG_KEY)==choice['outfit'])
    raw_stage=None
    if known:
        objs,defaults=preview.prepare(context.scene,objs,choice,s.preview_pose,fresh)
        ov.update(defaults)
        if (auto.get('native_environment') or {}).get('registration',{}).get('method')=='registered_points':
            ov['native_environment']=auto['native_environment']
    elif choice and rig:
        if fresh and s.preview_pose and matched:
            try:
                prepared=capture_preview.prepare(paths['frame'],objs,auto,matched)
            except (OSError,KeyError,IndexError,ValueError,ArithmeticError) as err:
                notes.append("Static capture preview unavailable: "+str(err))
            else:
                raw_stage=capture_preview.install(context.scene,objs,prepared)
                raw_stage['frame']=str(paths['frame'])
                rig[capture_preview.STAGE]=json.dumps(raw_stage)
        elif not fresh and capture_preview.STAGE in rig:
            raw_stage=json.loads(rig[capture_preview.STAGE])
        if raw_stage:
            objs=[o for o in objs if o==rig or o.name in raw_stage['objects']]
            notes.extend(raw_stage['warnings'])
            if raw_stage['outline']:
                ov['native_outline']=raw_stage['outline']
        elif fresh:
            shown=set(matched.values()) if s.frame_weapons else set()
            framing=[o for o in objs if o.type=='MESH' and o.visible_camera and
                     (not s.hide_weapon or 'weapon' not in o.name.lower() or o.name in shown)]
            preview.camera(context.scene,meshes=framing)
    if choice and fresh and rig:
        rig['zzz_preview_defaults']=True
    if 'native_bloom' in ov and not s.bloom:
        ov['native_bloom']=dict(ov['native_bloom'],enabled=False)
    if globals_snapshot is not None:
        ov["native_globals"] = {**ov.get("native_globals", {}), **globals_snapshot}
    if s.preset == "GAME" and s.character_overlay:
        ov["character_overlay"] = bpy.path.abspath(s.character_overlay)
    if s.light_snapshot and s.preset == 'GAME':
        with open(bpy.path.abspath(s.light_snapshot), encoding='utf8') as f:
            ov['native_light_profile'] = json.load(f)
    if s.head_sphere and s.preset == 'GAME':
        with open(bpy.path.abspath(s.head_sphere), encoding='utf8') as f:
            ov['native_head_sphere'] = json.load(f)
    if s.environment_snapshot and s.preset == 'GAME':
        with open(bpy.path.abspath(s.environment_snapshot), encoding='utf8') as f:
            ov['native_environment'] = json.load(f)
    if s.character_lut and s.preset == 'GAME':
        path=bpy.path.abspath(s.character_lut)
        with open(path,encoding='utf8') as f:profile=json.load(f)
        if not os.path.isabs(profile.get('texture','')):profile['texture']=os.path.normpath(os.path.join(os.path.dirname(path),profile['texture']))
        ov['native_character_lut']=profile
    if s.outline_snapshot and s.preset == 'GAME':
        path=bpy.path.abspath(s.outline_snapshot)
        with open(path,encoding='utf8') as f:profile=json.load(f)
        for item in profile.get('mesh_inputs',{}).values():
            if not os.path.isabs(item['path']):item['path']=os.path.normpath(os.path.join(os.path.dirname(path),item['path']))
        ov['native_outline']=profile
    if s.surface_profile and s.preset == 'GAME':
        path=bpy.path.abspath(s.surface_profile)
        with open(path,encoding='utf8') as f:profile=json.load(f)
        for item in profile.get('mesh_inputs',{}).values():
            if not os.path.isabs(item['path']):item['path']=os.path.normpath(os.path.join(os.path.dirname(path),item['path']))
        ov['native_surface']=profile
    if s.sampling_profile and s.preset == 'GAME':
        path=bpy.path.abspath(s.sampling_profile)
        with open(path,encoding='utf8') as f:profile=json.load(f)
        for textures in profile.get('materials',{}).values():
            for item in textures.values():
                if not os.path.isabs(item['atlas']):item['atlas']=os.path.normpath(os.path.join(os.path.dirname(path),item['atlas']))
        ov['native_sampling']=profile
        if raw_stage:
            required=set(profile.get('meshes',[]))
            objs.extend(o for o in rig.children_recursive if o.type=='MESH' and o.name in required and o not in objs)
    if s.face_layers and s.preset == 'GAME':
        with open(bpy.path.abspath(s.face_layers), encoding='utf8') as f:
            profile = json.load(f)
        for item in profile.get('eye_inputs',{}).values():
            if not os.path.isabs(item['path']):item['path']=os.path.normpath(os.path.join(os.path.dirname(bpy.path.abspath(s.face_layers)),item['path']))
        ov['native_face_layers'] = profile
    if s.native_face and s.preset == 'GAME':
        path=bpy.path.abspath(s.native_face)
        with open(path,encoding='utf8') as f:profile=json.load(f)
        for item in profile.get('mesh_inputs',{}).values():
            if not os.path.isabs(item['path']):item['path']=os.path.normpath(os.path.join(os.path.dirname(path),item['path']))
        ov['native_face']=profile
        if profile.get('receiver'):
            item=profile['receiver']
            if not os.path.isabs(item['red_path']):item['red_path']=os.path.normpath(os.path.join(os.path.dirname(path),item['red_path']))
    if s.bloom_profile and s.preset == 'GAME':
        with open(bpy.path.abspath(s.bloom_profile),encoding='utf8') as f:profile=json.load(f)
        character.native_bloom.validate(profile)
        profile['enabled']=profile['enabled'] and s.bloom
        ov['native_bloom']=profile
    if s.transparent_profile and s.preset == 'GAME':
        with open(bpy.path.abspath(s.transparent_profile),encoding='utf8') as f:profile=json.load(f)
        character.transparent_layers.validate(profile)
        ov['native_transparency']=profile
    direction=choice['config']['stage']['key_dir'] if known and not s.is_property_set('key_dir') else tuple(s.key_dir)
    lights=None
    if known:
        lights=choice['config']['stage']['lights'] if fresh and s.preview_pose else preview.light_state(context.scene)
    hide=s.hide_weapon and (not known or s.is_property_set('hide_weapon'))
    rep = character.setup_character(objs, paths['tex'], paths['mats'], s.preset, ov,
                                    direction, context.scene, s.skin_smooth and not raw_stage,hide=("Weapon",) if hide else ())
    weapons = []
    if auto and s.frame_weapons:
        for ob in objs:
            if ob.name in matched.values() and "weapon" in ob.name.lower():
                ob.hide_render = ob.hide_viewport = False
                weapons.append(ob.name)
    if s.eye_through > 0:
        character.setup_eye_pass(context.scene, objs)
    character.setup_compositor(context.scene, (0.8, 0.35, 0.6) if s.bloom else None, s.eye_through, s.eye_through)
    if known:
        preview.finish(context.scene,objs,choice['config']['stage'],lights,s.is_property_set('key_dir'))
    environment = ov.get('native_environment') or {}
    placement = environment.get('registration') or {}
    if placement.get('method') == 'fbx_bind_head' and not s.environment_snapshot:
        for obj in objs:
            if obj.type == 'ARMATURE':
                obj[capture_import.capture_space.ANCHOR_KEY] = json.dumps(placement)
    report = dict(frame=str(paths['frame'] or ''), page=paths['page'], page_status=paths['page_status'],
                  preview=choice['status'] if choice else None, bundled_outfit=choice['outfit'] if known else None,
                  preview_pose=bool(known and rig.get('zzz_preview_reference_pose')),
                  capture_stage_applied=bool(raw_stage), capture_stage=raw_stage,
                  effective_inputs=sorted(ov),
                  capture_inputs=sorted(auto), matched_draws=matched, matched_weapons=weapons,
                  active_light=ov.get('native_light_profile'), shader_globals=ov.get('native_globals',{}),
                  character_lut=ov.get('native_character_lut'), bloom=ov.get('native_bloom'),
                  character_ambient=ov.get('native_character_ambient'), warnings=notes+rep.get('material_warnings',[]),
                  captured_overlay=auto.get('capture_overlay'),
                  environment=environment, feature_draws=auto.get('capture_features', {}),
                  skipped_materials=rep['skipped'])
    context.scene['zzz_first_import_report'] = json.dumps(report, ensure_ascii=False)
    rep.setdefault('material_warnings', []).extend(notes)
    if paths['page_status'] and not paths['page_light'] and not ov.get('native_light_profile'):
        rep['material_warnings'].append(paths['page_status'])
    return rep


class ZZZTOON_OT_import(bpy.types.Operator):
    bl_idname = "zzz_toon.import_character"
    bl_label = "Import + Shade"
    bl_description = "Import the prefab FBX and build the toon shading"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.zzz_toon
        try:
            paths = _paths(s, importing=True)
            globals_snapshot = _globals(s)
            choice=paths.get('preview')
            if choice and choice['outfit']:
                collisions=set(choice['config']['stage']['objects']).intersection(bpy.data.objects.keys())
                if collisions:
                    raise ValueError("Outfit meshes already exist in this file. Select their rig and rebuild, or import in a new file.")
        except (OSError, ValueError, OverflowError) as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        # A stock Blender startup contains a renderable cube and light. Preserve that scene
        # when the user asks for a self-contained reference preview.
        if choice and s.preview_pose and sorted(o.name for o in context.scene.objects)==['Camera','Cube','Light']:
            target=bpy.data.scenes.new('ZZZ Outfit Preview')
            for prop in s.bl_rna.properties:
                if not prop.is_readonly and s.is_property_set(prop.identifier):
                    setattr(target.zzz_toon,prop.identifier,getattr(s,prop.identifier))
            context.window.scene=target
            s=target.zzz_toon
        character.setup_scene(context.scene)
        objs = character.import_fbx(str(paths['fbx']), str(paths['uv']) if paths['uv'] else None,
                                   repair_exports=s.repair_exports)
        rep = _apply(context, objs, globals_snapshot, paths)
        self.report({"INFO"}, "ZZZ Toon: %d materials, skipped %s" % (len(rep["materials"]), rep["skipped"]))
        for message in rep.get("material_warnings", []):
            self.report({"WARNING"}, message)
        return {"FINISHED"}


class ZZZTOON_OT_apply(bpy.types.Operator):
    bl_idname = "zzz_toon.apply"
    bl_label = "Rebuild Shading on Selection"
    bl_description = "Rebuild materials / outlines on the selected character (select its armature)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objs = _objs(context)
        if not any(o.type == "MESH" for o in objs):
            self.report({"ERROR"}, "Select the character's armature or meshes"); return {"CANCELLED"}
        try:
            paths = _paths(context.scene.zzz_toon)
            globals_snapshot = _globals(context.scene.zzz_toon)
            baked=next((o for o in objs if o.type=='ARMATURE' and o.get(capture_preview.STAGE)),None)
            if baked and paths['frame'] and json.loads(baked[capture_preview.STAGE])['frame']!=str(paths['frame']):
                raise ValueError("This static preview belongs to another capture. Import a fresh outfit for a different frame.")
        except (OSError, ValueError, OverflowError) as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        rep = _apply(context, objs, globals_snapshot, paths)
        self.report({"INFO"}, "ZZZ Toon: %d materials, skipped %s" % (len(rep["materials"]), rep["skipped"]))
        for message in rep.get("material_warnings", []):
            self.report({"WARNING"}, message)
        return {"FINISHED"}


class ZZZTOON_OT_light(bpy.types.Operator):
    bl_idname = "zzz_toon.key_light"
    bl_label = "Set Key Light"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        character.ensure_key_light(context.scene, tuple(context.scene.zzz_toon.key_dir))
        return {"FINISHED"}


class ZZZTOON_PT_panel(bpy.types.Panel):
    bl_label = "ZZZ Toon"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ZZZ Toon"

    def draw(self, context):
        s = context.scene.zzz_toon
        c = self.layout.column()
        tr = lambda value: wardrobe_language.text(s,value)
        c.row(align=True).prop(s,"wardrobe_language",expand=True)
        if context.scene.camera and wardrobe.KEY in context.scene.camera:
            c.operator("zzz_toon.fullscreen_preview",translate=False,text=tr("进入全屏预览"),icon="FULLSCREEN_ENTER")
            c.prop(s,"wardrobe_zoom",translate=False,text=tr("全身 ↔ 近景"),slider=True)
            c.label(translate=False,text=tr("相机视图中滚轮推拉 · 空格播放"))
        c.prop(s,"wardrobe_selection",translate=False,text=tr("角色"))
        if wardrobe_slots.input_role(s) == generic_assets.ROLE:
            generic_assets.draw(self.layout, s)
        else:
            wardrobe_slots.draw(self.layout,s)
        c=self.layout.column()
        c.prop(s,"wardrobe_output",translate=False,text=tr("工程目录"))
        c.operator("zzz_toon.configure_wardrobe",translate=False,text=tr("配置并打开服装预览"),icon="SCENE_DATA")
        if s.wardrobe_status:
            c.label(translate=False,text=tr(s.wardrobe_status))
        if s.wardrobe_result:
            c.prop(s,"wardrobe_result",translate=False,text=tr("生成的工程"))


CLASSES = (*wardrobe_slots.CLASSES, *generic_assets.CLASSES, ZZZToonSettings, wardrobe.ZZZ_OT_wardrobe_zoom, wardrobe.ZZZ_OT_wardrobe,
           fullscreen.ZZZ_OT_fullscreen, ZZZTOON_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.zzz_toon = PointerProperty(type=ZZZToonSettings)
    wardrobe.register_keys()
    fullscreen.register()
    from . import stencil_runtime
    stencil_runtime.register()
    from . import native_sampling
    native_sampling.register()


def unregister():
    fullscreen.unregister()
    wardrobe.unregister_keys()
    from . import stencil_runtime
    stencil_runtime.unregister()
    from . import native_sampling
    native_sampling.unregister()
    del bpy.types.Scene.zzz_toon
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
