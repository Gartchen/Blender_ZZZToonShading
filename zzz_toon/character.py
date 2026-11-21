"""Generic assembly: imported game prefab (FBX) + material JSONs + textures -> ZZZ toon shading.

Material inputs are read from native exports or the normalized material schema:
  * mesh material slots carry the game material names (MAT_<Char>_<Part>_UI) -> <name>.json in the material folder
  * textures come from the JSON texture slots (_MainTex, _LightTex, _OtherDataTex, _OtherDataTex2, _MatCapTex*)
  * material kind explicitly declared in normalized JSON, or inferred for legacy native material names
  * transparency from _DstBlend (10 = alpha blended), outline on/off from _Outline
  * outline width = width x vertex colour R / median(vertex colour R of the mesh)
"""
import bpy, json, os, math
import numpy as np
from mathutils import Vector
from . import core, assets, import_checks, light_profiles, native_head_sphere, native_environment, native_color, outline_runtime, native_face_layers, native_face, native_bloom, transparent_layers
from . import native_surface, native_sampling, surface_uv

DEFAULT_P = dict(core.P)          # values fitted on the outfit-screen video of the first character (Remielle)

# "GAME": reconstructed shader functions and native material parameters, plus fitted scene-light values.
# "UI_FIT": the fitted tints of the outfit screen (cool ambient shade, pink skin, light colour).
PRESETS = {
    "UI_FIT": {},
    "GAME": dict(
        light_col=(1, 1, 1, 1), face_lit_tint=(1, 1, 1, 1), skin_lit_tint=(1, 1, 1, 1),
        hair_tint=(1, 1, 1, 1), hair_shadow_tint=(1, 1, 1, 1), skin_shadow_tint=(1, 1, 1, 1),
        shadow_tint=(1, 1, 1, 1), shallow_tint=(1, 1, 1, 1), cloth_shallow_mix=0.0,
        skin_ramp_shallow_tint=(1, 1, 1, 1), skin_ramp_shadow_tint=(1, 1, 1, 1),
        face_hairshadow_tint=(1.0, 0.9, 0.9, 1.0),      # game constant (HoyoToon zzz-program.hlsl)
        iris_gamma=1.0, iris_gain=1.0, fresnel_gain=0.0,
        game_shading=True, normal_strength=1.0,
        # agent / outfit screen scene values (not in the materials, fitted on the videos)
        game_light=(1.03, 1.09, 1.14, 1.0), game_nonskin_tint=(1.0, 1.0, 1.0, 1.0),
        # v0.5: remove the old global non-skin darkening fitted on top of the
        # incorrect diffuse/specular pipeline. Keep per-material shadow colours.
        game_tint_hair=False,
        wing_tint=(1.0, 1.0, 1.0, 1.0), wing_shadow_tint=(1.0, 1.0, 1.0, 1.0), wing_glow_gain=0.0,
    ),
}
# never taken from a preset of another character: per-ID tints of Remielle's two body materials
CHAR_KEYS = ("b1_shadow_tints", "b2_shadow_tints", "sleeve_th_offset", "sleeve_lit_tint")


def set_preset(name, overrides=None):
    light_profiles.resolve((overrides or {}).get('native_light_profile'))
    native_head_sphere.validate((overrides or {}).get('native_head_sphere'))
    native_environment.validate((overrides or {}).get('native_environment'))
    native_color.validate((overrides or {}).get('native_character_lut'))
    native_bloom.validate((overrides or {}).get('native_bloom'))
    outline_runtime.validate((overrides or {}).get('native_outline'))
    native_face_layers.validate((overrides or {}).get('native_face_layers'))
    native_face.validate((overrides or {}).get('native_face'))
    transparent_layers.validate((overrides or {}).get('native_transparency'))
    native_surface.validate((overrides or {}).get('native_surface'))
    native_sampling.validate((overrides or {}).get('native_sampling'))
    if (overrides or {}).get('native_sampling') and name!='GAME':raise ValueError('Native sampling requires GAME preset')
    if (overrides or {}).get('native_surface') and name!='GAME':raise ValueError('Native surface requires GAME preset')
    core.P.clear(); core.P.update(DEFAULT_P); core.P.update(PRESETS[name]); core.P.update(overrides or {})


# ------------------------------------------------------------------ import
def import_fbx(path, uv_path=None, collection=None, repair_exports=True):
    """Import the prefab FBX (ufbx importer: the legacy one fails on these skeletons). uv_path = optional second
    export of the same prefab with all UV channels (AnimeStudio drops the vertex colours when UV2+ are enabled):
    its extra UV layers are copied onto the first import, loop by loop."""
    def imp(p, gs=1.0):
        before = set(bpy.data.objects)
        bpy.ops.wm.fbx_import(filepath=p, global_scale=gs)
        new = [o for o in bpy.data.objects if o not in before]
        ws = [o.matrix_world.to_scale().x for o in new if o.type == "ARMATURE"]
        if gs == 1.0 and ws and abs(ws[0] - 1.0) > 1e-3:      # centimetre file: re-import in metres, scale 1
            for o in new:
                data = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if data is not None and data.users == 0:
                    bpy.data.batch_remove([data])
            return imp(p, 1.0 / ws[0])
        return new

    objs = imp(path)
    if uv_path and os.path.exists(uv_path):
        extra = imp(uv_path)
        by = {o.name.split(".")[0]: o for o in objs if o.type == "MESH"}
        for o in extra:
            t = by.get(o.name.split(".")[0])
            if o.type == "MESH" and t:
                if (len(o.data.loops) != len(t.data.loops) or len(o.data.vertices) != len(t.data.vertices)
                        or len(o.data.polygons) != len(t.data.polygons)):
                    raise ValueError("UV export topology mismatch: " + o.name)
                left = np.empty(len(o.data.loops), dtype=np.int32)
                right = np.empty_like(left)
                o.data.loops.foreach_get("vertex_index", left)
                t.data.loops.foreach_get("vertex_index", right)
                if not np.array_equal(left, right):
                    raise ValueError("UV export corner order mismatch: " + o.name)
                for l in o.data.uv_layers:
                    if l.name not in t.data.uv_layers:
                        buf = np.zeros(len(o.data.loops) * 2)
                        l.data.foreach_get("uv", buf)
                        t.data.uv_layers.new(name=l.name).data.foreach_set("uv", buf)
        for o in extra:
            bpy.data.objects.remove(o, do_unlink=True)
    if collection:
        col = bpy.data.collections.get(collection) or bpy.data.collections.new(collection)
        if col.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(col)
        for o in objs:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            col.objects.link(o)
    import_checks.inspect_and_repair(objs, path, enabled=repair_exports)
    return objs


# ------------------------------------------------------------------ game data
def read_json(gname):
    try:
        p = core.find_file(core.MAT_DIRS, gname + ".json")
    except FileNotFoundError:
        return None
    data, kind = assets.load_material(p)
    sp = data["m_SavedProperties"]
    as_d = lambda v: dict(v) if isinstance(v, list) else (v or {})
    te = {}
    for k, v in as_d(sp["m_TexEnvs"]).items():
        t = v.get("m_Texture", {})
        if not t.get("IsNull", True) and t.get("Name"):
            te[k] = t["Name"]
    return dict(tex=te, floats=as_d(sp["m_Floats"]), kind=kind, params=core.params_from_data(data))


def kind_of(gname):
    n = gname.lower()
    if "eyebrow" in n:
        return "brow"
    if "eye" in n:
        return "eye"
    if "face" in n:
        return "face"
    if "hair" in n:
        return "hair"
    return "body"


def slots(me):
    if "zzz_slots" not in me:
        me["zzz_slots"] = [m.name.split(".")[0] if m else "" for m in me.materials]
    return list(me["zzz_slots"])


def vcol_ref(me):
    ca = me.color_attributes.get("Attribute")
    if ca is None or len(ca.data) == 0:
        return 1.0
    buf = np.zeros(len(ca.data) * 4); ca.data.foreach_get("color", buf)
    r = buf.reshape(-1, 4)[:, 0]
    r = r[r > 1e-3]
    return float(np.median(r)) if len(r) else 1.0


def bake_eye_lut(me, lut_name):
    """_EyeColorMap (16x16) texel per corner, picked by the vertex colour R byte -> colour attribute "zzz_eye"
    (rgb x 2, alpha).  Game: x = R & 15, y = 15 - (R >> 4), read without filtering."""
    im = core.img(lut_name, True)
    w, h = im.size
    px = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)
    ca = me.color_attributes.get("Attribute")
    n = len(me.loops)
    if ca is None or ca.domain != "CORNER" or (w, h) != (16, 16):
        return "skipped (no vertex colour / LUT is %dx%d)" % (w, h)
    buf = np.zeros(n * 4, dtype=np.float32); ca.data.foreach_get("color_srgb", buf)
    r = np.clip(np.round(buf.reshape(-1, 4)[:, 0] * 255.0), 0, 255).astype(np.int32)
    explicit = me.attributes.get('zzz_eye_index')
    if explicit is not None:
        if explicit.domain != 'CORNER' or explicit.data_type != 'FLOAT':
            raise ValueError('zzz_eye_index must be CORNER FLOAT numeric bytes')
        values = np.empty(n,dtype=np.float32); explicit.data.foreach_get('value',values)
        if not np.isfinite(values).all() or (values < -1).any() or (values > 255).any() or not np.equal(values,np.rint(values)).all():
            raise ValueError('Invalid explicit eye lookup bytes')
        known = values >= 0; r[known] = values[known].astype(np.int32)
    if explicit is None and len(set(r.tolist())) <= 2:          # unverified legacy vertex colours
        return "skipped (vertex colour R holds no LUT indices: %s)" % sorted(set(r.tolist()))
    tex = px[15 - (r >> 4), r & 15].copy()
    tex[:, :3] *= 2.0
    at = me.attributes.get("zzz_eye")
    if at is not None:
        me.attributes.remove(at)
    at = me.attributes.new("zzz_eye", "FLOAT_COLOR", "CORNER")
    at.data.foreach_set("color", tex.ravel())
    return "bytes " + str(sorted(set(r.tolist()))[:12])


def bake_surface_flags(me):
    """Native UI vertex program: hair-normal/colour flag = vertex B byte bit 5.

    Decode original sRGB storage bytes before interpolation, not the linearized
    ShaderNodeVertexColor output. Missing vertex data defaults to the body path.
    """
    ca = me.color_attributes.get("Attribute")
    values = np.zeros(len(me.loops), dtype=np.float32)
    tilt = np.zeros(len(me.loops), dtype=np.float32)
    alpha = np.zeros(len(me.loops), dtype=np.float32)
    if ca is not None and ca.domain in {"CORNER", "POINT"}:
        buf = np.zeros(len(ca.data) * 4, dtype=np.float32)
        ca.data.foreach_get("color_srgb", buf)
        byte = np.clip(np.rint(buf.reshape(-1, 4)[:, 2] * 255), 0, 255).astype(np.uint8)
        values = ((byte >> 5) & 1).astype(np.float32)
        # Native vertex program o7.y: hair flag ? 0 : vertex alpha.
        # Decode before interpolation, including triangles with mixed flags.
        alpha = buf.reshape(-1, 4)[:, 3].copy()
        tilt = (1.0 - values) * alpha
        if ca.domain == "POINT":
            indices = np.array([loop.vertex_index for loop in me.loops])
            values, tilt, alpha = values[indices], tilt[indices], alpha[indices]
    for name, data in [("zzz_surface_hair", values), ("zzz_shadow_tilt", tilt), ("zzz_vertex_alpha", alpha)]:
        old = me.attributes.get(name)
        if old is not None: me.attributes.remove(old)
        attr = me.attributes.new(name, "FLOAT", "CORNER")
        attr.data.foreach_set("value", data)


def bake_face_bit(me):
    """game vertex_face(): (vertex colour B byte & 16) -> lightmap A is used as face AO there. Corner attribute
    "zzz_facebit" (1 / 0); without vertex colours every corner gets 1."""
    ca = me.color_attributes.get("Attribute")
    n = len(me.loops)
    if ca is not None and ca.domain == "CORNER":
        buf = np.zeros(n * 4, dtype=np.float32); ca.data.foreach_get("color_srgb", buf)
        bb = np.clip(np.round(buf.reshape(-1, 4)[:, 2] * 255.0), 0, 255).astype(np.int32)
        v = ((bb & 16) > 0).astype(np.float32)
        if len(set(bb.tolist())) <= 2:
            v[:] = 1.0
    else:
        v = np.ones(n, np.float32)
    at = me.attributes.get("zzz_facebit")
    if at is not None:
        me.attributes.remove(at)
    at = me.attributes.new("zzz_facebit", "FLOAT", "CORNER")
    at.data.foreach_set("value", v)
    return float(v.mean())


def _clear_mods(ob, prefix):
    for md in list(ob.modifiers):
        if md.type == "NODES" and (md.node_group is None or md.node_group.name.startswith(prefix)):
            ob.modifiers.remove(md)


def _ids(g):
    return {s.name: s.identifier for s in g.interface.items_tree if getattr(s, "in_out", "") == "INPUT"}


def _after_armature(ob, md):
    types = [m.type for m in ob.modifiers]
    if "ARMATURE" in types:
        ob.modifiers.move(ob.modifiers.find(md.name), types.index("ARMATURE") + 1)


# ------------------------------------------------------------------ scene
def ensure_key_light(scene, direction=None):
    sun = bpy.data.objects.get("ZZZ_KeyLight")
    if sun is None:
        sun = bpy.data.objects.new("ZZZ_KeyLight", bpy.data.lights.new("ZZZ_KeyLight", "SUN"))
        scene.collection.objects.link(sun)
    sun.data.energy = math.pi          # Lambert: radiance = E / pi -> Shader to RGB returns plain N.L
    sun.data.color = (1, 1, 1)
    sun.data.angle = math.radians(2.0)
    sun.data.use_shadow = False
    sun.parent = None
    d = Vector(direction or core.P["key_world"]).normalized()
    core.P["key_world"] = tuple(d)
    sun.matrix_world = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    for mat in {slot.material for ob in scene.objects if ob.type == "MESH" for slot in ob.material_slots}:
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if "zzz_key_input" in node:
                    node.inputs[node["zzz_key_input"]].default_value = d
    return sun


def setup_scene(scene):
    """render settings the shader assumes: EEVEE, Standard view transform, black world"""
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    w = scene.world or bpy.data.worlds.new("World")
    scene.world = w
    w.use_nodes = True
    bg = next((n for n in w.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Strength"].default_value = 0.0


EYE_LAYER = "ZZZ_EyePass"
AOVS = ("zzz_hair", "zzz_eyes", "zzz_brows")


def setup_eye_pass(scene, objs):
    """Approximate eye-through-hair coverage with a second Blender view layer.
    Historical stencil interpretation (not a complete native depth/stencil replay): the face writes stencil 128 and the brows 144 (MAT_*_Face / _Eyebrows
    _StencilRef), the hair has a second "stencil pass" that is drawn alpha-blended where that stencil is set
    (HoyoToon shader, pass "Stencil Pass"). Here: a second view layer with only the face meshes (eyes, brows, skin -
    no hair) and the compositor blends it over the hair where the hair AOV and the eye / brow AOVs meet.
    objs: the character's objects; the meshes that carry an eye or brow material go to the eye layer."""
    from . import core as _c
    from . import feature_layers
    transparent_layers.clear(scene)
    if feature_layers.enabled(_c.P.get('native_face_layers')):
        layer=feature_layers.from_character(scene, objs, _c.P['native_face_layers'])
        if _c.P.get('native_transparency'):
            transparent_layers.setup(scene,{m for ob in objs if ob.type=='MESH' for m in ob.data.materials if m and m.get('zzz_native_transparent')})
        return layer
    eye_obs = [o for o in objs if o.type == "MESH" and any(
        m and m.node_tree and any(n.type == "OUTPUT_AOV" and n.aov_name in ("zzz_eyes", "zzz_brows")
                                  for n in m.node_tree.nodes) for m in o.data.materials)]
    if not eye_obs:
        return None
    col = bpy.data.collections.get(EYE_LAYER) or bpy.data.collections.new(EYE_LAYER)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    for o in eye_obs:
        if o.name not in col.objects:
            col.objects.link(o)
    main = scene.view_layers[0]
    vl = scene.view_layers.get(EYE_LAYER) or scene.view_layers.new(EYE_LAYER)
    for lc in main.layer_collection.children:          # main layer: the extra collection adds nothing
        if lc.collection == col:
            lc.exclude = True
    for lc in vl.layer_collection.children:            # eye layer: only the face meshes (+ lights in the scene root)
        lc.exclude = lc.collection != col
    vl.use_pass_combined = True
    for v in (main, vl):
        for n in AOVS:
            if n not in v.aovs:
                a = v.aovs.add(); a.name = n; a.type = "VALUE"
    return vl


def setup_compositor(scene, bloom=(0.8, 0.35, 0.6), eye_through=1.0, brow_through=1.0):
    """Compositor: eye-through-hair mix (if setup_eye_pass made the eye layer) and bloom (the game's post process;
    EEVEE has no bloom of its own). bloom None / strength 0 = no bloom. eye_through / brow_through are gains
    on hair transmittance. The explicit native UI profile adds the verified view-angle term.
    Its eye/hair mixture occurs before character LUT and opaque resolve. An explicit native
    Bloom profile replaces the legacy Glare branch; otherwise existing Bloom is retained.
    This layered approximation is not an implementation of Unity stencil/depth testing."""
    bloom_profile=native_bloom.validate(core.P.get('native_bloom'))
    tree = scene.compositing_node_group
    if tree is None:
        tree = bpy.data.node_groups.new("ZZZ_Bloom", "CompositorNodeTree")
        scene.compositing_node_group = tree
    if tree.name.split(".")[0] != "ZZZ_Bloom":
        return None                              # the scene has its own compositor: leave it alone
    if core.P.get('native_transparency'):
        transparent_layers.setup(scene,{m for ob in scene.objects if ob.type=='MESH' and not ob.hide_render
                                       for m in ob.data.materials if m and m.get('zzz_native_transparent')})
    else:
        transparent_layers.clear(scene)
    tree.nodes.clear()
    if not any(getattr(i, "in_out", "") == "OUTPUT" for i in tree.interface.items_tree):
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = tree.nodes, tree.links
    main = scene.view_layers[0]
    rl = N.new("CompositorNodeRLayers"); rl.location = (-600, 0); rl.scene = scene; rl.layer = main.name
    go = N.new("NodeGroupOutput"); go.location = (600, 0)
    src = rl.outputs["Image"]
    if core.P["game_shading"] and not core.P.get("native_face_layers"): 
        profile=core.P.get('native_character_lut')
        if profile and profile['enabled']:
            if 'zzz_lut_weight' not in rl.outputs:raise ValueError('Rebuild character materials before enabling character LUT')
            src=native_color.build(tree,src,rl.outputs['zzz_lut_weight'],rl.outputs['Alpha'],profile)
        from . import native_resolve
        src = native_resolve.build(scene, tree, rl, src, core.P)
    from . import feature_layers
    feature_mode = feature_layers.enabled(core.P.get('native_face_layers'))
    vl = scene.view_layers.get(feature_layers.LAYER if feature_mode else EYE_LAYER)
    if feature_mode and vl is not None and "zzz_hair" in rl.outputs:
        re = N.new('CompositorNodeRLayers'); re.scene = scene; re.layer = vl.name
        src = feature_layers.resolve(tree, rl, re, src)
    elif vl is not None and (eye_through > 0 or brow_through > 0) and "zzz_hair" in rl.outputs:
        re = N.new("CompositorNodeRLayers"); re.location = (-600, -400); re.scene = scene; re.layer = vl.name

        def m(op, a, b_, loc):
            n = N.new("ShaderNodeMath"); n.operation = op; n.use_clamp = True; n.location = loc
            for i, v in enumerate((a, b_)):
                if isinstance(v, float):
                    n.inputs[i].default_value = v
                else:
                    L.new(v, n.inputs[i])
            return n.outputs[0]

        fe = m("MULTIPLY", re.outputs["zzz_eyes"], float(eye_through), (-350, -300))
        fb = m("MULTIPLY", re.outputs["zzz_brows"], float(brow_through), (-350, -400))
        f = m("MULTIPLY", rl.outputs["zzz_hair"], m("ADD", fe, fb, (-200, -350)), (-50, -300))
        mx = N.new("ShaderNodeMix"); mx.data_type = "RGBA"; mx.location = (100, 0)
        L.new(f, mx.inputs[0]); L.new(src, mx.inputs[6]); L.new(re.outputs["Image"], mx.inputs[7])
        sa = N.new("CompositorNodeSetAlpha"); sa.location = (250, 0)
        L.new(mx.outputs[2], sa.inputs["Image"]); L.new(rl.outputs["Alpha"], sa.inputs["Alpha"])
        try:
            sa.inputs["Type"].default_value = "Replace Alpha"
        except Exception:
            pass
        src = sa.outputs["Image"]
    if core.P["game_shading"] and core.P.get("native_face_layers"):
        profile=core.P.get('native_character_lut')
        if profile and profile['enabled']:
            if 'zzz_lut_weight' not in rl.outputs:raise ValueError('Rebuild character materials before enabling character LUT')
            src=native_color.build(tree,src,rl.outputs['zzz_lut_weight'],rl.outputs['Alpha'],profile)
        from . import native_resolve
        src = native_resolve.build(scene, tree, rl, src, core.P)
    if core.P.get('native_transparency'):
        src=transparent_layers.resolve(scene,tree,src)
    if bloom_profile and bloom_profile['enabled']:
        src=native_bloom.build(scene,tree,src,bloom_profile)
    elif bloom and bloom[1] > 0:
        gl = N.new("CompositorNodeGlare"); gl.location = (400, 0)
        gl.inputs["Type"].default_value = "Bloom"; gl.inputs["Quality"].default_value = "High"
        gl.inputs["Threshold"].default_value = bloom[0]; gl.inputs["Smoothness"].default_value = 0.5
        gl.inputs["Strength"].default_value = bloom[1]; gl.inputs["Size"].default_value = bloom[2]
        L.new(src, gl.inputs["Image"]); src = gl.outputs["Image"]
    L.new(src, go.inputs[0])
    return tree


def setup_bloom(scene, threshold=0.8, strength=0.35, size=0.6):
    """bloom only (kept for older scripts): see setup_compositor"""
    return setup_compositor(scene, (threshold, strength, size))


# ------------------------------------------------------------------ main
def setup_character(objs, tex_dir, mat_dir, preset="UI_FIT", overrides=None, key_dir=None, scene=None,
                    skin_smooth=True, outlines=True, hide=("Weapon",)):
    """objs: the imported prefab objects (armature + meshes). Returns a report dict."""
    scene = scene or bpy.context.scene
    core.TEX_DIRS[:] = [tex_dir]; core.MAT_DIRS[:] = [mat_dir]
    if (overrides or {}).get('preview_texture_dir'):
        core.TEX_DIRS.insert(0,overrides['preview_texture_dir'])
    set_preset(preset, overrides)
    P = core.P
    rep = dict(materials={}, skipped=[], notes=[])
    if P['game_shading']:
        from . import native_resolve, native_lighting
        native_resolve.register_passes(scene)
        native_color.register_passes(scene)
        rep['native_globals'] = native_lighting.inputs(P)
        rep['native_globals_supplied'] = sorted(P.get('native_globals') or {})
        rep['native_globals_status'] = ('Explicit shader globals applied; omitted fields use preset values.'
                                        if rep['native_globals_supplied'] else 'Using preset shader globals.')
        rep['notes'].append('Native rim requires the ZZZ compositor; viewport material preview omits opaque depth resolve.')
    arm = next((o for o in objs if o.type == "ARMATURE"), None)
    sphere_center = native_head_sphere.bind(arm,scene,P.get('native_head_sphere')) if P['game_shading'] else None
    layers_head = native_face_layers.bind(arm,scene,P.get('native_face_layers')) if P['game_shading'] else None
    native_face_head=native_face.bind(arm,scene,P.get('native_face')) if P['game_shading'] else None
    receiver_coverage=None
    if P['game_shading'] and (P.get('native_face') or {}).get('receiver'):
        from . import stencil_runtime
        receiver_coverage=stencil_runtime.setup(scene,native_face_head,P['native_face']['receiver'])
    else:
        scene.pop('zzz_stencil_runtime',None)
        scene.pop('zzz_stencil_status',None)
        scene.pop('zzz_stencil_error',None)
    meshes = [o for o in objs if o.type == "MESH" and not o.get('zzz_outline_helper')]
    native_sampling.clear(meshes,scene)
    for o in meshes:
        if P["game_shading"]:
            face_restored=native_face.apply_inputs(o,P.get('native_face'),slots(o.data))
            if face_restored:rep.setdefault('native_face_inputs',{})[o.name]=face_restored
            restored = native_face_layers.apply_eye_inputs(o,P.get('native_face_layers'))
            if restored: rep.setdefault('eye_inputs',{})[o.name] = restored
            bake_surface_flags(o.data)
        if any(h.lower() in o.name.lower() for h in hide):
            o.hide_render = True; o.hide_viewport = True
    sun = ensure_key_light(scene, key_dir)
    # the FBX import leaves image datablocks with the exporter's (dead) paths under the texture names
    for im in bpy.data.images:
        if im.source == "FILE" and not os.path.exists(bpy.path.abspath(im.filepath)):
            try:
                im.filepath = core.find_file(core.TEX_DIRS, os.path.splitext(im.name)[0].split(".png")[0] + ".png")
                im.reload()
            except FileNotFoundError:
                pass

    # ---- materials
    info, built = {}, {}
    for ob in meshes:
        for g in slots(ob.data):
            if g and g not in info:
                info[g] = read_json(g)
    for g, js in info.items():
        if js is None:
            rep["skipped"].append(g); continue
        te, fl = js["tex"], js["floats"]
        D = te.get("_MainTex")
        if not D:
            rep["skipped"].append(g); continue
        prm = js["params"]
        kind = js["kind"] or kind_of(g)
        zn = "ZZZ_" + g.replace("MAT_", "")
        blend = fl.get("_DstBlend", 0) == 10
        if P["game_shading"]:
            if core.native_emission.avatar_ui(prm.get("shader_ref") or {}) and fl.get("_RenderType") == 1.:
                rep.setdefault("material_warnings", []).append(g + ": crystal variant uses the body fallback")
            mode = "BLEND" if blend else None
            maps = te.get("_OtherDataTex"), te.get("_OtherDataTex2")
            if surface_uv.opaque_matcap(prm, kind, mode, *maps):
                for index, slot in enumerate(prm.get("matcap") or [], 1):
                    if slot and slot.get("uv_fallback"):
                        rep.setdefault("material_warnings", []).append(
                            f"{g}: MatCap slot {index} uses view UV ({slot['uv_fallback']})")
            if surface_uv.supported(prm, kind, mode, *maps):
                users = [o for o in meshes if g in slots(o.data)]
                inputs = (P.get("native_surface") or {}).get("mesh_inputs", {})
                warnings, sources = surface_uv.bind(users, inputs, prm)
                rep.setdefault("surface_texcoord3", {})[g] = sources
                if warnings:
                    rep.setdefault("material_warnings", []).append(
                        g + ": primary UV fallback on " + ", ".join(warnings))
            if fl.get("_Emission", 0.) > .5 and not core.native_emission.supported(prm, kind, mode, *maps):
                rep.setdefault("material_warnings", []).append(g + ": native emission variant unsupported")
        if kind == "face" and g in (P.get('native_face') or {}).get('materials',{}):
            m = native_face.build(zn,D,te['_LightTex'],P['native_face']['materials'][g],native_face_head,core,stencil_coverage=receiver_coverage)
        elif kind == "face":
            m = core.build_toon(zn, D, None, None, None, prm, "face", face_center=None, sdf=te.get("_LightTex"))
        elif kind == "eye":
            lut = te.get("_EyeColorMap")
            m = core.build_toon(zn, D, None, None, None, prm, "eye", alpha_mode="PREMUL", iris_fx=not lut,
                                eye_lut=bool(lut))
        elif kind == "brow":
            m = core.build_toon(zn, D, None, None, None, prm, "eye", alpha_mode=P["lash_alpha_mode"])
        elif kind == "hair":
            # Selected draw variant is explicit: material JSON blend fields can
            # be overridden by the renderer (observed on the second capture).
            prm['native_hair_alpha'] = g in (P.get('native_face_layers') or {}).get('hair_materials',[])
            # the game's alpha-blended hair sub-mesh only lets the eyes / brows show through: drawn opaque here
            m = core.build_toon(zn, D, te.get("_LightTex"), te.get("_OtherDataTex"),
                                te.get("_OtherDataTex2"), prm, "hair", head_sphere_center=sphere_center, face_layers_head=layers_head)
        else:
            # NapAvatarUITransparent outputs RGB*A.R and transmittance 1-A.R
            # for _Surface=0. Blender coverage blending is equivalent for a
            # single layer; half-resolution/depth resolve is still approximate.
            native_opacity = (P["game_shading"] and blend and fl.get("_Surface", 0)==0
                              and fl.get("_SrcBlend", 1)==1 and te.get("_OtherDataTex2"))
            m = core.build_toon(zn, D, te.get("_LightTex"), te.get("_OtherDataTex"), te.get("_OtherDataTex2"), prm,
                                kind, silk_fx=False, alpha_mode="BLEND" if blend else None,
                                alpha_tex=te.get("_OtherDataTex2") if native_opacity else None,
                                const_alpha=P["gauze_alpha"] if blend and not native_opacity else None,
                                head_sphere_center=sphere_center,
                                native_transparent=g in (P.get('native_transparency') or {}).get('materials',[]))
            if native_opacity: m.use_transparency_overlap=True
        if m.get("zzz_overlay_warning"):
            rep.setdefault("material_warnings", []).append(f"{g}: {m['zzz_overlay_warning']}")
        if m.get("zzz_lut_warning"):
            rep.setdefault("material_warnings", []).append(f"{g}: {m['zzz_lut_warning']}")
        built[g] = m
        rep["materials"][g] = dict(kind=kind, blend=blend, tex=te, skin_id=fl.get("_SkinMatId"),
                                   matcap=[mc and mc["tex"] for mc in (prm["matcap"] or [])])
        if kind not in {"face","eye","brow","hair"}:
            rep["materials"][g]['opacity_source']='auxiliary.red' if native_opacity else ('legacy_constant' if blend else 'opaque')
    for ob in meshes:
        for i, g in enumerate(slots(ob.data)):
            if g in built:
                ob.data.materials[i] = built[g]
            if g in built and rep["materials"][g]["kind"] == "face":
                rep["notes"].append("%s: face bit 16 on %.0f %% of the corners" % (ob.name, 100 * bake_face_bit(ob.data)))
            if g in built and rep["materials"][g]["kind"] == "eye" and info[g]["tex"].get("_EyeColorMap"):
                res = bake_eye_lut(ob.data, info[g]["tex"]["_EyeColorMap"])
                rep["notes"].append("%s: eye LUT %s" % (ob.name, res))
                if res.startswith("skipped"):      # no vertex colours in this export: the inferred alpha formula
                    core.build_toon("ZZZ_" + g.replace("MAT_", ""), info[g]["tex"]["_MainTex"], None, None, None,
                                    info[g]["params"], "eye", alpha_mode="PREMUL", iris_fx=True, eye_lut=False)

    # ---- per mesh: lid shadow, skin smoothing, baked outline normals, outline hull
    ogn = core.outline_gn() if outlines else None
    sgn = core.skin_smooth_gn() if (skin_smooth and P["skin_smooth_level"] > 0) else None
    hgn = None
    face_ob = None
    for ob in meshes:
        me = ob.data
        sl = slots(me)
        first = next((g for g in sl if g in built), None)
        outline_runtime.clear(ob)
        _clear_mods(ob, "ZZZ_SkinSmooth"); _clear_mods(ob, "ZZZ_Outline"); _clear_mods(ob, "ZZZ_HullNormal")
        if first is None:
            continue
        kind = rep["materials"][first]["kind"]
        js = info[first]; te = js["tex"]; prm = js["params"]
        idmap = te.get("_OtherDataTex") if kind == "body" else None

        if kind == "face":
            face_ob = ob
            fi = sl.index(first)
            if "zzz_eyeshadow_slot" not in me and "UV0" in me.uv_layers:
                # lid shadow: loose navy triangles of the shared face layout (UV0 box), drawn as a multiply overlay
                uv0 = me.uv_layers["UV0"].data
                hit = [p for p in me.polygons if p.material_index == fi and
                       0.62 < sum(uv0[i].uv.x for i in p.loop_indices) / p.loop_total < 0.77 and
                       0.04 < sum(uv0[i].uv.y for i in p.loop_indices) / p.loop_total < 0.10]
                if hit:
                    me.materials.append(core.eye_shadow_mat(te["_MainTex"]))
                    me["zzz_eyeshadow_slot"] = len(me.materials) - 1
                    me["zzz_eyeshadow_source"] = fi
                    for p in hit:
                        p.material_index = me["zzz_eyeshadow_slot"]
                rep["notes"].append("%s: %d lid-shadow faces" % (ob.name, len(hit)))

        if sgn and idmap and js["floats"].get("_SkinMatId", 0) == 0:   # the GN group tests ID-map slot 1 (R > 0.85)
            md = ob.modifiers.new("ZZZ_SkinSmooth", "NODES"); md.node_group = sgn
            ids = _ids(sgn)
            core.set_gn_input(md, ids["Level"], P["skin_smooth_level"])
            core.set_gn_input(md, ids["Strength"], P["skin_smooth_strength"])
            core.set_gn_input(md, ids["ID Map"], core.img(idmap, True))

        if not outlines or kind in ("eye", "brow") or js["floats"].get("_Outline", 1) < 0.5:
            continue
        if P.get('native_outline') and kind != 'face':
            if outline_runtime.apply(ob,scene,te,prm,P['native_outline'],P):
                rep['notes'].append(ob.name+': native VS4/PS12 outline; explicit mesh inputs and scene snapshot')
                continue
            rep['notes'].append(ob.name+': legacy outline retained; verified native mesh inputs unavailable')
        baked = False
        for layer in ("UV3", "UV2"):
            if layer in me.uv_layers:
                res = core.bake_outline_normals(ob, layer)
                if "vertices" in res:
                    hgn = hgn or core.hull_normal_gn()
                    md = ob.modifiers.new("ZZZ_HullNormal", "NODES"); md.node_group = hgn
                    _after_armature(ob, md)
                    baked = True
                    rep["notes"].append("%s: outline normals from %s (%s)" % (ob.name, layer, res))
                    break
        if kind == "face":
            om = core.build_outline("ZZZ_Outline_" + ob.name, te["_MainTex"], prm["OutlineColor"][0],
                                    P["outline_dark"] * 1.1, game=P["outline_game_color"])
            width = P["outline_face"]
        else:
            om = core.build_outline("ZZZ_Outline_" + ob.name, te["_MainTex"], prm["OutlineColor"], P["outline_dark"],
                                    te.get("_OtherDataTex"), game=True if kind == "hair" else P["outline_game_color"])
            width = P["outline_hair"] if kind == "hair" else P["outline_body"]
        width *= js["floats"].get("_OutlineWidth", 1.0)
        if om.name not in [x.name for x in me.materials if x]:
            me.materials.append(om)
        md = ob.modifiers.new("ZZZ_Outline", "NODES"); md.node_group = ogn
        ids = _ids(ogn)
        core.set_gn_input(md, ids["Width"], width)
        core.set_gn_input(md, ids["Material"], om)
        core.set_gn_input(md, ids["Only Material Index"], sl.index(first) if kind == "face" else -1)
        core.set_gn_input(md, ids["Z Offset"], P["outline_zoffset_hair"] if kind == "hair" else 0.0)
        core.set_gn_input(md, ids["Baked Normal"], 1.0 if (baked and P["outline_baked_normal"]) else 0.0)
        core.set_gn_input(md, ids["View Plane"], P["outline_view_plane"])
        core.set_gn_input(md, ids["Vertex Colour Ref"], vcol_ref(me))
        if idmap:
            core.set_gn_input(md, ids["ID Map"], core.img(idmap, True))
            core.set_gn_input(md, ids["Skin Scale"], P["outline_skin_scale"])

    if P.get('native_surface'):
        rep['native_surface']=native_surface.apply(meshes,scene,P['native_surface'])

    if P.get('native_sampling'):
        rep['native_sampling']=native_sampling.apply(meshes,scene,P['native_sampling'])

    # ---- face: SDF vectors from the head bone, hair shadow from the game's caster mesh
    if face_ob is not None:
        col = face_ob.users_collection[0]
        hd = bpy.data.objects.get("ZZZ_HeadDir_" + face_ob.name) or bpy.data.objects.new("ZZZ_HeadDir_" + face_ob.name, None)
        if hd.name not in col.objects:
            col.objects.link(hd)
        hd.empty_display_type = "ARROWS"; hd.empty_display_size = 0.1; hd.hide_render = True
        head = arm and next((b for b in arm.data.bones if b.name.endswith("Head") and "Bip" in b.name), None)
        if head:
            hd.parent = arm; hd.parent_type = "BONE"; hd.parent_bone = head.name
            hd.matrix_parent_inverse.identity(); hd.matrix_basis.identity()
        else:
            rep["notes"].append("no head bone found: face SDF uses the object axes")
        _clear_mods(face_ob, "ZZZ_FaceVectors")
        fv = core.face_vectors_gn()
        md = face_ob.modifiers.new("ZZZ_FaceVectors", "NODES"); md.node_group = fv
        ids = _ids(fv)
        core.set_gn_input(md, ids["Head"], hd); core.set_gn_input(md, ids["Sun"], sun)
        _after_armature(face_ob, md)

        sun.light_linking.receiver_collection = core.ll_coll("ZZZ_LL_NotFace", [face_ob], "EXCLUDE")
        hs = next((o for o in meshes if "hairshadow" in o.name.lower()), None)
        for old in [o for o in bpy.data.objects if o.name.startswith("ZZZ_HairShadowLight")]:
            bpy.data.objects.remove(old)
        if hs is not None:
            hs.hide_viewport = False; hs.hide_render = False
            hs.visible_camera = False
            hs.visible_diffuse = hs.visible_glossy = hs.visible_transmission = False
            hs.visible_volume_scatter = False
            hs.visible_shadow = True
            _clear_mods(hs, "ZZZ_CasterOffset")
            cg = core.caster_offset_gn()
            md = hs.modifiers.new("ZZZ_CasterOffset", "NODES"); md.node_group = cg
            core.set_gn_input(md, next(s.identifier for s in cg.interface.items_tree if s.name == "Offset"),
                              P["hair_shadow_offset"])
            recv = core.ll_coll("ZZZ_LL_FaceOnly", [face_ob], "INCLUDE")
            block = core.ll_coll("ZZZ_LL_HairShadowCaster", [hs], "INCLUDE")
            for scn in bpy.data.scenes:
                cam = scn.camera
                if cam is None or face_ob.name not in scn.objects:
                    continue
                nm = "ZZZ_HairShadowLight_" + cam.name
                if nm in bpy.data.lights:
                    bpy.data.lights.remove(bpy.data.lights[nm])
                hl = bpy.data.objects.new(nm, bpy.data.lights.new(nm, "SPOT"))
                scn.collection.objects.link(hl)
                hl.parent = cam; hl.matrix_parent_inverse.identity(); hl.matrix_basis.identity()
                hl.data.energy = core.HS_POWER; hl.data.color = (1, 1, 1)
                hl.data.shadow_soft_size = 0.0; hl.data.spot_size = math.radians(70.0); hl.data.spot_blend = 0.0
                try:
                    hl.data.use_soft_falloff = False
                except AttributeError:
                    pass
                hl.data.use_shadow = True
                hl.light_linking.receiver_collection = recv
                hl.light_linking.blocker_collection = block
        else:
            rep["notes"].append("no HairShadow mesh: no hair shadow on the face")
    return rep
