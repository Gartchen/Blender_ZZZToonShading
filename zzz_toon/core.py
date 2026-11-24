"""ZZZ toon shading - node builders.  EEVEE shader and outline construction.

Texture channel layout (from the game materials):
  _D  _MainTex        RGB albedo, A alpha (face / eyes)
  _N  _LightTex       RG tangent normal XY, B shadow bias (0.5 neutral)
  _M  _OtherDataTex   R material ID (1.0/0.7/0.5/0.3/0.0 -> slot 1..5), G metallic, B specular mask
  _A  _OtherDataTex2  R misc, G rim width, B MatCap mask
"""
import bpy, json, os, math
import numpy as np
from . import native_lighting, material_inputs, light_profiles, native_head_sphere, native_environment, native_face_layers, native_color, native_emission, surface_uv
from mathutils import Vector

TEX_DIRS = []      # folders searched for <name>.png
MAT_DIRS = []      # folders searched for <material>.json


def find_file(dirs, fn):
    for d in dirs:
        p = os.path.join(d, fn)
        if os.path.exists(p):
            return p
    low = fn.lower()                       # the game is not consistent in case (Eff_MatCap / Eff_Matcap)
    for d in dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.lower() == low:
                    return os.path.join(d, f)
    raise FileNotFoundError("%s not found in %s" % (fn, dirs))


# ---------------------------------------------------------------- tunables
P = dict(
    key_world=(0.8099, -0.4173, 0.4121),   # world direction TOWARD the key light. v5: fitted on the close-up with the
    # region statistics (zzz_cu_tools.py): 66 deg to the camera's right, 25 deg up. The left sleeve / left hair are in
    # shade in the game, so the light is a side light; the frontal _OverrideMainLight* vectors stored in the hair
    # material were tested and do not match (override is off).
    light_col=(1.01, 1.045, 1.065, 1.0),   # lit = albedo * this. v4: x0.95 - the fitted bloom adds the rest on top
    iris_gamma=0.80, iris_gain=1.08,       # iris: out = col ** 0.8 * 1.08 (fitted on the video iris median)
    lash_alpha_mode=None,                  # None = opaque (game), "DITHER" = old
    face_lit_tint=(0.97, 0.99, 1.04, 1.0), # face skin reads cooler than the body in the close-up (251,235,238)
    hair_tint=(1.00, 0.875, 0.83, 1.0),       # v8: fitted on the close-up VIDEO (hair is brighter / more coral there
    # than in the screenshot: median 236,153,160 vs 225,143,160), colour-class percentiles over 4 frames
    skin_shadow_tint=(0.957, 0.94, 1.0, 1.0),   # v3: skin shadows pink, not peach. v18: G 0.902 -> 0.94, measured on the
    # video's chest shadow patch with the hard terminator: video 242/216/220 and 244/207/210, ours was 242/211/219
    # and 244/203/210
    hair_shadow_tint=(0.69, 0.745, 0.865, 1.0),  # v8: pairs with hair_tint (video p10 190,111,127)
    shadow_tint=(0.36, 0.50, 0.72, 1.0),   # v4: cloth shadow, whole-region percentiles vs the close-up screenshot
    shallow_tint=(0.64, 0.76, 0.88, 1.0),  # half-way for the transition band
    shadow_th=0.20, shadow_soft=0.15,   # lit/shadow terminator on N.L + LightTex.B bias (v5, with the side key light)
    shallow_th=-0.35, shallow_soft=0.05,  # shallow->deep shadow
    # Body_1 shadow tint per material ID (1 skin - unused, skin has its own; 2 corset / bust / collar; 3 tassels,
    # ruffle, ribbon; 4 metal; 5 sleeves), multiplied onto _ShadowColor[ID]; fitted on the close-up screenshot
    # v13: ID2 (0.80, 0.83, 0.97) -> (0.63, 0.72, 0.94): on the VIDEO the corset waist is a lavender shade
    # (185,189,222) against a bright bust; too light a shade made the waist read wide
    b1_shadow_tints=((1.0, 1.0, 1.0), (0.58, 0.71, 0.94), (0.65, 0.77, 0.98), (0.84, 0.91, 1.0), (0.50, 0.60, 1.0)),
    # v19: R of IDs 2-4, of the global cloth tint and of wing_tint lowered ~7 %: with the sleeves no longer forced into
    # shade the cloth mid tones read too red against the video (cloth colour diff 4.3 -> 3.8, score 84.7 -> 84.9)
    # Body_2: IDs 1-4 keep the global cloth tint, ID5 = sleeves (lavender (165,175,228) in the screenshot)
    b2_shadow_tints=((0.36, 0.50, 0.72), (0.36, 0.50, 0.72), (0.36, 0.50, 0.72), (0.36, 0.50, 0.72), (0.46, 0.57, 0.90)),
    sleeve_th_offset=0.0,              # Body_2 ID5 (sleeves): terminator shift. v4-v18: 0.20 ("mostly in shade", fitted
    # on the full-body screenshot). v19: in the close-up video the sleeve is lit with one shadow band underneath and
    # the cuff ruffle is white; cloth percentiles of the sleeve body (v17/reg.py, p50 / p90): video 181,199,241 /
    # 209,231,254; offset 0.20: 183,194,237 / 211,222,252; offset 0: 188,199,240 / 220,232,255 -> G and B fit with 0,
    # R is then too high, which is what the score punished in v18 (cloth colour diff 3.0 -> 5.0)
    sleeve_lit_tint=(0.86, 0.99, 1.0, 1.0),   # v19: lit sleeve is cyan-white in the video (R 209 where ours gave 220)
    backface_shadow=0.0, backface_d=-0.2,   # v13: back faces to the shadow tone - tested on the video, no effect (cloth dL 18.1 -> 18.0): off
    ao_bias=1.0,                        # weight of the LightTex.B shadow bias
    cloth_rim_lo=0.35, cloth_rim_hi=0.85, cloth_rim_gain=0.0,   # v6: wide edge light on cloth - tested, residual got worse (9.0 -> 12.3): off
    skin_shadow_th=0.11, skin_shadow_soft=0.03,   # v18: the skin terminator is a HARD toon edge in the video (a
    # flat shadow patch on the left breast and in the cleavage, edge 3-4 px wide); with 0.03 the patch has the
    # video's outline. v3's 0.20 (fitted on the screenshot's region statistics) rendered it as a smooth gradient
    # v19 th 0.18 -> 0.13: shadow share of the chest skin (v17/sk.py, 2 frames) video 35 %, 0.18: 37 %, 0.10: 34 %
    # v20 cloth: same ramp structure as the skin. 4-frame score: old two-step 85.5 (cloth colour diff 3.9);
    # ramp with mix 0.5 / w 0.6: 85.1; mix 0.2 / w 1.0: 85.9 (2.1); + step 0.2 / lit_w 0.3: 86.1 (1.5)
    cloth_ramp=True, cloth_ramp_lit_w=0.30, cloth_ramp_shadow_w=1.0, cloth_ramp_step=0.20,
    cloth_shallow_mix=0.2,             # shallow tint = 1 - mix + mix x shadow tint (0 = the game's _ShallowColor as is)
    hw_ramp=False, hair_ramp_th=0.08, hw_ramp_lit_w=0.30, hw_ramp_shadow_w=1.0, hw_ramp_step=0.2,   # v21 hair / wings
    face_ramp=True, face_ramp_lit_w=0.06, face_ramp_shadow_w=0.25, face_ramp_step=0.35,             # v21 face (SDF units)
    # v20 body skin ramp (see build_toon): lit -> shallow over [th, th + lit_w], shallow -> shadow over
    # [th - shadow_w, th + step] of the signed N.L; tints multiply the game's _ShallowColor / _ShadowColor (ID1)
    # fitted on two chest profiles of the video (v17/prof.py, frame 100, y 672 / 700): 8-frame score 84.9 -> 85.4,
    # skin colour diff 1.5 -> 0.8, skin IoU 0.723 -> 0.763
    skin_ramp_lit_w=0.15, skin_ramp_shadow_w=0.60, skin_ramp_step=0.35, skin_ramp_edge=0.01,
    skin_ramp_shallow_tint=(0.99, 1.0, 1.02, 1.0), skin_ramp_shadow_tint=(0.95, 0.92, 0.985, 1.0),
    hair_shadow_th=0.08, hair_shadow_soft=0.30,   # v3: hair shading is a soft gradient, less area in shadow
    normal_strength=0.6,
    spec_th=5.0, spec_soft=0.04, spec_gain=0.35,   # v5: cloth toon specular off (no highlight blob on the bust in game)
    rim_lo=0.62, rim_hi=0.70, rim_gain=0.04,   # v6: 0.12 drew a light edge over the outlines (game edges are dark lines)
    metal_spec_lo=0.68, metal_spec_hi=0.90, metal_spec_gain=0.20,   # v6: metal highlight band (N.H), x _SpecularColor x albedo
    skin_lit_tint=(1.0, 1.04, 1.045, 1.0),      # v6: lit body skin pinker / whiter (chest 249,235,238 in the screenshot)
    outline_body=0.0006, outline_hair=0.0006, outline_face=0.0005, outline_wing=0.0013,
    outline_hair_color=(0.64, 0.62, 0.61, 1.0),   # v17: hair line = albedo x this. Video: a hair line is the hair colour
                                       # times ~0.8 (sRGB) in all channels, 1-2 px wide - not the purple tint that
                                       # _OutlineColor (1, 0.80, 0.97) x 0.45 gave (line G/B 106/121 on 173/173)
    outline_baked_normal=True,         # v17: hull along the game's baked outline normal (UV3), see
                                       # scripts/zzz_outline_normals.py
    skin_smooth_level=2,               # v17: Phong tessellation of the skin (ZZZ_SkinSmooth), subdivision level
    skin_smooth_strength=0.5,          # 0.5 = edge midpoints on the circle through their vertices. Left-shoulder
                                       # contour vs video (v17/shc.py, 4 frames): raw mesh rms 1.14 px, 0.75: 2.21
                                       # (bulges), 0.5: 1.53, 0.5 + twist -0.15: 0.87
    outline_skin_scale=1.0,            # v16: hull width on skin (ID map R > 0.85). Video: the line where skin meets
                                       # the background / other skin is ~1 px (lw.py 1.05 px, ours was 1.50)
    outline_vcol_ref_body=0.2196,      # game: width x vertex colour R; body R = 56/255 nearly everywhere, 0.12 on
                                       # the chest ruffles, 0 on a few hidden faces
    hair_t_alpha=False,                # True = bang sub-mesh (MAT_Remielle_Hair_T_UI, queue 2100) alpha-blended with
                                       # Hair_A.R over everything behind it. v17 test: the lower bang goes dark
                                       # (241/161/162, back hair shows through) where the video stays 255/176/174 and
                                       # opaque gives 255/174/174 -> the game only lets the eyes / brows through;
                                       # Hair_A is used for that in the close-up compositor (AOV zzz_thru)
    # v21: the game's outline (HoyoToon reconstruction, ref/HoyoToon-ZZZ): colour formula, view-plane extrusion,
    # hair width x vertex colour R (1 on the hair, 0 on 630 corners = no line there)
    outline_game_color=True, outline_game_gain=1.0, outline_view_plane=1.0, outline_vcol_ref_hair=1.0,
    outline_dark=0.34,                 # outline = albedo * _OutlineColor[ID] * this
    outline_floor=0.08,
    silk_skin=(1.0, 0.80, 0.74, 1.0), silk_lo=0.10, silk_hi=0.55, silk_amount=0.22,
    fresnel_pow=3.5, fresnel_gain=0.15,  # blue edge (_FresnelColor 0,0.2,1.62); v4: the halo itself comes from the bloom
    wing_glow=(0.80, 0.92, 1.0), wing_glow_gain=0.0, wing_tint=(0.78, 0.90, 0.89, 1.0),
    # v22: video wings are mint (208,239,240) on BOTH sides; ours were blue and dark on the shaded side (182,219,242)
    # because the cloth shadow tint was multiplied onto the wing's own _ShadowColor (0.80,0.80,0.85)
    wing_shadow_tint=(1.0, 1.0, 1.0, 1.0),
    gauze_alpha=0.85,                  # Body_1_T (skirt gauze) opacity: lavender, mostly opaque in the video
    gauze_tex_alpha=False,             # multiply by _D.a (_UseAlpha 0 on the _T_UI material)
    face_sdf_soft=0.02,                # SDF terminator half-width
    hair_shadow_lo=0.35, hair_shadow_hi=0.65,   # face visibility (shadowed N.L / N.L) -> hair shadow edge
    face_hairshadow_tint=(0.90, 0.84, 0.89, 1.0),   # v16: shadow tint inside the hair shadow (video band median
                                       # 231,195,197 on lit skin 252,237,238); skin_shadow_tint elsewhere
    hair_shadow_offset=(-0.0057, -0.0026, 0.0),   # v16: the game shifts the HairShadow caster mesh by a constant
                                       # (_XOffset 0.8, _YOffset 1.5 in MAT_HairShadow_UI) and stencils it on the
                                       # face: the shadow is the caster's silhouette moved by a fixed screen offset,
                                       # the same width along every strand. Here: camera-space metres (x right,
                                       # y up), fitted on the video: its shadows sit 10 px left / 4.5 px below the
                                       # strands in all three test frames (fsoff.py); ours measures the same.
    nose_k=0.01,                       # nose-line UV slide per unit of view offset (x _NoseLine*Disp); slides
                                       # away from the camera. Close-up ref (view 7 deg off-axis) fits ~0: small
    nose_dark=(0.36, 0.22, 0.27, 1.0), # nose line = skin * this
    nose_spec=0.6,                     # nose-tip highlight (lightmap B) blend toward white
    hair_spec_gain=0.10,               # Hair_M.B painted highlight strokes x _SpecularColor x albedo. v15: the
                                       # material's own _SpecIntensity (0.1). 0.30 gave 1300 cream-yellow pixels on
                                       # the crown where the video has ~0 (its streaks are only ~10 levels brighter)
    outline_zoffset_hair=0.0,          # game: _MaxOutlineZOffset 0.01 x (vertex colour B - 0.5), hair B = 1. v17: 0.
                                       # Any push-back makes a line between two strands depend on their depth gap,
                                       # so it comes and goes while the hair moves; the video's bang lines stay.
                                       # Line density in the bang (v17/hl.py): video 24.2 %, z 0: 22.5, 0.003: 22.0,
                                       # 0.006: 21.5 (v15's 0.006 was fitted while the lines were too dark and wide)
    eye_shadow=0.35,                   # lid-shadow overlay strength (multiply toward the navy texels)
    matcap_gain=1.0,                   # global scale on the game MatCap contribution
    # v24: the game's own shading functions (HoyoToon reconstruction, ref/HoyoToon-ZZZ/zzz-common.hlsl) instead of
    # the fitted ramps, for body / hair / wing materials:
    #   shadow_body(): s = N.L + 2 (2 LightTex.B - 1); shadow below -1/3, shallow gradient -1/3..1/3, a thin SSS band
    #   and the "front" tint above 1/3 (band widths from _AlbedoSmoothness); colours _ShallowColor[ID] / _ShadowColor[ID]
    #   x the six _Post*Tint SCENE globals (not stored in the materials: HoyoToon's defaults below, the outfit screen
    #   has its own -> the only fitted part)
    #   specular(): toon GGX x _OtherDataTex.B x _SpecularColor[ID], F0 = lerp(0.04, colour, metallic); the diffuse
    #   part of the same function (colour x (1 - 0.96 metallic)) darkens the metal IDs
    #   ndotv_rim(): Fresnel rim x _RimGlowLightColor[ID] x _UISunColor[ID]
    native_globals={},  # Explicit unresolved native runtime inputs; see native_lighting.py.
    game_shading=False,
    post_shallow=(0.9569, 0.9608, 0.9020), post_shallow_fade=(0.8745, 0.8, 0.7922), post_shadow=(0.8510, 0.7804, 0.7725),
    post_front=(1.0, 0.9961, 0.9294), post_sss=(1.0, 0.9482, 0.9294),
    game_light=(1.0, 1.0, 1.0, 1.0),   # CalcLighting: colour x scene light colour
    game_rim_gain=1.0, game_spec_gain=1.0, game_metal=0.96,
    game_sat=1.0,                      # weight of the colour curve at the head of specular(): c -> c ^ p toward the
    # shade (p = 1.44 + 0.29 lum in full shade, 1 in full light) - deeper, more saturated mid tones and shadows
    # scene part, fitted on the agent-screen video (Velina close-up, colour-class percentiles): with HoyoToon's
    # default _Post*Tint the SKIN shadow already matches (video shadow / lit = 0.80, 0.74, 0.73, formula 0.83, 0.75,
    # 0.74); cloth and hair shadows are cooler and darker there (0.61, 0.63, 0.67 against 0.77, 0.71, 0.73)
    game_nonskin_tint=(1.0, 1.0, 1.0, 1.0),
    game_tint_hair=True,               # also on the hair
)
HS_POWER = 4.0 * math.pi ** 2 * 10.0   # hair-shadow spot at the camera: white diffuse = 10 * N.V / r^2


# ---------------------------------------------------------------- helpers
def img(name, noncolor=False):
    fn = name + ".png"
    if 'wardrobe_texture_files' in P:
        from .wardrobe_slots import load_image
        return load_image(P, fn, noncolor)
    path = find_file(TEX_DIRS, fn)
    im = bpy.data.images.load(bpy.path.abspath(path), check_existing=True)
    im.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    im.alpha_mode = "CHANNEL_PACKED"
    return im


def load_params(jname):
    """Material JSON -> per-ID colours + MatCap slots. Prefers the *_UI material (outfit screen)."""
    p = find_file(MAT_DIRS, jname + ".json")
    d = json.load(open(p, encoding="utf-8"))
    out = params_from_data(d)
    out["file"] = os.path.basename(p)
    return out


def params_from_data(d):
    """Build shader parameters from the canonical material schema (native or normalized)."""
    sp = d["m_SavedProperties"]
    co = sp["m_Colors"]; co = dict(co) if isinstance(co, list) else co
    fl = sp["m_Floats"]; fl = dict(fl) if isinstance(fl, list) else fl
    te = sp["m_TexEnvs"]; te = dict(te) if isinstance(te, list) else te

    def c(k, default=(1, 1, 1)):
        v = co.get(k)
        return (v["r"], v["g"], v["b"], 1.0) if v else (*default, 1.0)

    out = {"color_encoding": d.get("zzz_color_encoding", "native-property")}
    out["shader_ref"] = d.get("m_Shader")
    for key in ("ShadowColor", "ShallowColor", "SpecularColor", "OutlineColor", "RimGlowLightColor", "FresnelColor",
                "UISunColor"):
        out[key] = [c("_" + key + ("" if i == 1 else str(i))) for i in range(1, 6)]
    out["floats"] = fl
    out["Color"] = c("_Color")
    if fl.get("_Emission", 0.0) > 0.5:
        out["EmissionColor"] = [c("_EmissionColor" + suffix) for suffix in ("", "2", "3", "4", "5")]
    # PS12 uses outline alpha as saturation input. Keep legacy RGBA unchanged
    # and expose the original alpha explicitly for the opt-in native path.
    def rgba(k):
        v = co.get(k)
        return tuple(v[x] for x in 'rgba') if v else (1.,1.,1.,1.)
    out['NativeOutlineColor'] = [rgba('_OutlineColor'+('' if i==1 else str(i))) for i in range(1,6)]
    out['NativeMaterialColor'] = rgba('_Color')
    # MatCap slot i (= material ID slot i, M.R 1.0/0.7/0.5/0.3/0.0):
    #   mc = matcap(view normal) * _MatCapColorTint * _MatCapColorBurst
    #   BlendMode 1: add  mc * a     BlendMode 0: lerp(col, mc, a)     a = sat(_MatCapAlphaBurst) * mask
    #   mask = _A.B when _UseMatCapMask (1 exactly on the metal / trim IDs).  Inferred from the values:
    #   Body_1 slot4 047 add with ColorBurst 0 (no-op), Body_2 slot5 pearl at AlphaBurst 1.94 must be masked.
    mcs = []
    if fl.get("_MatCap", 0) > 0.5:
        for i in range(1, 6):
            s = "" if i == 1 else str(i)
            t = (te.get("_MatCapTex" + s) or {}).get("m_Texture", {})
            if t.get("IsNull", True) or not t.get("Name"):
                mcs.append(None); continue
            slot = dict(tex=t["Name"], mode=int(fl.get("_MatCapBlendMode" + s, 0)),
                        cb=fl.get("_MatCapColorBurst" + s, 1.0), ab=fl.get("_MatCapAlphaBurst" + s, 1.0),
                        tint=c("_MatCapColorTint" + s))
            if fl.get("_MatCapRefract" + s, 0.) > .5:
                if any(fl.get(key + s, 0.) != 0. for key in ("_MatCapUSpeed", "_MatCapVSpeed")):
                    slot["uv_fallback"] = "scroll requires game shader time"
                elif "_RefractParam" + s not in co or "_RefractDepth" + s not in fl:
                    slot["uv_fallback"] = "missing refraction transform or depth"
                else:
                    slot["refraction"] = (rgba("_RefractParam" + s), fl["_RefractDepth" + s])
            mcs.append(slot)
    out["matcap"] = mcs if any(mcs) else None
    out["use_mask"] = fl.get("_UseMatCapMask", 0) > 0.5
    return out


class NB:
    """tiny node-building helper"""
    def __init__(self, mat):
        self.nt = mat.node_tree
        self.n = self.nt.nodes
        self.l = self.nt.links
        self.x = 0

    def node(self, t, x=None, y=0, **kw):
        nd = self.n.new(t)
        nd.location = (x if x is not None else self.x, y)
        for k, v in kw.items():
            setattr(nd, k, v)
        return nd

    def link(self, a, b):
        self.l.new(a, b)

    def math(self, op, a, b=None, y=0, clamp=False, x=None):
        nd = self.node("ShaderNodeMath", x=x, y=y, operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                nd.inputs[i].default_value = v
            else:
                self.link(v, nd.inputs[i])
        return nd.outputs[0]

    def smooth(self, v, lo, hi, y=0, x=None):
        nd = self.node("ShaderNodeMapRange", x=x, y=y, interpolation_type="SMOOTHSTEP", clamp=True)
        self.link(v, nd.inputs["Value"])
        nd.inputs["From Min"].default_value = lo
        nd.inputs["From Max"].default_value = hi
        return nd.outputs["Result"]

    def mixc(self, fac, a, b, blend="MIX", y=0, x=None, clamp_factor=True):
        nd = self.node("ShaderNodeMix", x=x, y=y, data_type="RGBA", blend_type=blend,
                       clamp_result=False, clamp_factor=clamp_factor)
        ins = [i for i in nd.inputs if i.enabled]
        # enabled inputs: Factor, A(color), B(color)
        fac_in, a_in, b_in = nd.inputs[0], nd.inputs[6], nd.inputs[7]
        for sock, v in ((fac_in, fac), (a_in, a), (b_in, b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif isinstance(v, tuple):
                sock.default_value = v if len(v) == 4 else (*v, 1.0)
            else:
                self.link(v, sock)
        return nd.outputs[2]

    def rgb(self, col, y=0, x=None):
        nd = self.node("ShaderNodeRGB", x=x, y=y)
        nd.outputs[0].default_value = col if len(col) == 4 else (*col, 1.0)
        return nd.outputs[0]

    def tex(self, im, y=0, x=None, uv=None):
        nd = self.node("ShaderNodeTexImage", x=x, y=y)
        nd.image = im
        nd.interpolation = "Linear"
        return nd


def id_select(b, R, colors, y):
    """pick per-material-ID colour (slots: R≈1.0,0.7,0.5,0.3,0.0)."""
    if P["game_shading"]:
        out = b.rgb(colors[0], y=y)
        for th, col in zip((.8, .6, .4, .2), colors[1:]):
            out = b.mixc(b.math("LESS_THAN", R, th, y=y), out, b.rgb(col, y=y), y=y)
        return out
    out = b.rgb(colors[4], y=y)
    for th, col in ((0.15, colors[3]), (0.40, colors[2]), (0.60, colors[1]), (0.85, colors[0])):
        f = b.math("GREATER_THAN", R, th, y=y)
        out = b.mixc(f, out, b.rgb(col, y=y), y=y)
    return out


def id_scalar(b, R, params, key, default, y):
    """Scalar selection with the reference shader's 0.2/0.4/0.6/0.8 ID boundaries."""
    values = [float(params.get(key + ("" if i == 1 else str(i)), default)) for i in range(1, 6)]
    if R is None or len(set(values)) == 1:
        return values[0]
    result = values[0]
    for threshold, value in zip((0.8, 0.6, 0.4, 0.2), values[1:]):
        factor = b.math("LESS_THAN", R, threshold, y=y)
        mix = b.node("ShaderNodeMix", data_type="FLOAT", y=y)
        for sock, v in ((mix.inputs[0], factor), (mix.inputs[2], result), (mix.inputs[3], value)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            else:
                b.link(v, sock)
        result = mix.outputs[0]
    return result


def native_ui_shadow_signal(b, normal, light, ndl, y):
    """UI fragment 156: vertex-alpha tilt in Unity Y-up (= Blender world Z)."""
    sep = b.node("ShaderNodeSeparateXYZ", y=y); b.link(normal, sep.inputs[0])
    attr = b.node("ShaderNodeAttribute", y=y); attr.attribute_name = "zzz_shadow_tilt"
    # Keep light-Z in a tagged vector node so Set Key Light updates the formula.
    light_node = b.node("ShaderNodeVectorMath", operation="ADD", y=y)
    light_node.inputs[0].default_value = (0, 0, 0)
    if isinstance(light, tuple):
        light_node.inputs[1].default_value = light; light_node["zzz_key_input"] = 1
    else:
        b.link(light, light_node.inputs[1])
    lsep = b.node("ShaderNodeSeparateXYZ", y=y); b.link(light_node.outputs[0], lsep.inputs[0])
    ny, ly = sep.outputs[2], lsep.outputs[2]
    profile = light_profiles.resolve(P.get("native_light_profile"))
    if profile:
        def vertical(value):
            n = b.node("ShaderNodeVectorMath", operation="DOT_PRODUCT", y=y)
            b.link(value, n.inputs[0]); n.inputs[1].default_value = profile["up"]
            return n.outputs["Value"]
        ny, ly = vertical(normal), vertical(light_node.outputs[0])
    ly_socket = b.math("MINIMUM", b.math("MULTIPLY", ly, 3.0, y=y), 1.0, y=y)
    offset = b.math("SUBTRACT", 1.5, b.math("MULTIPLY", ly_socket, .5, y=y), y=y)
    slope = b.math("ADD", ny, offset, y=y, clamp=True)
    tilted = b.math("SUBTRACT", b.math("MULTIPLY", b.math("ADD", ndl, 1.0, y=y), slope, y=y), 1.0, y=y)
    return b.math("ADD", ndl, b.math("MULTIPLY", attr.outputs["Fac"], b.math("SUBTRACT", tilted, ndl, y=y), y=y), y=y)


def native_ui_shadow_color(b, color, y):
    """Native UI fragment 156: hue-preserving near tint -> raw tint by distance.

    Uses Euclidean camera distance in the imported game metre scale. This is a
    material computation, not an exposure adjustment or camera-position fit.
    """
    safe = b.node("ShaderNodeVectorMath", operation="ADD", y=y)
    b.link(color, safe.inputs[0]); safe.inputs[1].default_value = (2**-14,) * 3
    mean = b.node("ShaderNodeVectorMath", operation="DOT_PRODUCT", y=y)
    b.link(safe.outputs[0], mean.inputs[0]); mean.inputs[1].default_value = (.333330005,) * 3
    div = b.node("ShaderNodeVectorMath", operation="SCALE", y=y)
    b.link(safe.outputs[0], div.inputs[0]); b.link(b.math("DIVIDE", 1.0, mean.outputs['Value'], y=y), div.inputs['Scale'])
    lo = b.node("ShaderNodeVectorMath", operation="MAXIMUM", y=y)
    b.link(div.outputs[0], lo.inputs[0]); lo.inputs[1].default_value = (0,0,0)
    hi = b.node("ShaderNodeVectorMath", operation="MINIMUM", y=y)
    b.link(lo.outputs[0], hi.inputs[0]); hi.inputs[1].default_value = (1,1,1)
    camera = b.node("ShaderNodeCameraData", y=y)
    weight = b.math("MULTIPLY", camera.outputs['View Distance'], .437249988, y=y, clamp=True)
    return b.mixc(weight, hi.outputs[0], safe.outputs[0], y=y)


def native_ui_surface(b, color, normal, material_id, params, y, light, *, ps1119_normals=False):
    """NapAvatarUI's pre-light surface transform, native fragments 152/154/156.

    zzz_surface_hair is vertex B byte bit 5, decoded before interpolation by
    character.bake_surface_flags. It is not inferred from character/material names.
    Runtime head-sphere lighting and scene globals are separate unresolved inputs.
    """
    geo = b.node("ShaderNodeNewGeometry", y=y)
    flag = b.node("ShaderNodeAttribute", y=y); flag.attribute_name = "zzz_surface_hair"
    # The native fragment branches on interpolated v7.z > .5; do not blend
    # two different colour curves across triangles with mixed vertex flags.
    is_hair = b.math("GREATER_THAN", flag.outputs["Fac"], .5, y=y)
    vertex_normal = geo.outputs["Normal"]
    if ps1119_normals:
        # The colour curve reads VS44's vertex normal before face orientation.
        op = native_lighting.Ops(b)
        vertex_normal = op.scale(vertex_normal, op.sub(1., op.mul(2., geo.outputs["Backfacing"])))
        vertex_normal.node.label = "PS1119 unflipped surface normal"
    chosen = b.mixc(is_hair, vertex_normal, normal, y=y)
    def dot(u, v):
        n = b.node("ShaderNodeVectorMath", operation="DOT_PRODUCT", y=y)
        b.link(u, n.inputs[0])
        if isinstance(v, tuple):
            n.inputs[1].default_value = v; n["zzz_key_input"] = 1
        else: b.link(v, n.inputs[1])
        return n.outputs["Value"]
    ndl, ndg = dot(normal, light), dot(chosen, light)
    d = b.math("SUBTRACT", ndg, ndl, y=y)
    q = b.math("MULTIPLY_ADD", d, -3.0, y=y, clamp=True); q.node.inputs[2].default_value = 1.0
    q = b.math("MULTIPLY", b.math("POWER", q, 1.5, y=y), 2.0, y=y, clamp=True)
    half = b.math("MULTIPLY_ADD", ndl, .5, y=y); half.node.inputs[2].default_value = .5
    yn = b.math("MULTIPLY", b.math("ADD", b.math("MAXIMUM", ndl, 0.0, y=y, clamp=True),
                                  b.math("MULTIPLY", half, q, y=y), y=y), .5, y=y)
    lum = dot(color, (0.29, 0.60, 0.11)); lum.node.pop("zzz_key_input", None)
    power = b.math("MULTIPLY_ADD", lum, .2875, y=y); power.node.inputs[2].default_value = 1.4375
    power = b.math("ADD", power, b.math("MULTIPLY", yn, b.math("SUBTRACT", 1.0, power, y=y), y=y), y=y)
    sep = b.node("ShaderNodeSeparateColor", y=y); b.link(color, sep.inputs[0])
    largest = b.math("MAXIMUM", b.math("MAXIMUM", sep.outputs[0], sep.outputs[1], y=y), sep.outputs[2], y=y)
    largest = b.math("MAXIMUM", largest, 1.0, y=y)
    body = b.node("ShaderNodeCombineColor", y=y); hair = b.node("ShaderNodeCombineColor", y=y)
    hair_pow = b.math("MULTIPLY_ADD", lum, .4, y=y); hair_pow.node.inputs[2].default_value = 1.0
    hair_mul = b.math("MULTIPLY_ADD", lum, -.05, y=y); hair_mul.node.inputs[2].default_value = 1.05
    for i in range(3):
        b.link(b.math("POWER", b.math("DIVIDE", sep.outputs[i], largest, y=y), power, y=y), body.inputs[i])
        b.link(b.math("MULTIPLY", b.math("POWER", sep.outputs[i], hair_pow, y=y), hair_mul, y=y), hair.inputs[i])
    weight = b.math("MULTIPLY_ADD", b.math("MAXIMUM", ndg, 0.0, y=y, clamp=True), .5, y=y)
    weight.node.inputs[2].default_value = .5
    curved = b.mixc(is_hair, b.mixc(weight, color, body.outputs[0], y=y), hair.outputs[0], y=y)
    if material_id is not None:
        slot = b.math("MAXIMUM", b.math("SUBTRACT", 4.0, b.math("FLOOR", b.math("MULTIPLY", material_id, 5.0, y=y), y=y), y=y), 0.0, y=y)
        skin = b.math("COMPARE", slot, float(params['floats'].get('_SkinMatId', 0)), y=y)
        skin.node.inputs[2].default_value = .1
        curved = b.mixc(skin, curved, color, y=y)
    return b.mixc(P["game_sat"], color, curved, y=y)


# ---------------------------------------------------------------- material builder
def build_toon(name, D, N=None, M=None, A=None, params=None, kind="body", alpha_mode=None,
               face_center=None, const_alpha=None, obj=None, sdf=None, silk_fx=True, id_tints=None, id5_offset=0.0, iris_fx=False,
               alpha_tex=None, id5_lit=None, eye_lut=False, head_sphere_center=None, face_layers_head=None,
               native_transparent=False):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    b = NB(mat)
    game = bool(P["game_shading"]) and kind in ("body", "hair", "wing")
    prm = material_inputs.prepare(params, game)
    flags = (prm or {}).get("floats", {})
    emission = game and native_emission.supported(prm, kind, alpha_mode, M, A)
    ps1119_opaque = game and surface_uv.opaque_matcap(prm, kind, alpha_mode, M, A)
    lut_weight = 1.0
    mat.pop('zzz_lut_warning', None)
    mat.pop('zzz_native_transparent', None)
    mat.pop('zzz_surface_tbn_v34', None)
    if native_transparent:
        if not game or alpha_mode != 'BLEND' or alpha_tex is None:
            raise ValueError('Native transparent shader requires GAME, BLEND and auxiliary opacity')
        if prm['floats'].get('_Surface',0.) != 0. or prm['floats'].get('_Emission',0.) >= .5:
            raise ValueError('Unsupported transparent surface/emission branch')
        if any(prm['floats'].get(k,0.) > .5 for k in ('_DoubleSided','_SymmetryUV')):
            raise ValueError('Transparent secondary UV routing is not implemented')
        if prm['floats'].get('_Cull',0.) not in (0.,2.):
            raise ValueError('Transparent front-face culling is not implemented')
        mat['zzz_native_transparent'] = True
    mat["zzz_material_color_encoding"] = (params or {}).get("color_encoding", "native-property")
    if game and P.get("native_light_profile"):
        mat["zzz_native_light_snapshot"] = json.dumps(P["native_light_profile"], sort_keys=True)
    else:
        mat.pop("zzz_native_light_snapshot", None)
    native_light = light_profiles.direction(b, P) if game else None
    if native_light is None:
        native_light = tuple(Vector(P["key_world"]).normalized())
    game_face = bool(P["game_shading"]) and kind == "face" and bool(sdf)
    s_face = None
    # game (Material_Detect): the skin slot is _SkinMatId (0-based slot of the ID map: R 1.0 / 0.7 / 0.5 / 0.3 / 0.0)
    skin_slot = int(round((prm or {}).get("floats", {}).get("_SkinMatId", 0)))

    def skin_test(x=None, y=0):
        if not 0 <= skin_slot <= 4:
            return b.math("MULTIPLY", R, 0.0, x=x, y=y)
        if game:
            slot = b.math("MAXIMUM", b.math("SUBTRACT", 4.0, b.math("FLOOR", b.math("MULTIPLY", R, 5.0, y=y), y=y), y=y), 0.0, y=y)
            cmp = b.math("COMPARE", slot, float(skin_slot), x=x, y=y)
            cmp.node.inputs[2].default_value = .1
            return cmp
        lo_, hi_ = [(0.85, 9.0), (0.60, 0.85), (0.40, 0.60), (0.15, 0.40), (-9.0, 0.15)][skin_slot]
        return b.math("MULTIPLY", b.math("GREATER_THAN", R, lo_, x=x, y=y), b.math("LESS_THAN", R, hi_, x=x, y=y - 40),
                      x=x, y=y)

    # ---- textures
    b.x = -1600
    tD = b.tex(img(D), y=400)
    base = tD.outputs["Color"]
    alpha = tD.outputs["Alpha"]
    ao = None
    nrm = None
    if N:
        tN = b.tex(img(N, True), y=0)
        sep = b.node("ShaderNodeSeparateColor", x=-1300, y=0)
        b.link(tN.outputs["Color"], sep.inputs[0])
        # reconstruct Z from XY: z = sqrt(1 - x^2 - y^2)
        bias = 1.004 if game else 1.0
        xs = b.math("SUBTRACT", b.math("MULTIPLY", sep.outputs[0], 2.0, x=-1150, y=80), bias, x=-1000, y=80)
        ys = b.math("SUBTRACT", b.math("MULTIPLY", sep.outputs[1], 2.0, x=-1150, y=20), bias, x=-1000, y=20)
        if game:
            bump = prm["floats"].get("_BumpScale", 1.0)
            xs = b.math("MULTIPLY", xs, bump, x=-900, y=80)
            ys = b.math("MULTIPLY", ys, bump, x=-900, y=20)
        s =b.math("ADD", b.math("MULTIPLY", xs, xs, x=-850, y=80), b.math("MULTIPLY", ys, ys, x=-850, y=20), x=-700, y=50)
        z = b.math("SQRT", b.math("SUBTRACT", 1.0, s, x=-550, y=50, clamp=True), x=-400, y=50)
        zc = b.math("MULTIPLY_ADD", z, 0.5, x=-250, y=50)
        zc.node.inputs[2].default_value = 0.5
        comb = b.node("ShaderNodeCombineColor", x=-100, y=0)
        if game:
            for value, sock in [(xs, comb.inputs[0]), (ys, comb.inputs[1])]:
                encoded = b.math("MULTIPLY_ADD", value, .5, x=-250, y=30)
                encoded.node.inputs[2].default_value = .5; b.link(encoded, sock)
        else:
            b.link(sep.outputs[0], comb.inputs[0]); b.link(sep.outputs[1], comb.inputs[1])
        b.link(zc, comb.inputs[2])
        nm = b.node("ShaderNodeNormalMap", x=50, y=0)
        nm.inputs["Strength"].default_value = P["normal_strength"]
        encoded = comb.outputs[0]
        if ps1119_opaque:
            # EEVEE flips the full tangent-space perturbation on a backface.
            # PS1119 changes the sign of N*z and retains T*x and B*y.
            op = native_lighting.Ops(b)
            back = op.v("MULTIPLY_ADD", encoded, (-1., -1., 1.))
            back.node.inputs[2].default_value = (1., 1., 0.)
            adapt = b.node("ShaderNodeMix", data_type="VECTOR")
            adapt["zzz_ps1119_normal_map"] = True
            adapt.label = "PS1119 tangent-space backface"
            b.link(b.node("ShaderNodeNewGeometry").outputs["Backfacing"], adapt.inputs[0])
            b.link(encoded, adapt.inputs[4])
            b.link(back, adapt.inputs[5])
            encoded = adapt.outputs[1]
        b.link(encoded, nm.inputs["Color"])
        nrm = nm.outputs[0]
        # LightTex.B = shadow bias (0.5 neutral, lower = shadow-prone): terminator at N.L + ao_bias * (2B - 1) = th.
        # (v5: was a 0.18..0.44 on/off multiplier, which ignored the 0.3-0.45 values that cover most cloth)
        ao = b.math("MULTIPLY", b.math("SUBTRACT", b.math("MULTIPLY", sep.outputs[2], 2.0, x=-250, y=-150), 1.0, x=-100, y=-150), P["ao_bias"], x=50, y=-150)
    R = Gm = Bm = None
    if M:
        tM = b.tex(img(M, True), y=-350)
        sm = b.node("ShaderNodeSeparateColor", x=-1300, y=-350)
        b.link(tM.outputs["Color"], sm.inputs[0])
        R, Gm, Bm = sm.outputs[0], sm.outputs[1], sm.outputs[2]
    silk = rimw = None
    if A:
        tA = b.tex(img(A, True), y=-650)
        sa = b.node("ShaderNodeSeparateColor", x=-1300, y=-650)
        b.link(tA.outputs["Color"], sa.inputs[0])
        silk = sa.outputs[2]
        rimw = sa.outputs[1]

    primary_uv = None
    if game and surface_uv.supported(prm, kind, alpha_mode, M, A) and prm.get("surface_uv3_available", True):
        # VS44 passes TEXCOORD0 and TEXCOORD3. PS1119 uses the latter
        # for all four surface textures on backfaces when symmetry is off.
        primary = b.node("ShaderNodeUVMap", uv_map="UV0")
        back_uv = prm.get("surface_uv3_name", "UV3")
        if back_uv == surface_uv.ATTRIBUTE:
            secondary = b.node("ShaderNodeAttribute", attribute_name=back_uv).outputs["Vector"]
        else:
            secondary = b.node("ShaderNodeUVMap", uv_map=back_uv).outputs[0]
        facing = b.node("ShaderNodeNewGeometry").outputs["Backfacing"]
        primary_uv = primary.outputs[0]
        if prm.get("mixed_surface_uv3"):
            available = b.node("ShaderNodeAttribute", attribute_type="OBJECT", attribute_name="zzz_ps1119_has_uv3")
            facing = b.math("MULTIPLY", facing, available.outputs["Fac"])
            fallback = b.node("ShaderNodeMix", data_type="VECTOR")
            b.link(available.outputs["Fac"], fallback.inputs[0])
            b.link(b.node("ShaderNodeTexCoord").outputs["UV"], fallback.inputs[4])
            b.link(primary_uv, fallback.inputs[5])
            primary_uv = fallback.outputs[1]
        route = b.node("ShaderNodeMix", data_type="VECTOR")
        b.link(facing, route.inputs[0])
        b.link(primary_uv, route.inputs[4])
        b.link(secondary, route.inputs[5])
        route.label = "PS1119 front TEXCOORD0 / back TEXCOORD3"
        route["zzz_two_sided_uv"] = True
        route["zzz_back_uv"] = back_uv
        route["zzz_partial_uv3"] = bool(prm.get("mixed_surface_uv3"))
        for tex in ([tD, tM, tA, tN] if N else [tD, tM, tA]):
            b.link(route.outputs[1], tex.inputs["Vector"])

    # ---- face: spherical normals around head centre (clean anime face shading)
    if kind == "face" and face_center is not None:
        tc = b.node("ShaderNodeTexCoord", x=-600, y=250)
        sub = b.node("ShaderNodeVectorMath", x=-450, y=250, operation="SUBTRACT")
        b.link(tc.outputs["Object"], sub.inputs[0])
        sub.inputs[1].default_value = face_center
        sc = b.node("ShaderNodeVectorMath", x=-300, y=250, operation="MULTIPLY")
        b.link(sub.outputs[0], sc.inputs[0]); sc.inputs[1].default_value = (1.0, 1.6, 0.9)  # flatten front/back
        # rotation-only object matrix: VECTOR transform (NORMAL type flipped the result in EEVEE here)
        vt = b.node("ShaderNodeVectorTransform", x=-150, y=250, vector_type="VECTOR",
                    convert_from="OBJECT", convert_to="WORLD")
        b.link(sc.outputs[0], vt.inputs["Vector"])
        nn = b.node("ShaderNodeVectorMath", x=0, y=250, operation="NORMALIZE")
        b.link(vt.outputs[0], nn.inputs[0])
        nrm = nn.outputs[0]

    # ---- lighting terms (lights only; world contributes nothing)
    diff = b.node("ShaderNodeBsdfDiffuse", x=250, y=200)
    if nrm is not None:
        b.link(nrm, diff.inputs["Normal"])
    s2r = b.node("ShaderNodeShaderToRGB", x=420, y=200)
    b.link(diff.outputs[0], s2r.inputs[0])
    bw = b.node("ShaderNodeRGBToBW", x=580, y=200)
    b.link(s2r.outputs[0], bw.inputs[0])
    d = bw.outputs[0]
    if ao is not None:
        d = b.math("ADD", d, ao, x=720, y=200)

    if kind == "body" and P["backface_shadow"] > 0:
        # back faces (sleeve / skirt linings seen through the openings): Blender flips the shading normal toward the
        # viewer, so linings were lit like the outside. The game's toon shader keeps the mesh normal on back faces
        # (two-sided, no flip), which puts linings in the shadow tone - in the close-up video the sleeve linings
        # beside the waist are dark lavender and that is what makes the waist read slim.
        gbf = b.node("ShaderNodeNewGeometry", x=560, y=380)
        bfm = b.node("ShaderNodeMix", x=720, y=380, data_type="FLOAT")
        b.link(b.math("MULTIPLY", gbf.outputs["Backfacing"], P["backface_shadow"], x=640, y=440), bfm.inputs[0])
        b.link(d, bfm.inputs[2]); bfm.inputs[3].default_value = P["backface_d"]
        d = bfm.outputs[0]
    if R is not None and kind == "body" and id5_offset:
        # sleeves (Body_2 ID5, M.R < 0.15) sit mostly in shade in the game: shift their terminator
        d = b.math("SUBTRACT", d, b.math("MULTIPLY", b.math("LESS_THAN", R, 0.15, x=720, y=320), id5_offset,
                                         x=800, y=320), x=840, y=240)
    th_, so_ = (P["hair_shadow_th"], P["hair_shadow_soft"]) if kind == "hair" else (P["shadow_th"], P["shadow_soft"])
    lit = b.smooth(d, th_ - so_, th_ + so_, x=870, y=260)
    if kind in ("body", "wing"):
        # cloth: wide view-dependent edge light. In the game close-up, cloth facing the camera sits in the lavender
        # shadow tone and only a broad band along the silhouette is white (sleeve: lavender middle, white edges;
        # bust: white top edge fading to lavender) - the white comes from this rim, not from the key light.
        lwc = b.node("ShaderNodeLayerWeight", x=700, y=450)
        lwc.inputs["Blend"].default_value = 0.5
        if nrm is not None:
            b.link(nrm, lwc.inputs["Normal"])
        rimf = b.math("MULTIPLY", b.smooth(lwc.outputs["Facing"], P["cloth_rim_lo"], P["cloth_rim_hi"], x=870, y=450),
                      P["cloth_rim_gain"], x=1000, y=450)
        lit = b.math("MAXIMUM", lit, rimf, x=1100, y=380)
    shal = b.smooth(d, P["shallow_th"] - P["shallow_soft"], P["shallow_th"] + P["shallow_soft"], x=870, y=120)
    if R is not None and kind == "body":
        # skin (material ID1). v20: the game's three-tone ramp. The material has no ramp texture (_CharacterRampTex
        # empty, _RampSource 0): the ramp is lit -> _ShallowColor -> _ShadowColor. Measured across the chest terminator
        # of the video (v17/prof.py): lit 251,240,238 -> soft band ending at x(0.99,0.96,0.97) = _ShallowColor
        # (1,0.957,0.949) -> gradient down to x(0.94,0.87,0.90) = _ShadowColor (0.945,0.898,0.898).
        # The gradient continues where N.L < 0 (the game ramps on a signed / half Lambert), which Shader-to-RGB
        # clamps away: a second diffuse BSDF on the negated normal gives the negative half, ds = signed N.L.
        if nrm is not None:
            nsrc = nrm
        else:
            nsrc = b.node("ShaderNodeNewGeometry", x=100, y=-50).outputs["Normal"]
        ngv = b.node("ShaderNodeVectorMath", x=250, y=-50, operation="SCALE")
        b.link(nsrc, ngv.inputs[0]); ngv.inputs["Scale"].default_value = -1.0
        diffn = b.node("ShaderNodeBsdfDiffuse", x=400, y=-50)
        b.link(ngv.outputs[0], diffn.inputs["Normal"])
        s2rn = b.node("ShaderNodeShaderToRGB", x=550, y=-50)
        b.link(diffn.outputs[0], s2rn.inputs[0])
        bwn = b.node("ShaderNodeRGBToBW", x=700, y=-50)
        b.link(s2rn.outputs[0], bwn.inputs[0])
        ds = b.math("SUBTRACT", d, bwn.outputs[0], x=850, y=-50)
        th_s = P["skin_shadow_th"]
        lit_skin = b.smooth(ds, th_s, th_s + P["skin_ramp_lit_w"], x=1000, y=330)
        # shallow -> shadow: a crisp step of size skin_ramp_step at the terminator (the visible toon edge), the rest
        # as a gradient over skin_ramp_shadow_w
        j_ = P["skin_ramp_step"]
        shal_skin = b.math("ADD",
                           b.math("MULTIPLY", b.smooth(ds, th_s - P["skin_ramp_shadow_w"], th_s, x=1000, y=-50), 1.0 - j_, x=1100, y=-50),
                           b.math("MULTIPLY", b.smooth(ds, th_s - P["skin_ramp_edge"], th_s + P["skin_ramp_edge"], x=1000, y=-150), j_, x=1100, y=-150),
                           x=1200, y=-100)
        is_skin_ = skin_test(x=870, y=400)
        if P["cloth_ramp"]:
            # v20: the same three-tone ramp on the cloth IDs (lit -> _ShallowColor[ID] -> _ShadowColor[ID] x tint)
            th_c, jc = P["shadow_th"], P["cloth_ramp_step"]
            lit = b.smooth(ds, th_c, th_c + P["cloth_ramp_lit_w"], x=1000, y=450)
            shal = b.math("ADD",
                          b.math("MULTIPLY", b.smooth(ds, th_c - P["cloth_ramp_shadow_w"], th_c, x=1000, y=520), 1.0 - jc, x=1100, y=520),
                          b.math("MULTIPLY", b.smooth(ds, th_c - 0.01, th_c + 0.01, x=1000, y=590), jc, x=1100, y=590),
                          x=1200, y=550)
        lit = b.mixc(is_skin_, lit, lit_skin, x=1100, y=300)
        shal = b.mixc(is_skin_, shal, shal_skin, x=1100, y=120)
    if kind in ("hair", "wing") and P["hw_ramp"]:
        # v21: the same three-tone ramp on hair and wings (the game runs every material through one shadow function)
        if nrm is not None:
            nsrc = nrm
        else:
            nsrc = b.node("ShaderNodeNewGeometry", x=100, y=-50).outputs["Normal"]
        ngv = b.node("ShaderNodeVectorMath", x=250, y=-50, operation="SCALE")
        b.link(nsrc, ngv.inputs[0]); ngv.inputs["Scale"].default_value = -1.0
        diffn = b.node("ShaderNodeBsdfDiffuse", x=400, y=-50)
        b.link(ngv.outputs[0], diffn.inputs["Normal"])
        s2rn = b.node("ShaderNodeShaderToRGB", x=550, y=-50)
        b.link(diffn.outputs[0], s2rn.inputs[0])
        bwn = b.node("ShaderNodeRGBToBW", x=700, y=-50)
        b.link(s2rn.outputs[0], bwn.inputs[0])
        ds = b.math("SUBTRACT", d, bwn.outputs[0], x=850, y=-50)
        th_h = P["hair_ramp_th"] if kind == "hair" else P["shadow_th"]
        jh = P["hw_ramp_step"]
        lit = b.smooth(ds, th_h, th_h + P["hw_ramp_lit_w"], x=1000, y=450)
        shal = b.math("ADD",
                      b.math("MULTIPLY", b.smooth(ds, th_h - P["hw_ramp_shadow_w"], th_h, x=1000, y=520), 1.0 - jh, x=1100, y=520),
                      b.math("MULTIPLY", b.smooth(ds, th_h - 0.01, th_h + 0.01, x=1000, y=590), jh, x=1100, y=590),
                      x=1200, y=550)

    # ---- face: SDF lightmap (Female_Face_Lightmap_02: R = SDF, A = where the SDF applies)
    # FRONT / RIGHT / SUN come from the ZZZ_FaceVectors GN modifier (head bone empty + key light),
    # all in the face object's space. Light from the character's right samples the mirrored lightmap.
    if sdf:
        def attr(nm, yy):
            a_ = b.node("ShaderNodeAttribute", x=-600, y=yy, attribute_type="GEOMETRY", attribute_name=nm)
            return a_.outputs["Vector"]
        F_, R_, S_ = attr("zzz_front", 1100), attr("zzz_right", 1000), attr("zzz_sun", 900)
        fdl = b.node("ShaderNodeVectorMath", x=-400, y=1100, operation="DOT_PRODUCT")
        b.link(F_, fdl.inputs[0]); b.link(S_, fdl.inputs[1])
        rdl = b.node("ShaderNodeVectorMath", x=-400, y=1000, operation="DOT_PRODUCT")
        b.link(R_, rdl.inputs[0]); b.link(S_, rdl.inputs[1])
        flip = b.math("GREATER_THAN", rdl.outputs["Value"], 0.0, x=-250, y=1000)
        uvn = b.node("ShaderNodeUVMap", x=-600, y=800, uv_map="UV0")
        sxy = b.node("ShaderNodeSeparateXYZ", x=-450, y=800)
        b.link(uvn.outputs[0], sxy.inputs[0])
        u2 = b.node("ShaderNodeMix", x=-250, y=850, data_type="FLOAT")
        b.link(flip, u2.inputs[0]); b.link(sxy.outputs[0], u2.inputs[2])
        b.link(b.math("SUBTRACT", 1.0, sxy.outputs[0], x=-350, y=750), u2.inputs[3])
        cxy = b.node("ShaderNodeCombineXYZ", x=-100, y=800)
        b.link(u2.outputs[0], cxy.inputs[0]); b.link(sxy.outputs[1], cxy.inputs[1])
        tL = b.tex(img(sdf, True), x=50, y=800)
        tL.extension = "EXTEND"
        b.link(cxy.outputs[0], tL.inputs[0])
        sl_ = b.node("ShaderNodeSeparateColor", x=300, y=800)
        b.link(tL.outputs["Color"], sl_.inputs[0])
        thr = b.math("MULTIPLY_ADD", fdl.outputs["Value"], -0.5, x=-250, y=1100)   # 0.5 - 0.5 * F.S
        thr.node.inputs[2].default_value = 0.5
        lo = b.math("SUBTRACT", thr, P["face_sdf_soft"], x=450, y=900)
        hi = b.math("ADD", thr, P["face_sdf_soft"], x=450, y=850)
        mr = b.node("ShaderNodeMapRange", x=600, y=850, interpolation_type="SMOOTHSTEP", clamp=True)
        b.link(sl_.outputs[0], mr.inputs["Value"]); b.link(lo, mr.inputs["From Min"]); b.link(hi, mr.inputs["From Max"])
        wa = tL.outputs["Alpha"]
        # outside the SDF area (lightmap A < 1) fall back to N.L of the key direction: the key lamp itself does
        # not light the face (light linking), so take N from the geometry and L from zzz_sun
        gk = b.node("ShaderNodeNewGeometry", x=-800, y=1150)
        nko = b.node("ShaderNodeVectorTransform", x=-650, y=1150, vector_type="VECTOR",
                     convert_from="WORLD", convert_to="OBJECT")
        b.link(gk.outputs["Normal"], nko.inputs["Vector"])
        nkn = b.node("ShaderNodeVectorMath", x=-500, y=1150, operation="NORMALIZE")
        b.link(nko.outputs[0], nkn.inputs[0])
        dk = b.node("ShaderNodeVectorMath", x=-350, y=1150, operation="DOT_PRODUCT")
        b.link(nkn.outputs[0], dk.inputs[0]); b.link(S_, dk.inputs[1])
        dkey = b.math("MAXIMUM", dk.outputs["Value"], 0.0, x=-200, y=1150)
        lit = b.smooth(dkey, P["shadow_th"] - P["shadow_soft"], P["shadow_th"] + P["shadow_soft"], x=870, y=1150)
        shal = b.smooth(dkey, P["shallow_th"] - P["shallow_soft"], P["shallow_th"] + P["shallow_soft"], x=870, y=1100)
        lit_f = shal_f = mr.outputs["Result"]
        if game_face:
            # game vertex_face(): only the horizontal (xz in Unity) parts of the head vectors and the light count
            upv_ = b.node("ShaderNodeVectorMath", x=-500, y=1250, operation="CROSS_PRODUCT")
            b.link(R_, upv_.inputs[0]); b.link(F_, upv_.inputs[1])
            su_ = b.node("ShaderNodeVectorMath", x=-350, y=1250, operation="DOT_PRODUCT")
            b.link(S_, su_.inputs[0]); b.link(upv_.outputs[0], su_.inputs[1])
            sc_ = b.node("ShaderNodeVectorMath", x=-200, y=1250, operation="SCALE")
            b.link(upv_.outputs[0], sc_.inputs[0]); b.link(su_.outputs["Value"], sc_.inputs["Scale"])
            sh_ = b.node("ShaderNodeVectorMath", x=-50, y=1250, operation="SUBTRACT")
            b.link(S_, sh_.inputs[0]); b.link(sc_.outputs[0], sh_.inputs[1])
            shn = b.node("ShaderNodeVectorMath", x=100, y=1250, operation="NORMALIZE"); b.link(sh_.outputs[0], shn.inputs[0])
            fdh = b.node("ShaderNodeVectorMath", x=250, y=1250, operation="DOT_PRODUCT")
            b.link(F_, fdh.inputs[0]); b.link(shn.outputs[0], fdh.inputs[1])
            w_ = b.math("MULTIPLY_ADD", fdh.outputs["Value"], -0.5, x=400, y=1250); w_.node.inputs[2].default_value = 0.5
            fm = b.math("MULTIPLY_ADD", sl_.outputs[0], 0.9, x=450, y=1180); fm.node.inputs[2].default_value = 0.1
            mrg = b.node("ShaderNodeMapRange", x=600, y=1250, interpolation_type="SMOOTHSTEP", clamp=True)
            b.link(fm, mrg.inputs["Value"])
            b.link(b.math("SUBTRACT", w_, 0.5, x=500, y=1300), mrg.inputs["From Min"])
            b.link(b.math("ADD", w_, 0.5, x=500, y=1350), mrg.inputs["From Max"])
            fb_ = b.node("ShaderNodeAttribute", x=450, y=1450, attribute_type="GEOMETRY", attribute_name="zzz_facebit")
            ao_f = b.mixc(fb_.outputs["Fac"], (1, 1, 1, 1), tL.outputs["Alpha"], x=600, y=1450)
            stp = b.math("MULTIPLY", mrg.outputs["Result"], ao_f, x=750, y=1300)
            s_face = b.math("MULTIPLY_ADD", stp, 2.0, x=850, y=1300); s_face.node.inputs[2].default_value = -1.0
        if P["face_ramp"]:
            # v21: three-tone ramp on the SDF value (game: shadow_face() feeds the SDF result to the same ramp)
            mr1 = b.node("ShaderNodeMapRange", x=600, y=1000, interpolation_type="SMOOTHSTEP", clamp=True)
            b.link(sl_.outputs[0], mr1.inputs["Value"]); b.link(thr, mr1.inputs["From Min"])
            b.link(b.math("ADD", thr, P["face_ramp_lit_w"], x=450, y=1000), mr1.inputs["From Max"])
            mr2 = b.node("ShaderNodeMapRange", x=600, y=700, interpolation_type="SMOOTHSTEP", clamp=True)
            b.link(sl_.outputs[0], mr2.inputs["Value"]); b.link(thr, mr2.inputs["From Max"])
            b.link(b.math("SUBTRACT", thr, P["face_ramp_shadow_w"], x=450, y=700), mr2.inputs["From Min"])
            jf = P["face_ramp_step"]
            lit_f = mr1.outputs["Result"]
            shal_f = b.math("ADD", b.math("MULTIPLY", mr2.outputs["Result"], 1.0 - jf, x=750, y=700),
                            b.math("MULTIPLY", mr.outputs["Result"], jf, x=750, y=760), x=850, y=730)
        lit = b.mixc(wa, lit, lit_f, x=750, y=600)
        shal = b.mixc(wa, shal, shal_f, x=750, y=500)

        # ---- hair shadow on the face. Game: the HairShadow caster mesh is drawn shifted by a constant offset
        # into the stencil and the face takes its shadow colour there. Here the caster is shifted the same way
        # (GN modifier ZZZ_CasterOffset, camera space) and a spot light sitting AT the camera (light-linked to the
        # face, blocked only by the caster) tests it: light rays = view rays, so the shadow is exactly the shifted
        # caster's screen silhouette. White diffuse = HS_POWER / (4 pi^2) * N.V / r^2 * visibility.
        geo_ = b.node("ShaderNodeNewGeometry", x=-800, y=1300)
        ndl = b.node("ShaderNodeVectorMath", x=-350, y=1300, operation="DOT_PRODUCT")
        b.link(geo_.outputs["Normal"], ndl.inputs[0]); b.link(geo_.outputs["Incoming"], ndl.inputs[1])
        camd = b.node("ShaderNodeCameraData", x=-650, y=1420)
        r2 = b.math("MULTIPLY", camd.outputs["View Distance"], camd.outputs["View Distance"], x=-450, y=1420)
        ratio = b.math("DIVIDE", b.math("MULTIPLY", d, r2, x=-300, y=1420),
                       b.math("MULTIPLY", b.math("MAXIMUM", ndl.outputs["Value"], 0.02, x=-200, y=1350),
                              HS_POWER / (4.0 * math.pi ** 2), x=-120, y=1350), x=-50, y=1350)
        vis = b.smooth(ratio, P["hair_shadow_lo"], P["hair_shadow_hi"], x=100, y=1350)
        fade = b.smooth(ndl.outputs["Value"], 0.03, 0.12, x=100, y=1250)
        hs = b.math("MULTIPLY", b.math("SUBTRACT", 1.0, vis, x=250, y=1350), fade, x=400, y=1350)   # 1 = in hair shadow
        if P.get("generic_casters") == []:
            hs = 0.0
        lit = b.mixc(hs, lit, (0, 0, 0, 1), x=900, y=600)
        if game_face:          # inside the hair shadow: deep shadow of the same ramp
            s_face = b.math("SUBTRACT", s_face, b.math("MULTIPLY", hs, 2.0, x=900, y=1400), x=1000, y=1350)
        shal = b.mixc(hs, shal, (0, 0, 0, 1), x=900, y=500)      # hair shadow = the face's deep _ShadowColor (v4:
        # the screenshot's band under the bangs is darker than the shallow colour)

        # ---- nose line: Face_D alpha holds a small dark diamond under the nose tip. The game slides it with the
        # view direction in head space (_NoseLineHoriDisp sideways, _NoseLineLkDnDisp when looking down at the face).
        # Exact game formula unknown: shift = view offset x _NoseLine*Disp x nose_k (sign/size fitted on the close-up).
        # UV: character's right = low u, up = high v.
        inc = b.node("ShaderNodeVectorTransform", x=-650, y=1500, vector_type="VECTOR",
                     convert_from="WORLD", convert_to="OBJECT")
        b.link(geo_.outputs["Incoming"], inc.inputs["Vector"])
        vn = b.node("ShaderNodeVectorMath", x=-500, y=1500, operation="NORMALIZE")
        b.link(inc.outputs[0], vn.inputs[0])
        upv = b.node("ShaderNodeVectorMath", x=-500, y=1600, operation="CROSS_PRODUCT")
        b.link(R_, upv.inputs[0]); b.link(F_, upv.inputs[1])
        vr = b.node("ShaderNodeVectorMath", x=-350, y=1500, operation="DOT_PRODUCT")
        b.link(vn.outputs[0], vr.inputs[0]); b.link(R_, vr.inputs[1])
        vu = b.node("ShaderNodeVectorMath", x=-350, y=1600, operation="DOT_PRODUCT")
        b.link(vn.outputs[0], vu.inputs[0]); b.link(upv.outputs[0], vu.inputs[1])
        fl_ = prm["floats"]
        su = b.math("MULTIPLY", vr.outputs["Value"], fl_.get("_NoseLineHoriDisp", 0.85) * P["nose_k"], x=-200, y=1500)
        sv = b.math("MULTIPLY", vu.outputs["Value"], fl_.get("_NoseLineLkDnDisp", 0.5) * P["nose_k"], x=-200, y=1600)
        off = b.node("ShaderNodeCombineXYZ", x=-50, y=1550)
        b.link(su, off.inputs[0]); b.link(sv, off.inputs[1])
        nuv = b.node("ShaderNodeVectorMath", x=100, y=1550, operation="ADD")
        b.link(uvn.outputs[0], nuv.inputs[0]); b.link(off.outputs[0], nuv.inputs[1])
        tNose = b.tex(img(D), x=250, y=1550)
        tNose.extension = "EXTEND"
        b.link(nuv.outputs[0], tNose.inputs[0])
        nose_line = b.math("MULTIPLY", b.math("SUBTRACT", 1.0, b.smooth(tNose.outputs["Alpha"], 0.3, 0.95, x=400, y=1550), x=500, y=1550),
                           fl_.get("_NoseLineScale", 1.0), x=650, y=1550, clamp=True)
        # nose-tip specular: lightmap B (one small dot), unflipped UV
        tNs = b.tex(img(sdf, True), x=250, y=1750)
        b.link(uvn.outputs[0], tNs.inputs[0])
        sns = b.node("ShaderNodeSeparateColor", x=500, y=1750)
        b.link(tNs.outputs["Color"], sns.inputs[0])
        nose_spec = b.math("MULTIPLY", b.smooth(sns.outputs[2], 0.12, 0.5, x=650, y=1750),
                           fl_.get("_NoseSpecularScale", 1.0) * P["nose_spec"], x=800, y=1750)
        face_hi = None
        if game_face:
            # game face_high(): sat(lightmap.G - 0.5) on the flipped lightmap UV, only for u in 0.45..0.55 (nose),
            # smoothstep(w' - 0.75, w' + 0.75, .) with w' = max(w, 0.75), x 10 (N.H)^10 on the mesh normal. The lightmap
            # B dot is not read by the game shader: no nose-tip dot.
            nose_spec = b.math("MULTIPLY", sns.outputs[2], 0.0, x=800, y=1820)
            sg_ = b.node("ShaderNodeSeparateColor", x=300, y=1900); b.link(tL.outputs["Color"], sg_.inputs[0])
            gh = b.math("SUBTRACT", sg_.outputs[1], 0.5, x=450, y=1900, clamp=True)
            wq_ = b.math("MAXIMUM", w_, 0.75, x=450, y=1980)
            mrh = b.node("ShaderNodeMapRange", x=600, y=1900, interpolation_type="SMOOTHSTEP", clamp=True)
            b.link(gh, mrh.inputs["Value"])
            b.link(b.math("SUBTRACT", wq_, 0.75, x=550, y=1980), mrh.inputs["From Min"])
            b.link(b.math("ADD", wq_, 0.75, x=550, y=2030), mrh.inputs["From Max"])
            sxu = b.node("ShaderNodeSeparateXYZ", x=450, y=2100); b.link(cxy.outputs[0], sxu.inputs[0])
            ugate = b.math("MULTIPLY", b.math("GREATER_THAN", sxu.outputs[0], 0.45, x=600, y=2100),
                           b.math("LESS_THAN", sxu.outputs[0], 0.55, x=600, y=2150), x=700, y=2100)
            gf = b.node("ShaderNodeNewGeometry", x=300, y=2250)
            hvf = b.node("ShaderNodeVectorMath", x=450, y=2250, operation="ADD")
            b.link(gf.outputs["Incoming"], hvf.inputs[0]); hvf.inputs[1].default_value = tuple(Vector(P["key_world"]).normalized())
            hvf["zzz_key_input"] = 1
            hnf = b.node("ShaderNodeVectorMath", x=600, y=2250, operation="NORMALIZE"); b.link(hvf.outputs[0], hnf.inputs[0])
            ndhf = b.node("ShaderNodeVectorMath", x=750, y=2250, operation="DOT_PRODUCT")
            b.link(gf.outputs["Normal"], ndhf.inputs[0]); b.link(hnf.outputs[0], ndhf.inputs[1])
            p10 = b.math("POWER", b.math("MAXIMUM", ndhf.outputs["Value"], 0.0, x=850, y=2250), 10.0, x=950, y=2250)
            face_hi = b.math("MULTIPLY", b.math("MULTIPLY", mrh.outputs["Result"], ugate, x=850, y=1950),
                             b.math("MULTIPLY", p10, 10.0, x=1050, y=2250), x=1150, y=1950)
            face_hi = b.math("MULTIPLY", face_hi, fb_.outputs["Fac"], x=1250, y=1950)   # 0 on eyes / teeth (bit 16)

    if R is not None:
        sh_col = id_select(b, R, prm["ShadowColor"], y=-900)
        sl_col = id_select(b, R, prm["ShallowColor"], y=-1050)
        sp_col = id_select(b, R, prm["SpecularColor"], y=-1200)
    else:
        sh_col = b.rgb(prm["ShadowColor"][0], x=700, y=-900)
        sl_col = b.rgb(prm["ShallowColor"][0], x=700, y=-1050)
        sp_col = b.rgb(prm["SpecularColor"][0], x=700, y=-1200)

    # measured on the reference: lit = albedo * light_col, shadows get a strong blue ambient on top of
    # the material's own _ShadowColor (neck shadow 171,176,218 from skin albedo 230,208,204)
    # fabric shadows are blue-lavender in game, skin (ID1 / face) and hair shadows stay warm
    if kind == "hair":
        tint_sh, tint_sl = P["hair_shadow_tint"], P["hair_shadow_tint"]
    elif kind == "wing":      # v22: the wing's own _ShadowColor / _ShallowColor, no cloth ambient tint on top
        tint_sh, tint_sl = P["wing_shadow_tint"], P["wing_shadow_tint"]
    elif kind == "face" and P["face_ramp"]:
        tint_sh, tint_sl = P["skin_ramp_shadow_tint"], P["skin_ramp_shallow_tint"]
    elif kind == "face":
        tint_sh, tint_sl = P["skin_shadow_tint"], P["skin_shadow_tint"]
    else:
        tint_sh, tint_sl = P["shadow_tint"], P["shallow_tint"]
    sh_t = b.rgb(tint_sh, x=700, y=-1300); sl_t = b.rgb(tint_sl, x=700, y=-1400)
    if kind == "face" and sdf:         # the hair's shadow on the face has its own (greyer) tone in the video
        sh_t = b.mixc(hs, sh_t, P["face_hairshadow_tint"], x=780, y=-1300)
        sl_t = b.mixc(hs, sl_t, P["face_hairshadow_tint"], x=780, y=-1400)
    if R is not None and kind == "body" and id_tints:
        # per material ID ambient tint on top of the game's own _ShadowColor[ID] (v7): one global tint was a
        # compromise between the sleeves (ID5, strong blue-lavender shade) and the corset / bust (ID2, shade barely
        # darker than lit) - it put a visible "lit island" on the bust and left the sleeves too white.
        sh_t = id_select(b, R, [t + (1.0,) for t in id_tints], y=-1300)
        m_ = P["cloth_shallow_mix"]
        sl_t = id_select(b, R, [tuple(1.0 - m_ + m_ * v for v in t) + (1.0,) for t in id_tints], y=-1400)
    if R is not None and kind == "body":
        is_skin = skin_test(x=700, y=-1500)
        sh_t = b.mixc(is_skin, sh_t, P["skin_ramp_shadow_tint"], x=850, y=-1300)
        sl_t = b.mixc(is_skin, sl_t, P["skin_ramp_shallow_tint"], x=850, y=-1400)
    sh_raw, sl_raw = sh_col, sl_col
    sh_col = b.mixc(1.0, sh_col, sh_t, blend="MULTIPLY", x=1000, y=-900)
    sl_col = b.mixc(1.0, sl_col, sl_t, blend="MULTIPLY", x=1000, y=-1050)
    ramp = b.mixc(shal, sh_col, sl_col, x=1050, y=100)
    lcol = P["light_col"] if kind != "face" else tuple(a * b_ for a, b_ in zip(P["light_col"], P["face_lit_tint"]))
    if R is not None and kind == "body":
        lcol = b.mixc(skin_test(x=1050, y=0), lcol,
                      tuple(a * b_ for a, b_ in zip(P["light_col"], P["skin_lit_tint"])), x=1150, y=0)
    if R is not None and kind == "body" and id5_lit is not None:
        # v19: lit colour of material ID5 (Body_2: sleeves), see P["sleeve_lit_tint"]
        lcol = b.mixc(b.math("LESS_THAN", R, 0.15, x=1050, y=-100), lcol,
                      tuple(a * b_ for a, b_ in zip(P["light_col"], id5_lit)), x=1150, y=-100)
    ramp = b.mixc(lit, ramp, lcol, x=1200, y=100)
    if game or game_face:
        # ---- game shadow_body() / shadow_face(): weights of the six tones along s
        if game_face:
            s_ = s_face
        else:
            nsrc = nrm if nrm is not None else b.node("ShaderNodeNewGeometry", x=100, y=-2300).outputs["Normal"]
            # The GAME preset has no main-light self-shadow. Use the shader's
            # signed dot product directly; Diffuse defaults to 0.8 and would
            # scale N.L and include unrelated scene lights.
            ndl_node = b.node("ShaderNodeVectorMath", x=700, y=-2300, operation="DOT_PRODUCT")
            b.link(nsrc, ndl_node.inputs[0])
            if isinstance(native_light, tuple):
                ndl_node.inputs[1].default_value = native_light
                ndl_node["zzz_key_input"] = 1
            else:
                b.link(native_light, ndl_node.inputs[1])
            ndl_node.label = "ZZZ signed N.L"
            s_ = ndl_node.outputs["Value"]
            s_ = native_ui_shadow_signal(b, nsrc, native_light, s_, -2250)
        if ao is not None and not game_face:
            s_ = b.math("ADD", s_, b.math("MULTIPLY", ao, 2.0 / max(P["ao_bias"], 1e-6), x=850, y=-2380), x=1000, y=-2300)
        a_ = b.math("MAXIMUM", id_scalar(b, R if game else None, prm["floats"], "_AlbedoSmoothness", .05, -2200), 1e-5, y=-2200)
        inv_a = b.math("DIVIDE", 1.0, a_, y=-2200)
        k_ = b.math("DIVIDE", 3.0, b.math("SUBTRACT", 2.0, b.math("MULTIPLY", a_, 3.0, y=-2200), y=-2200), y=-2200)
        t_ = 0.3333

        def lin(m, c, y):
            nd_ = b.math("MULTIPLY_ADD", s_, m, x=1150, y=y, clamp=True)
            if isinstance(c, (float, int)): nd_.node.inputs[2].default_value = c
            else: b.link(c, nd_.node.inputs[2])
            return nd_

        def mn(u, v, y):
            return b.math("MINIMUM", u, v, x=1300, y=y)

        ap = b.math("ADD", b.math("MULTIPLY", inv_a, t_, y=-2200), .5, y=-2200)
        minus_k = b.math("MULTIPLY", k_, -1.0, y=-2200)
        half_a = b.math("MULTIPLY", a_, .5, y=-2200)
        c_sf = b.math("ADD", 1.0, b.math("MULTIPLY", b.math("SUBTRACT", half_a, t_, y=-2200), k_, y=-2200), y=-2200)
        st1 = lin(inv_a, ap, -2300)
        w_sh = b.math("SUBTRACT", 1.0, st1, x=1300, y=-2300)
        neg_inv = b.math("MULTIPLY", inv_a, -1.0, y=-2200)
        a_front = b.math("MULTIPLY", b.math("ADD", b.math("MULTIPLY", a_, 1.5, y=-2200), t_, y=-2200), k_, y=-2200)
        w_sf = mn(st1, lin(minus_k, c_sf, -2360), -2360)
        w_s = mn(lin(neg_inv, ap, -2420), lin(k_, b.math("SUBTRACT", 1.0, c_sf, y=-2200), -2480), -2420)
        w_ss = mn(lin(neg_inv, b.math("ADD", ap, 1.0, y=-2200), -2540), lin(inv_a, b.math("SUBTRACT", 1.0, ap, y=-2200), -2600), -2540)
        w_f = mn(lin(inv_a, b.math("MULTIPLY", ap, -1.0, y=-2200), -2660), lin(minus_k, b.math("ADD", 1.0, a_front, y=-2200), -2720), -2660)
        w_l = lin(k_, b.math("MULTIPLY", a_front, -1.0, y=-2200), -2780)
        # Body UI normalizes shadow chroma and blends by camera distance.
        # Face retains the earlier approximation pending its own native audit.
        sl_n = native_ui_shadow_color(b, sl_raw, -2850) if game else b.mixc(0.43725, (1, 1, 1, 1), sl_raw, x=1300, y=-2850)
        sh_n = native_ui_shadow_color(b, sh_raw, -2950) if game else b.mixc(0.43725, (1, 1, 1, 1), sh_raw, x=1300, y=-2950)
        blk = (0, 0, 0, 1)
        g1 = b.mixc(w_s, b.mixc(w_sf, blk, P["post_shallow_fade"], blend="ADD", x=1450, y=-2360), P["post_shallow"],
                    blend="ADD", x=1550, y=-2400)
        g1 = b.mixc(1.0, g1, sl_n, blend="MULTIPLY", x=1650, y=-2400)
        g2 = b.mixc(1.0, b.mixc(w_sh, blk, P["post_shadow"], blend="ADD", x=1450, y=-2300), sh_n, blend="MULTIPLY",
                    x=1650, y=-2300)
        g3 = b.mixc(w_l, b.mixc(w_ss, b.mixc(w_f, blk, P["post_front"], blend="ADD", x=1450, y=-2660), P["post_sss"],
                                blend="ADD", x=1550, y=-2600), (1, 1, 1, 1), blend="ADD", x=1650, y=-2600)
        g12 = b.mixc(1.0, g1, g2, blend="ADD", x=1750, y=-2350)
        nst = P["game_nonskin_tint"] if (kind not in ("face", "hair") or P["game_tint_hair"]) else (1.0, 1.0, 1.0, 1.0)
        if R is not None and kind == "body":
            nst = b.mixc(skin_test(x=1650, y=-2500), nst, (1, 1, 1, 1), x=1750, y=-2500)
        g12 = b.mixc(1.0, g12, nst, blend="MULTIPLY", x=1800, y=-2350)
        ramp = b.mixc(1.0, g12, g3, blend="ADD", x=1850, y=-2400)
        ramp = b.mixc(1.0, ramp, P["game_light"], blend="MULTIPLY", x=1950, y=-2400)
        if game:
            ng = native_lighting.inputs(P)
            va = b.node("ShaderNodeAttribute"); va.attribute_name = "zzz_vertex_alpha"
            environment = native_environment.validate(P.get('native_environment'))
            env_light = env_attenuation = None
            if environment:
                env_geo = b.node('ShaderNodeNewGeometry')
                env_light,env_attenuation = native_environment.light(b,env_geo.outputs['Position'],environment)
                mat['zzz_native_environment'] = json.dumps(environment,sort_keys=True)
            else:
                mat.pop('zzz_native_environment',None)
            ramp = native_lighting.shadow(b, s_, a_, ng['shadow_visibility'],
                skin_test() if R is not None else 0., sl_n, sh_n, va.outputs['Fac'], P,env_light,env_attenuation)
            ramp.node.label = "Native UI shadow: visibility/entity/six tints"

    if kind == "eye":
        # eyeball / lid layers sit recessed inside the face: their own normals put them in the blue shadow
        # ramp (grey-blue sclera, navy lid band). In game the eye layers read fully lit (white sclera).
        ramp = b.rgb(P["light_col"], x=1200, y=0)

    albedo = base
    # silk / stockings live on material ID4 (M.R ~0.3): skin shows through where the
    # surface faces the camera, the fabric reads white at grazing angles.
    # (stockings only: on Body_1 the same ID 4 is the opaque blue-violet skirt panels)
    if R is not None and kind == "body" and silk_fx:
        id4 = b.math("MULTIPLY", b.math("GREATER_THAN", R, 0.15, x=700, y=750),
                     b.math("LESS_THAN", R, 0.40, x=700, y=820), x=850, y=780)
        lw = b.node("ShaderNodeLayerWeight", x=700, y=600)
        lw.inputs["Blend"].default_value = 0.5
        front = b.smooth(lw.outputs["Facing"], P["silk_lo"], P["silk_hi"], x=870, y=600)
        front = b.math("SUBTRACT", 1.0, front, x=1000, y=600)          # 1 = facing camera
        amt = b.math("MULTIPLY", b.math("MULTIPLY", front, id4, x=1100, y=650), P["silk_amount"], x=1200, y=650)
        skin = b.mixc(1.0, base, P["silk_skin"], blend="MULTIPLY", x=1100, y=550)
        albedo = b.mixc(amt, base, skin, x=1300, y=600)

    if kind == "hair":
        albedo = b.mixc(1.0, albedo, P["hair_tint"], blend="MULTIPLY", x=1250, y=450)
    if kind == "wing":   # wings read pale cyan in the game UI lighting
        albedo = b.mixc(1.0, albedo, P["wing_tint"], blend="MULTIPLY", x=1250, y=450)
    col = (b.mixc(1.0, albedo, prm.get("Color", (1, 1, 1, 1)), blend="MULTIPLY", x=1400, y=300)
           if game else b.mixc(1.0, albedo, ramp, blend="MULTIPLY", x=1400, y=300))
    mat.pop("zzz_overlay_warning", None)
    if ps1119_opaque and flags.get("_UseOverlayTex", 0.) > .5 and flags.get("_Emission", 0.) <= .5:
        scale = flags.get("_OverlayTexScale")
        path = P.get("character_overlay")
        captured = P.get("capture_overlay") if not path else None
        if path and not os.path.isfile(bpy.path.abspath(path)):
            mat["zzz_overlay_warning"] = f"shared character overlay file missing: {path}"
        elif not path and not captured:
            try:
                path = find_file(TEX_DIRS, "CharacterOverlayTex.png")
            except FileNotFoundError:
                mat["zzz_overlay_warning"] = "missing shared CharacterOverlayTex.png"
        if not isinstance(scale, (float, int)) or not math.isfinite(scale):
            mat["zzz_overlay_warning"] = "missing or invalid _OverlayTexScale"
        if not mat.get("zzz_overlay_warning"):
            image = (surface_uv.captured_overlay(captured) if captured else
                     bpy.data.images.load(bpy.path.abspath(path), check_existing=True))
            image.colorspace_settings.name = "Non-Color"
            if primary_uv is None:
                primary_uv = b.node("ShaderNodeTexCoord").outputs["UV"]
            col = surface_uv.character_overlay(b, col, primary_uv, image, scale)
    if iris_fx:
        # iris reads lighter in the game video (median 254,136,239 vs our 240,116,226): lift the mid-tones with a
        # gamma (black sprite areas of the premultiplied sheet stay black) and a small gain
        gm = b.node("ShaderNodeGamma", x=1450, y=420); b.link(col, gm.inputs["Color"])
        gm.inputs["Gamma"].default_value = P["iris_gamma"]
        col = b.mixc(1.0, gm.outputs["Color"], (P["iris_gain"],) * 3 + (1.0,), blend="MULTIPLY", x=1550, y=420)
    if sdf:
        if game_face and face_hi is not None:
            col = b.mixc(face_hi, col, (1, 1, 1, 1), blend="ADD", x=1350, y=1650)
        dark = b.mixc(1.0, col, P["nose_dark"], blend="MULTIPLY", x=1400, y=1550)
        col = b.mixc(nose_line, col, dark, x=1550, y=1500)
        col = b.mixc(nose_spec, col, (1, 1, 1, 1), x=1700, y=1500)

    # ---- toon specular
    if kind in ("body", "hair") and not game:
        gl = b.node("ShaderNodeBsdfGlossy", x=250, y=-150)
        gl.inputs["Roughness"].default_value = 0.32 if kind == "body" else 0.22
        if nrm is not None:
            b.link(nrm, gl.inputs["Normal"])
        s2 = b.node("ShaderNodeShaderToRGB", x=420, y=-150)
        b.link(gl.outputs[0], s2.inputs[0])
        sbw = b.node("ShaderNodeRGBToBW", x=580, y=-150)
        b.link(s2.outputs[0], sbw.inputs[0])
        st = b.smooth(sbw.outputs[0], P["spec_th"] - P["spec_soft"], P["spec_th"] + P["spec_soft"], x=870, y=-150)
        mask = Bm if Bm is not None else 0.0
        if kind == "body" and Gm is not None:
            # metal (M.G > 0: rings, cuff bands, clips): Blinn highlight band N.H against the key direction, colour
            # _SpecularColor[ID] (Body_1 ID4 = 2.53, 2.97, 4.24 -> bright blue-silver). The Glossy-BSDF version
            # never lit them (flat mid-grey rings; bright silver in the game).
            gm_ = b.node("ShaderNodeNewGeometry", x=250, y=-350)
            hv = b.node("ShaderNodeVectorMath", x=420, y=-350, operation="ADD")
            b.link(gm_.outputs["Incoming"], hv.inputs[0])
            hv.inputs[1].default_value = tuple(Vector(P["key_world"]).normalized())
            hv["zzz_key_input"] = 1
            hn = b.node("ShaderNodeVectorMath", x=580, y=-350, operation="NORMALIZE")
            b.link(hv.outputs[0], hn.inputs[0])
            ndh = b.node("ShaderNodeVectorMath", x=720, y=-350, operation="DOT_PRODUCT")
            b.link(nrm if nrm is not None else gm_.outputs["Normal"], ndh.inputs[0]); b.link(hn.outputs[0], ndh.inputs[1])
            st = b.math("MULTIPLY", b.smooth(ndh.outputs["Value"], P["metal_spec_lo"], P["metal_spec_hi"], x=870, y=-350),
                        b.math("GREATER_THAN", Gm, 0.08, x=870, y=-420), x=1000, y=-350)
            mask = b.math("ADD", 0.0, P["metal_spec_gain"] / P["spec_gain"], x=750, y=-250)   # constant: no M.B mask
        if kind == "hair" and not isinstance(mask, float):
            # Hair_M.B = hand-painted highlight strokes (the "angel ring" pieces): in the close-up they show
            # wherever the hair is lit, colour _SpecularColor (3.0, 2.6, 1.43) x albedo -> near-white warm streaks
            st = lit
            mask = b.math("MULTIPLY", mask, P["hair_spec_gain"] / P["spec_gain"], x=750, y=-250)
        if isinstance(mask, float):
            sp_amt = b.math("MULTIPLY", st, 0.0, x=1000, y=-150)
        else:
            sp_amt = b.math("MULTIPLY", st, b.math("MULTIPLY", mask, P["spec_gain"], x=870, y=-250), x=1000, y=-150)
        spec = b.mixc(sp_amt, (0, 0, 0, 1), sp_col, x=1150, y=-150)
        spec = b.mixc(1.0, spec, albedo, blend="MULTIPLY", x=1300, y=-150)
        col = b.mixc(1.0, col, spec, blend="ADD", x=1550, y=200)

    # ---- metallic parts (M.G): fake matcap brightening by view angle
    # A.B marks hard / metal trims (shoes, armour plates, clips): in game they get a
    # chrome matcap. Fake it with a camera-space normal ramp (bright top band, dark lower band).
    if prm.get("matcap") and R is not None:
        # game MatCaps per material-ID slot, sampled with the view-space normal
        geo = b.node("ShaderNodeNewGeometry", x=950, y=-600)
        tcn = b.node("ShaderNodeVectorTransform", x=1100, y=-600, vector_type="VECTOR",
                     convert_from="WORLD", convert_to="CAMERA")
        b.link(nrm if nrm is not None else geo.outputs["Normal"], tcn.inputs["Vector"])   # 5.3: inputs[0] is the new LightIndex socket
        muv = b.node("ShaderNodeVectorMath", x=1250, y=-600, operation="MULTIPLY_ADD")
        b.link(tcn.outputs[0], muv.inputs[0])
        muv.inputs[1].default_value = (0.5, 0.5, 0.0); muv.inputs[2].default_value = (0.5, 0.5, 0.0)
        bands = [(0.85, 9.0), (0.60, 0.85), (0.40, 0.60), (0.15, 0.40), (-9.0, 0.15)]
        # Native NapAvatarUI: optional _OtherDataTex2.B mask via _UseMatCapMask.
        # Modes 0/1 interpolate/add T*ColorBurst with sat(A*mask*AlphaBurst).
        # Mode 2 overlays sat(T+(T-.5)*ColorBurst), with the same alpha.
        # The legacy UI_FIT path keeps its previous community reconstruction.
        mask = silk if silk is not None and (not game or prm.get("use_mask")) else 1.0
        if emission and flags.get("_UseMatCapMask", 0.) >= .5:
            # The packed auxiliary B shares a range between MatCap and emission.
            mask = native_emission.matcap_mask(b, silk) if prm.get("use_mask") else silk
        for i, mc in enumerate(prm["matcap"]):
            if not mc or (mc["mode"] == 1 and mc["cb"] <= 0.0):
                continue
            yy = -1700 - 220 * i
            tm = b.tex(img(mc["tex"]), x=1250, y=yy)
            tm.extension = "EXTEND"
            address = muv.outputs[0]
            if game and mc.get("refraction") and surface_uv.opaque_matcap(prm, kind, alpha_mode, M, A):
                if primary_uv is None:
                    # Match the implicit primary UV of the ordinary surface images.
                    primary_uv = b.node("ShaderNodeTexCoord").outputs["UV"]
                address = surface_uv.refracted_matcap(b, primary_uv, address, sa.outputs[0], *mc["refraction"])
            b.link(address, tm.inputs[0])
            lo_, hi_ = bands[i]
            inid = b.math("MULTIPLY", b.math("GREATER_THAN", R, lo_, x=1250, y=yy - 120),
                          b.math("LESS_THAN", R, hi_, x=1250, y=yy - 170), x=1400, y=yy - 140)
            if game:
                inid = id_scalar(b, R, {"slot"+("" if j==0 else str(j+1)): float(i==j) for j in range(5)}, "slot", 0.0, yy-140)
            al = tm.outputs["Alpha"]
            if not isinstance(mask, float):
                al = b.math("MULTIPLY", al, mask, x=1550, y=yy - 200, clamp=True)
            if mc["mode"] == 2:
                bl = b.mixc(1.0, tm.outputs["Color"], tuple(v * (1.0 + mc["cb"]) for v in mc["tint"][:3]) + (1.0,),
                            blend="MULTIPLY", x=1550, y=yy)
                if game:
                    # Native UI overlay: sat(T + (T-.5)*ColorBurst).
                    # The community formula omitted -.5*ColorBurst, lifting
                    # Velina's overlay MatCaps towards white even on dark texels.
                    shift = b.node("ShaderNodeVectorMath", operation="SUBTRACT", x=1600, y=yy)
                    b.link(bl, shift.inputs[0]); shift.inputs[1].default_value = (0.5 * mc["cb"],) * 3
                    positive = b.node("ShaderNodeVectorMath", operation="MAXIMUM", x=1650, y=yy)
                    b.link(shift.outputs[0], positive.inputs[0]); positive.inputs[1].default_value = (0, 0, 0)
                    bl = positive.outputs[0]
                    al = b.math("MULTIPLY", al, mc["ab"], x=1650, y=yy-200, clamp=True)
                bl = b.mixc(1.0, bl, (1, 1, 1, 1), blend="DARKEN", x=1650, y=yy)            # saturate
                bl = b.mixc(al, (0.5, 0.5, 0.5, 1.0), bl, x=1750, y=yy)
                col = b.mixc(b.math("MULTIPLY", inid, P["matcap_gain"], x=1700, y=yy - 140), col, bl,
                             blend="OVERLAY", x=1850, y=yy)
            else:
                a_ = b.math("MULTIPLY", al, mc["ab"], x=1650, y=yy - 200, clamp=True)
                a_ = b.math("MULTIPLY", b.math("MULTIPLY", inid, a_, x=1700, y=yy - 140), P["matcap_gain"],
                            x=1780, y=yy - 140)
                mcol = b.mixc(1.0, tm.outputs["Color"], tuple(v * mc["cb"] for v in mc["tint"][:3]) + (1.0,),
                              blend="MULTIPLY", x=1550, y=yy)
                col = b.mixc(a_, col, mcol, blend="ADD" if mc["mode"] == 1 else "MIX", x=1850, y=yy)
    elif silk is not None and not game:   # only the legacy fitted preset invents a chrome response
        tcn = b.node("ShaderNodeVectorTransform", x=1100, y=-600, vector_type="VECTOR",
                     convert_from="WORLD", convert_to="CAMERA")
        geo = b.node("ShaderNodeNewGeometry", x=950, y=-600)
        b.link(nrm if nrm is not None else geo.outputs["Normal"], tcn.inputs["Vector"])   # 5.3: inputs[0] is the new LightIndex socket
        sepn = b.node("ShaderNodeSeparateXYZ", x=1250, y=-600)
        b.link(tcn.outputs[0], sepn.inputs[0])
        cr = b.node("ShaderNodeValToRGB", x=1400, y=-600)
        els = cr.color_ramp.elements
        els[0].position, els[0].color = 0.25, (0.38, 0.40, 0.48, 1)
        els[1].position, els[1].color = 0.62, (1.45, 1.45, 1.55, 1)
        e = els.new(0.50); e.color = (0.80, 0.82, 0.90, 1)
        e = els.new(0.78); e.color = (0.95, 0.97, 1.05, 1)
        yy = b.math("MULTIPLY_ADD", sepn.outputs[1], 0.5, x=1300, y=-700)  # camera-space up: -1..1 -> 0..1
        yy.node.inputs[2].default_value = 0.5
        b.link(yy, cr.inputs[0])
        chrome = b.mixc(1.0, b.mixc(0.35, albedo, (1, 1, 1, 1), x=1550, y=-500), cr.outputs[0],
                        blend="MULTIPLY", x=1650, y=-550)
        met = b.math("MULTIPLY", silk, 0.75, x=1550, y=-450)
        col = b.mixc(met, col, chrome, x=1750, y=200)

    if game:
        fl_g = prm["floats"]
        gg = b.node("ShaderNodeNewGeometry", x=950, y=-3200)
        Nn = nrm if nrm is not None else gg.outputs["Normal"]
        Lk = native_light

        def dotn(u, v, y):
            nd_ = b.node("ShaderNodeVectorMath", x=1300, y=y, operation="DOT_PRODUCT")
            b.link(u, nd_.inputs[0])
            if isinstance(v, tuple):
                nd_.inputs[1].default_value = v
                nd_["zzz_key_input"] = 1
            else:
                b.link(v, nd_.inputs[1])
            return nd_.outputs["Value"]

        skin_g = skin_test(x=1300, y=-3100) if R is not None else None
        # Native NapAvatarUI fragment 152/154/156/159, linked by material PathID.
        # MatCap modifies unlit surface colour, then the native curve builds both
        # diffuse and F0; lighting multiplies each contribution only once.
        spec_base = native_ui_surface(b, col, Nn, R, prm, -5500, Lk, ps1119_normals=ps1119_opaque)
        metal = b.math("MULTIPLY", Gm, fl_g.get("_Metallic", 1.0), y=-5450) if Gm is not None else 0.0
        diffuse_weight = b.math("SUBTRACT", 0.96, b.math("MULTIPLY", metal, 0.96, y=-5400), y=-5360)
        diffuse = b.mixc(diffuse_weight, (0, 0, 0, 1), spec_base, blend="ADD", y=-5300, clamp_factor=False)
        col = b.mixc(1.0, diffuse, ramp, blend="MULTIPLY", y=-5250)
        native_spec_excess = None
        if Bm is not None:
            hv = b.node("ShaderNodeVectorMath", x=1100, y=-3300, operation="ADD")
            b.link(gg.outputs["Incoming"], hv.inputs[0])
            if isinstance(Lk, tuple):
                hv.inputs[1].default_value = Lk; hv["zzz_key_input"] = 1
            else: b.link(Lk, hv.inputs[1])
            hn = b.node("ShaderNodeVectorMath", x=1200, y=-3300, operation="NORMALIZE"); b.link(hv.outputs[0], hn.inputs[0])
            rng = id_scalar(b, R, fl_g, "_SpecularRange", 1.0, -3280)

            def f75(v, y):
                scaled = b.math("MULTIPLY", v, rng, x=1400, y=y)
                nd_ = b.math("MULTIPLY_ADD", scaled, 0.75, x=1450, y=y, clamp=True)
                nd_.node.inputs[2].default_value = 0.25
                return nd_

            nh = f75(dotn(Nn, hn.outputs[0], -3300), -3300)
            lh = f75(dotn(hn.outputs[0], Lk, -3380), -3380)
            nl = f75(dotn(Nn, Lk, -3460), -3460)
            gls = fl_g.get("_Glossiness", 1.0)
            g_ = b.math("MULTIPLY", rimw, gls, x=1450, y=-3540) if rimw is not None else b.math("MULTIPLY", Bm, 0.0, x=1450, y=-3540)
            al = b.math("SUBTRACT", 1.0, g_, x=1550, y=-3540); al = b.math("MULTIPLY", al, al, x=1650, y=-3540)
            a2 = b.math("MULTIPLY", al, al, x=1750, y=-3540)
            d1 = b.math("MULTIPLY_ADD", b.math("MULTIPLY", nh, nh, x=1550, y=-3300), b.math("SUBTRACT", a2, 1.0, x=1850, y=-3540),
                        x=1950, y=-3300)
            d1.node.inputs[2].default_value = 1.00001
            den = b.math("MULTIPLY", b.math("MULTIPLY", d1, d1, x=2050, y=-3300),
                         b.math("MAXIMUM", b.math("MULTIPLY", lh, lh, x=1550, y=-3380), 0.1, x=1650, y=-3380), x=2150, y=-3300)
            k42 = b.math("MULTIPLY_ADD", al, 4.0, x=1850, y=-3620); k42.node.inputs[2].default_value = 2.0
            den = b.math("MULTIPLY", den, k42, x=2250, y=-3300)
            dv = b.math("SUBTRACT", b.math("DIVIDE", a2, den, x=2350, y=-3300), g_, x=2450, y=-3300, clamp=True)
            v_ = b.math("DIVIDE", b.math("MULTIPLY", dv, nl, x=2550, y=-3300), b.math("MAXIMUM", al, 1e-5, x=1850, y=-3700),
                        x=2650, y=-3300)
            toon_scale = b.math("MULTIPLY", id_scalar(b, R, fl_g, "_ToonSpecular", 0.01, -3720),
                                id_scalar(b, R, fl_g, "_ModelSize", 1.0, -3760), x=2650, y=-3760)
            v_ = b.math("MULTIPLY", v_, b.math("MULTIPLY", toon_scale, 10.0, y=-3760), x=2750, y=-3300, clamp=True)
            toon_term = b.math("MULTIPLY", v_, Bm, x=2850, y=-3300)
            # UI uses half-Lambert N.H and unsquared light mask. The battle
            # Standard shader has a different mask and must not be substituted.
            shape_normal = Nn
            sphere_mask = None
            sphere_profile = native_head_sphere.validate(P.get('native_head_sphere'))
            if sphere_profile and sphere_profile['radius'] > 0:
                if head_sphere_center is None:
                    raise ValueError('Enabled head sphere needs its bound center object')
                center = native_head_sphere.center_socket(b,head_sphere_center)
                shape_normal, sphere_mask = native_head_sphere.shape_inputs(b,Nn,Lk,gg.outputs['Position'],
                    center,sphere_profile['radius'])
                mat['zzz_head_sphere_profile'] = json.dumps(sphere_profile,sort_keys=True)
            else:
                mat.pop('zzz_head_sphere_profile',None)
            shape_nh = b.math("MULTIPLY_ADD", dotn(shape_normal, hn.outputs[0], -4500), 0.5, y=-4500, clamp=True)
            shape_nh.node.inputs[2].default_value = 0.5
            shape_lit = b.math("MULTIPLY_ADD", s_, 1.5, y=-4580, clamp=True)
            shape_lit.node.inputs[2].default_value = 0.5
            if sphere_mask is not None: shape_lit = sphere_mask
            shape_edge = b.math("ADD", b.math("SUBTRACT", Bm, 1.0, y=-4660),
                                b.math("MULTIPLY", shape_nh, shape_lit, y=-4580), y=-4660)
            softness = id_scalar(b, R, fl_g, "_ShapeSoftness", 0.1, -4740)
            shape_mask = b.math("DIVIDE", shape_edge, b.math("MAXIMUM", softness, 1e-5, y=-4740), y=-4820, clamp=True)
            lh2 = b.math("MAXIMUM", b.math("MULTIPLY", lh, lh, y=-4900), 0.1, y=-4900)
            shape_term = b.math("MULTIPLY", shape_mask,
                                b.math("MULTIPLY", b.math("MINIMUM", b.math("DIVIDE", 0.166663334, lh2, y=-4980), 1.0, y=-4980), nl, y=-5060), y=-5140)
            shape_switch = b.math("GREATER_THAN", id_scalar(b, R, fl_g, "_HighlightShape", 0.0, -5220), 0.5, y=-5220)
            spec_mix = b.node("ShaderNodeMix", data_type="FLOAT", x=2900, y=-3300)
            b.link(shape_switch, spec_mix.inputs[0]); b.link(toon_term, spec_mix.inputs[2]); b.link(shape_term, spec_mix.inputs[3])
            amt = b.math("MULTIPLY", spec_mix.outputs[0],
                         100.0 * fl_g.get("_SpecIntensity", 0.1) * P["game_spec_gain"], x=2950, y=-3300)
            f0 = b.mixc(metal, (0.04, 0.04, 0.04, 1.0), spec_base, x=2950, y=-3500, clamp_factor=False)
            spc = b.mixc(1.0, b.mixc(1.0, f0, ramp, blend="MULTIPLY", x=3050, y=-3500), sp_col, blend="MULTIPLY", x=3150, y=-3500)
            spec = b.mixc(amt, (0, 0, 0, 1), spc, blend="ADD", x=3250, y=-3300, clamp_factor=False)
            # Native UI explicitly adds the excess above 1 again (HDR branch).
            excess = b.node("ShaderNodeVectorMath", operation="SUBTRACT", x=3300, y=-3500)
            b.link(spec, excess.inputs[0]); excess.inputs[1].default_value = (1, 1, 1)
            pos = b.node("ShaderNodeVectorMath", operation="MAXIMUM", x=3400, y=-3500)
            b.link(excess.outputs[0], pos.inputs[0]); pos.inputs[1].default_value = (0, 0, 0)
            native_spec_excess = pos.outputs[0]
            contribution = spec if native_transparent else b.mixc(1.0, spec, native_spec_excess, blend="ADD", y=-3500)
            col = b.mixc(1.0, col, contribution, blend="ADD", x=3500, y=-3300)
            col.node.label = "ZZZ native UI diffuse + specular"
        # Native material rim is encoded for deferred depth resolve, not added
        # directly over the opaque surface. Transparent UI uses a direct path.
        ng = native_lighting.inputs(P)
        ambient_color = environment['ambient_color'] if environment else P.get('native_character_ambient')
        if ambient_color is not None:
            ambient = native_environment.ambient_rgb(b,spec_base,diffuse,ambient_color,ng['vertex_ambient'])
            col = b.mixc(1.,col,ambient,blend='ADD',clamp_factor=False)
            col.node.label = 'Native material plus character and vertex ambient'
        if emission:
            tint = id_select(b, R, prm["EmissionColor"], -5300)
            emitted, lut_weight = native_emission.outputs(b, spec_base, tint, silk, prm.get("use_mask"))
            col = native_lighting.Ops(b).v("ADD", col, emitted)
        if native_transparent:
            # HalfResCharacterToon PS60 applies LUT to diffuse+spec+ambient,
            # then adds HDR excess and direct rim; RGB is premultiplied last.
            profile = P.get('native_character_lut')
            if profile and profile['enabled']:
                mapped = native_color.shader(b, col, profile)
                col = b.mixc(prm['floats'].get('_ColorCorrectionIntensity',1.),col,mapped)
            if native_spec_excess is not None:
                col = b.mixc(1.,col,native_spec_excess,blend='ADD')
        cam_data = b.node("ShaderNodeCameraData")
        flag = b.node("ShaderNodeAttribute"); flag.attribute_name = "zzz_surface_hair"
        hair_flag = b.math("GREATER_THAN", flag.outputs['Fac'], .5)
        f0_rim = b.mixc(metal, (.04,.04,.04,1.), spec_base, clamp_factor=False)
        suns = prm.get('UISunColor',[(1.,1.,1.,1.)]*5)
        rim_colors = prm.get('RimGlowLightColor',[(1.,1.,1.,1.)]*5)
        sun = id_select(b,R,suns,0) if R is not None else b.rgb(suns[0])
        rcolor = id_select(b,R,rim_colors,0) if R is not None else b.rgb(rim_colors[0])
        rim_light=b.node('ShaderNodeVectorMath',operation='ADD')
        rim_light.inputs[0].default_value=(0.,0.,0.)
        if isinstance(Lk, tuple):
            rim_light.inputs[1].default_value=Lk; rim_light['zzz_key_input']=1
        else: b.link(Lk, rim_light.inputs[1])
        rim_light.label='Native rim light direction'
        raw_rim = native_lighting.rim(b,Nn,gg.outputs['Incoming'],rim_light.outputs[0],cam_data.outputs['View Distance'],
            skin_g if skin_g is not None else 0., hair_flag, diffuse,f0_rim,metal,ramp,sun,rcolor,P)
        if alpha_mode == 'BLEND':
            col = b.mixc(1.,col,raw_rim,blend='ADD')
        else:
            def native_aov(name,value,color=False):
                a=b.node('ShaderNodeOutputAOV');a.aov_name=name
                socket=a.inputs['Color' if color else 'Value']
                if isinstance(value,(float,int)):socket.default_value=value
                else:b.link(value,socket)
            native_aov('zzz_rim',native_lighting.encode_rim(b,raw_rim),True)
            native_aov('zzz_rim_skin',skin_g if skin_g is not None else 0.)
            native_aov('zzz_rim_surface',1.)
            native_aov('zzz_rim_ndv',dotn(Nn,gg.outputs['Incoming'],0))
            # PS26 uses V x N in a +Z-forward view basis. Blender's camera
            # looks down -Z: reflecting this basis reverses cross-product XY.
            cross=b.node('ShaderNodeVectorMath',operation='CROSS_PRODUCT')
            b.link(Nn,cross.inputs[0])
            b.link(gg.outputs['Incoming'],cross.inputs[1])
            tr=b.node('ShaderNodeVectorTransform',vector_type='VECTOR',convert_from='WORLD',convert_to='CAMERA')
            b.link(cross.outputs['Vector'],tr.inputs['Vector'])
            native_aov('zzz_rim_cross',tr.outputs['Vector'],True)
            uv=b.node('ShaderNodeTexCoord');native_aov('zzz_rim_uv',uv.outputs['Window'],True)

    # ---- rim glow
    if kind in ("body", "hair", "wing", "face") and not game:
        lw3 = b.node("ShaderNodeLayerWeight", x=1250, y=650)
        lw3.inputs["Blend"].default_value = 0.5
        rim = b.smooth(lw3.outputs["Fresnel"], P["rim_lo"], P["rim_hi"], x=1400, y=650)
        rg = P["rim_gain"] * {"wing": 1.0, "face": 0.3}.get(kind, 1.0)
        rim = b.math("MULTIPLY", rim, rg, x=1550, y=650)
        rim_col = b.mixc(0.5, albedo, (0.75, 0.88, 1.0, 1), x=1550, y=750)
        rim_term = b.mixc(rim, (0, 0, 0, 1), rim_col, x=1700, y=650)
        col = b.mixc(1.0, col, rim_term, blend="ADD", x=1850, y=300)
        # _FresnelColor (0, 0.2, 1.62) * pow(1-NdotV, _FresnelWidth): the blue edge glow
        fpow = b.math("POWER", lw3.outputs["Facing"], P["fresnel_pow"], x=1550, y=900)
        fg = P["fresnel_gain"] * {"wing": 0.5, "face": 0.0, "hair": 0.15}.get(kind, 1.0)
        fpow = b.math("MULTIPLY", fpow, fg, x=1700, y=900)
        if R is not None:   # skin (material ID1, M.R ~ 1) shows no blue edge in game
            fpow = b.math("MULTIPLY", fpow, b.math("SUBTRACT", 1.0, skin_test(x=1500, y=1000), x=1550, y=1000), x=1700, y=1000)
        fcol = b.mixc(fpow, (0, 0, 0, 1), prm["FresnelColor"][0], x=1850, y=900)
        col = b.mixc(1.0, col, fcol, blend="ADD", x=2000, y=300)

    # ---- wing transmission glow
    if kind == "wing" and "generic_casters" not in P:
        tT = b.tex(img("Remielle_Wings_T", True), x=-1600, y=-950)
        stt = b.node("ShaderNodeSeparateColor", x=-1300, y=-950)
        b.link(tT.outputs["Color"], stt.inputs[0])
        g = b.math("MULTIPLY", b.smooth(stt.outputs[0], 0.55, 1.0, x=1700, y=-650), P["wing_glow_gain"], x=1850, y=-650)
        glow = b.mixc(g, (0, 0, 0, 1), P["wing_glow"], x=1950, y=-600)
        col = b.mixc(1.0, col, glow, blend="ADD", x=2050, y=300)

    # ---- AOVs for the eye-through-hair composite (character.setup_compositor)
    def aov(name, v):
        ao_ = b.node("ShaderNodeOutputAOV", x=2600, y=-200 - 150 * len([n for n in b.n if n.type == "OUTPUT_AOV"]))
        ao_.aov_name = name
        if isinstance(v, float):
            ao_.inputs["Value"].default_value = v
        else:
            b.link(v, ao_.inputs["Value"])

    if kind == "hair":
        # Preserve the game's opaque hair colour. Only the eye/brow overlay is
        # weighted by the auxiliary map's R opacity, never the whole hair layer.
        if A:
            if P['game_shading'] and P.get('native_face_layers'):
                native_face_layers.validate(P['native_face_layers'])
                transmission = 0.0
                if prm.get('native_hair_alpha', False):
                    transmission = native_face_layers.transmittance(b, sa.outputs[0], face_layers_head)
                    coverage = P['native_face_layers'].get('coverage_attribute')
                    if coverage:
                        verified = b.node('ShaderNodeAttribute')
                        verified.attribute_name = coverage
                        transmission = b.math('MULTIPLY', transmission, verified.outputs['Fac'])
                aov('zzz_hair', transmission)
            else:
                opacity = b.math("MAXIMUM", sa.outputs[0], prm["floats"].get("_MinStencilAlpha", 0.0), y=-5600, clamp=True)
                aov("zzz_hair", b.math("SUBTRACT", 1.0, opacity, y=-5650, clamp=True))
        else:
            aov("zzz_hair", 0.0)
    if P['game_shading']:
        camera_depth=b.node('ShaderNodeCameraData')
        depth_aov=b.node('ShaderNodeOutputAOV');depth_aov.aov_name='zzz_view_z'
        b.link(camera_depth.outputs['View Z Depth'],depth_aov.inputs['Value'])
        # UIEye and transparent hair draw before DeferredCharacterLUT.
        # Retain legacy order unless the verified UI variant is explicitly selected.
        if (kind not in ('eye','brow') or P.get('native_face_layers')) and alpha_mode != 'BLEND':
            if (P.get('native_character_lut') or {}).get('enabled') and flags.get('_Emission',0.) > .5 and not emission:
                if P['native_character_lut'].get('source_format') != 'dds-rgba16f':
                    raise ValueError(name + ': enabled emission variant is outside the supported opaque PS1119 path')
                # The captured LUT is known, but this variant's exemption mask is unknown.
                # Leave this material on its previous colour path and flag the fallback.
                lut_weight = 0.0
                mat["zzz_lut_warning"] = "Captured LUT bypassed on unsupported emission variant"
            aov('zzz_lut_weight',lut_weight)
    # ---- output
    em = b.node("ShaderNodeEmission", x=2200, y=300)
    b.link(col, em.inputs["Color"])
    em.inputs["Strength"].default_value = 1.0
    out = b.node("ShaderNodeOutputMaterial", x=2600, y=300)
    if alpha_mode == "PREMUL":
        # UIEye writes premultiplied RGB and uses One / OneMinusSrcAlpha.
        # Missing lookup data uses the legacy vertex-alpha approximation below.
        if eye_lut:
            # game (HoyoToon zzz-common.hlsl seperate_eyeshadow): every eye layer is multiplied by a texel of the
            # 16x16 _EyeColorMap picked by the vertex colour R byte (x = R & 15, y = 15 - (R >> 4)), rgb x 2.
            # The addon bakes that texel per corner into the colour attribute "zzz_eye" (character.bake_eye_lut).
            va = b.node("ShaderNodeAttribute", x=1900, y=650); va.attribute_name = "zzz_eye"
            native_eye = P['game_shading'] and native_face_layers.generic_ui_eye(P, prm)
            if native_eye and P.get('native_environment') and not flags.get('_UseOverlayTex'):
                rgb = native_face_layers.eye_surface(b, base, prm['Color'], va.outputs['Color'], P['native_environment'])
            else:
                rgb = b.mixc(1.0, col, va.outputs["Color"], blend="MULTIPLY", x=2050, y=300)
            a = b.math("MULTIPLY", alpha, va.outputs["Alpha"], x=2200, y=650)
            # A Generic texture-only eye still declares PREMUL and supplies the
            # lookup alpha. A different, explicitly identified shader keeps its path.
            reference = (prm or {}).get('shader_ref') or {}
            texture_only = 'generic_casters' in P and not (reference.get('Name') or reference.get('m_PathID'))
            if P['game_shading'] and (native_eye or texture_only or P.get('native_face_layers')):
                rgb = native_face_layers.premultiply(b, rgb, a)
            b.link(rgb, em.inputs['Color'])
        else:
            va = b.node("ShaderNodeVertexColor", x=1900, y=650)
            va.layer_name = "Attribute"
            a = b.math("MULTIPLY", alpha, b.math("SUBTRACT", 1.0, va.outputs["Alpha"], x=2050, y=650), x=2200, y=650)
        aov("zzz_eyes", a)
        inv = b.math("SUBTRACT", 1.0, a, x=2350, y=650)
        tr = b.node("ShaderNodeBsdfTransparent", x=2200, y=450)
        trc = b.node("ShaderNodeCombineColor", x=2350, y=500)
        for k in range(3):
            b.link(inv, trc.inputs[k])
        b.link(trc.outputs[0], tr.inputs["Color"])
        ad = b.node("ShaderNodeAddShader", x=2450, y=350)
        b.link(em.outputs[0], ad.inputs[0]); b.link(tr.outputs[0], ad.inputs[1])
        b.link(ad.outputs[0], out.inputs["Surface"])
        mat.surface_render_method = "BLENDED"
    elif alpha_mode or const_alpha is not None:
        tr = b.node("ShaderNodeBsdfTransparent", x=2200, y=450)
        mx = b.node("ShaderNodeMixShader", x=2400, y=350)
        a = alpha
        if alpha_tex is not None:
            # Explicit opacity source: native transparent-body coverage is A.R.
            # Hair stencil layers use a separate view-layer path in the addon.
            ta = b.tex(img(alpha_tex, True), x=1900, y=750)
            sa_ = b.node("ShaderNodeSeparateColor", x=2050, y=750)
            b.link(ta.outputs["Color"], sa_.inputs[0])
            a = sa_.outputs[0]
        if const_alpha is not None:
            # _T_UI: _UseAlpha 0 -> the game does not read _D.a for this pass; P["gauze_tex_alpha"] keeps the old look
            a = b.math("MULTIPLY", alpha, const_alpha, x=2200, y=600) if P["gauze_tex_alpha"] else const_alpha
        if isinstance(a, float):
            mx.inputs[0].default_value = a
        else:
            b.link(a, mx.inputs[0])
        b.link(tr.outputs[0], mx.inputs[1])
        b.link(em.outputs[0], mx.inputs[2])
        b.link(mx.outputs[0], out.inputs["Surface"])
        mat.surface_render_method = "BLENDED" if alpha_mode == "BLEND" else "DITHERED"
        if alpha_tex is not None:
            mat.use_transparency_overlap = False       # mostly opaque strands: only the front-most layer blends
    else:
        b.link(em.outputs[0], out.inputs["Surface"])
        mat.surface_render_method = "DITHERED"
    if kind == "eye" and alpha_mode != "PREMUL":
        aov("zzz_brows", 1.0)
    mat.use_backface_culling = native_transparent and prm['floats'].get('_Cull',0.) == 2.
    return mat


def build_outline(name, D, color_mul, dark, M=None, game=False):
    """outline = albedo * _OutlineColor[material ID] * dark (game: per-ID outline tint, skin ID1 reddish).
    color_mul: one colour, or the 5 per-ID colours (picked with _M.R like the shadow colours)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree; nt.nodes.clear()
    b = NB(mat)
    # game (HoyoToon's reconstruction of the ZZZ shader, ref/HoyoToon-ZZZ/zzz-common.hlsl outline_color()):
    # line = diffuse * pow(0.5 * _OutlineColor[ID], 1.5) * lerp(0.01, 1, 0.5 + 0.5 * saturate(N.L))
    ocf = (lambda v: (0.5 * v) ** 1.5 * P["outline_game_gain"]) if game else (lambda v: v * dark)
    t = b.tex(img(D), x=-600, y=0)
    if M and isinstance(color_mul[0], (tuple, list)):
        tM = b.tex(img(M, True), x=-900, y=-300)
        sm = b.node("ShaderNodeSeparateColor", x=-700, y=-300)
        b.link(tM.outputs["Color"], sm.inputs[0])
        oc = id_select(b, sm.outputs[0], [tuple(ocf(v) for v in c[:3]) + (1.0,) for c in color_mul], y=-300)
    else:
        cm = color_mul[0] if isinstance(color_mul[0], (tuple, list)) else color_mul
        oc = b.rgb(tuple(ocf(v) for v in cm[:3]), x=-600, y=-300)
    c = b.mixc(1.0, t.outputs["Color"], oc, blend="MULTIPLY", x=-300, y=0)
    if game:
        # half-Lambert darkening of the line: lerp(0.01, 1, 0.5 + 0.5 * saturate(N.L)). The hull faces are flipped,
        # so the surface normal is the negated shading normal.
        gN = b.node("ShaderNodeNewGeometry", x=-900, y=300)
        ng = b.node("ShaderNodeVectorMath", x=-750, y=300, operation="SCALE")
        b.link(gN.outputs["Normal"], ng.inputs[0]); ng.inputs["Scale"].default_value = -1.0
        df = b.node("ShaderNodeBsdfDiffuse", x=-600, y=300)
        b.link(ng.outputs[0], df.inputs["Normal"])
        s2 = b.node("ShaderNodeShaderToRGB", x=-450, y=300)
        b.link(df.outputs[0], s2.inputs[0])
        bw_ = b.node("ShaderNodeRGBToBW", x=-300, y=300)
        b.link(s2.outputs[0], bw_.inputs[0])
        ndl = b.math("MINIMUM", bw_.outputs[0], 1.0, x=-150, y=300)
        fac = b.math("MULTIPLY_ADD", ndl, 0.495, x=0, y=300)       # 0.01 + 0.99 * (0.5 + 0.5 ndl)
        fac.node.inputs[2].default_value = 0.505
        fc = b.node("ShaderNodeCombineColor", x=-150, y=150)
        for i_ in range(3):
            b.link(fac, fc.inputs[i_])
        c = b.mixc(1.0, c, fc.outputs[0], blend="MULTIPLY", x=-200, y=0)
    # small floor so lines on near-black texels do not go pure black
    c = b.mixc(0.0 if game else P["outline_floor"], c, (0.22, 0.20, 0.24, 1), x=-150, y=0)
    em = b.node("ShaderNodeEmission", x=0, y=0)
    b.link(c, em.inputs["Color"])
    out = b.node("ShaderNodeOutputMaterial", x=200, y=0)
    b.link(em.outputs[0], out.inputs["Surface"])
    mat.use_backface_culling = True
    mat.surface_render_method = "DITHERED"
    if P['game_shading']:
        # Native outline deferred writes geometry/depth but zero rim RGB.
        # Keep hull pixels as occluders instead of mistaking them for infinity.
        camera_depth=b.node('ShaderNodeCameraData')
        depth_aov=b.node('ShaderNodeOutputAOV');depth_aov.aov_name='zzz_view_z'
        b.link(camera_depth.outputs['View Z Depth'],depth_aov.inputs['Value'])
        lut_aov=b.node('ShaderNodeOutputAOV');lut_aov.aov_name='zzz_lut_weight';lut_aov.inputs['Value'].default_value=1.
    return mat


def outline_gn():
    """GN modifier group: weld seams, push along smooth normals, flip, set outline material."""
    name = "ZZZ_Outline"
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.is_modifier = True
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    w = g.interface.new_socket("Width", in_out="INPUT", socket_type="NodeSocketFloat")
    w.default_value = 0.001; w.min_value = 0.0
    g.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    mi = g.interface.new_socket("Only Material Index", in_out="INPUT", socket_type="NodeSocketInt")
    mi.default_value = -1       # -1 = outline every sub-mesh
    zo = g.interface.new_socket("Z Offset", in_out="INPUT", socket_type="NodeSocketFloat")
    zo.default_value = 0.0; zo.min_value = 0.0
    g.interface.new_socket("ID Map", in_out="INPUT", socket_type="NodeSocketImage")     # _M texture: R = material ID
    ss = g.interface.new_socket("Skin Scale", in_out="INPUT", socket_type="NodeSocketFloat")
    ss.default_value = 1.0             # width factor where the ID map says skin (R > 0.85)
    vr = g.interface.new_socket("Vertex Colour Ref", in_out="INPUT", socket_type="NodeSocketFloat")
    vr.default_value = 0.0             # > 0: width *= vertex colour R / ref (the game's per-vertex width)
    bk = g.interface.new_socket("Baked Normal", in_out="INPUT", socket_type="NodeSocketFloat")
    bk.default_value = 1.0; bk.min_value = 0.0; bk.max_value = 1.0     # 1 = use "zzz_oln" where present
    vp = g.interface.new_socket("View Plane", in_out="INPUT", socket_type="NodeSocketFloat")
    vp.default_value = 0.0; vp.min_value = 0.0; vp.max_value = 1.0     # 1 = game: normal flattened into the view plane
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = g.nodes; l = g.links
    gi = n.new("NodeGroupInput"); gi.location = (-900, 0)
    go = n.new("NodeGroupOutput"); go.location = (700, 0)
    # optionally keep only one material slot (e.g. face skin, not the eye layers inside it)
    mat_idx = n.new("GeometryNodeInputMaterialIndex"); mat_idx.location = (-1100, -300)
    eq = n.new("FunctionNodeCompare"); eq.data_type = "INT"; eq.operation = "EQUAL"; eq.location = (-950, -300)
    ia = 2 if len(eq.inputs) > 2 else 0      # Blender 5.x keeps only the active type's sockets
    l.new(mat_idx.outputs[0], eq.inputs[ia]); l.new(gi.outputs[3], eq.inputs[ia + 1])
    neg = n.new("FunctionNodeCompare"); neg.data_type = "INT"; neg.operation = "LESS_THAN"; neg.location = (-950, -450)
    l.new(gi.outputs[3], neg.inputs[ia]); neg.inputs[ia + 1].default_value = 0
    orr = n.new("FunctionNodeBooleanMath"); orr.operation = "OR"; orr.location = (-800, -350)
    l.new(eq.outputs[0], orr.inputs[0]); l.new(neg.outputs[0], orr.inputs[1])
    sep = n.new("GeometryNodeSeparateGeometry"); sep.domain = "FACE"; sep.location = (-800, -150)
    l.new(gi.outputs[0], sep.inputs["Geometry"]); l.new(orr.outputs[0], sep.inputs["Selection"])
    merge = n.new("GeometryNodeMergeByDistance"); merge.location = (-650, -150)
    merge.inputs["Distance"].default_value = 1e-5
    l.new(sep.outputs[0], merge.inputs[0])
    normal = n.new("GeometryNodeInputNormal"); normal.location = (-650, -400)
    try:
        normal.legacy_corner_normals = False
    except Exception:
        pass
    # width = Width x (skin ? Skin Scale : 1) x (vertex colour R / ref)
    uv = n.new("GeometryNodeInputNamedAttribute"); uv.data_type = "FLOAT_VECTOR"; uv.location = (-1300, -900)
    uv.inputs["Name"].default_value = "UV0"
    itex = n.new("GeometryNodeImageTexture"); itex.location = (-1100, -900); itex.interpolation = "Linear"
    l.new(gi.outputs[5], itex.inputs["Image"]); l.new(uv.outputs[0], itex.inputs["Vector"])
    isep = n.new("FunctionNodeSeparateColor"); isep.location = (-900, -900)
    l.new(itex.outputs["Color"], isep.inputs[0])
    skin = n.new("ShaderNodeMath"); skin.operation = "GREATER_THAN"; skin.location = (-750, -900)
    l.new(isep.outputs[0], skin.inputs[0]); skin.inputs[1].default_value = 0.85
    smix = n.new("ShaderNodeMix"); smix.data_type = "FLOAT"; smix.location = (-600, -900)
    l.new(skin.outputs[0], smix.inputs[0]); smix.inputs[2].default_value = 1.0; l.new(gi.outputs[6], smix.inputs[3])
    vc = n.new("GeometryNodeInputNamedAttribute"); vc.data_type = "FLOAT_COLOR"; vc.location = (-1300, -1100)
    vc.inputs["Name"].default_value = "Attribute"
    vsep = n.new("FunctionNodeSeparateColor"); vsep.location = (-1100, -1100)
    l.new(vc.outputs[0], vsep.inputs[0])
    vdiv = n.new("ShaderNodeMath"); vdiv.operation = "DIVIDE"; vdiv.location = (-900, -1100)
    l.new(vsep.outputs[0], vdiv.inputs[0]); l.new(gi.outputs[7], vdiv.inputs[1])
    vuse = n.new("ShaderNodeMath"); vuse.operation = "GREATER_THAN"; vuse.location = (-900, -1250)
    l.new(gi.outputs[7], vuse.inputs[0]); vuse.inputs[1].default_value = 0.0
    vmix = n.new("ShaderNodeMix"); vmix.data_type = "FLOAT"; vmix.location = (-750, -1100)
    l.new(vuse.outputs[0], vmix.inputs[0]); vmix.inputs[2].default_value = 1.0; l.new(vdiv.outputs[0], vmix.inputs[3])
    wmul = n.new("ShaderNodeMath"); wmul.operation = "MULTIPLY"; wmul.location = (-450, -900)
    l.new(smix.outputs[0], wmul.inputs[0]); l.new(vmix.outputs[0], wmul.inputs[1])
    wfin = n.new("ShaderNodeMath"); wfin.operation = "MULTIPLY"; wfin.location = (-300, -900)
    l.new(wmul.outputs[0], wfin.inputs[0]); l.new(gi.outputs[1], wfin.inputs[1])
    # hull direction: the game's baked outline normal (UV3 -> "zzz_oln", scripts/zzz_outline_normals.py) where the
    # mesh has one, else the welded vertex normal
    oln = n.new("GeometryNodeInputNamedAttribute"); oln.data_type = "FLOAT_VECTOR"; oln.location = (-1100, -480)
    oln.inputs["Name"].default_value = "zzz_oln"
    olen = n.new("ShaderNodeVectorMath"); olen.operation = "LENGTH"; olen.location = (-900, -520)
    l.new(oln.outputs[0], olen.inputs[0])
    ouse = n.new("ShaderNodeMath"); ouse.operation = "GREATER_THAN"; ouse.location = (-750, -520)
    l.new(olen.outputs["Value"], ouse.inputs[0]); ouse.inputs[1].default_value = 0.5
    obk = n.new("ShaderNodeMath"); obk.operation = "MULTIPLY"; obk.location = (-750, -700)
    l.new(ouse.outputs[0], obk.inputs[0]); l.new(gi.outputs[8], obk.inputs[1])
    onrm = n.new("ShaderNodeVectorMath"); onrm.operation = "NORMALIZE"; onrm.location = (-900, -400)
    l.new(oln.outputs[0], onrm.inputs[0])
    nmix = n.new("ShaderNodeMix"); nmix.data_type = "VECTOR"; nmix.location = (-600, -420)
    l.new(obk.outputs[0], nmix.inputs["Factor"])
    va, vb = [x for x in nmix.inputs if x.name in ("A", "B") and x.type == "VECTOR"]
    l.new(normal.outputs[0], va); l.new(onrm.outputs[0], vb)
    nout = next(x for x in nmix.outputs if x.type == "VECTOR")
    acam = n.new("GeometryNodeInputActiveCamera"); acam.location = (-1100, -650)
    oi = n.new("GeometryNodeObjectInfo"); oi.transform_space = "RELATIVE"; oi.location = (-950, -650)
    l.new(acam.outputs[0], oi.inputs["Object"])
    # game (vs_outline): the outline normal goes to view space, its z is zeroed and xy normalised - every vertex is
    # pushed by the full width inside the view plane, so the line width does not depend on how the normal faces
    # the camera. Camera axis in object space = camera rotation applied to (0, 0, -1).
    rv = n.new("FunctionNodeRotateVector"); rv.location = (-780, -760)
    rv.inputs["Vector"].default_value = (0.0, 0.0, -1.0); l.new(oi.outputs["Rotation"], rv.inputs["Rotation"])
    fdot = n.new("ShaderNodeVectorMath"); fdot.operation = "DOT_PRODUCT"; fdot.location = (-620, -760)
    l.new(nout, fdot.inputs[0]); l.new(rv.outputs[0], fdot.inputs[1])
    fsc = n.new("ShaderNodeVectorMath"); fsc.operation = "SCALE"; fsc.location = (-460, -760)
    l.new(rv.outputs[0], fsc.inputs[0]); l.new(fdot.outputs["Value"], fsc.inputs["Scale"])
    fsub = n.new("ShaderNodeVectorMath"); fsub.operation = "SUBTRACT"; fsub.location = (-300, -760)
    l.new(nout, fsub.inputs[0]); l.new(fsc.outputs[0], fsub.inputs[1])
    fnor = n.new("ShaderNodeVectorMath"); fnor.operation = "NORMALIZE"; fnor.location = (-140, -760)
    l.new(fsub.outputs[0], fnor.inputs[0])
    fmix = n.new("ShaderNodeMix"); fmix.data_type = "VECTOR"; fmix.location = (20, -700)
    l.new(gi.outputs[9], fmix.inputs["Factor"])
    fa, fb = [x for x in fmix.inputs if x.name in ("A", "B") and x.type == "VECTOR"]
    l.new(nout, fa); l.new(fnor.outputs[0], fb)
    nfin = next(x for x in fmix.outputs if x.type == "VECTOR")
    sc = n.new("ShaderNodeVectorMath"); sc.operation = "SCALE"; sc.location = (-450, -400)
    l.new(nfin, sc.inputs[0]); l.new(wfin.outputs[0], sc.inputs["Scale"])
    sp = n.new("GeometryNodeSetPosition"); sp.location = (-250, -150)
    # game: clip-space z offset of the hull (_MaxOutlineZOffset). Here: push along the view ray (camera -> vertex)
    ppos = n.new("GeometryNodeInputPosition"); ppos.location = (-950, -550)
    vsub = n.new("ShaderNodeVectorMath"); vsub.operation = "SUBTRACT"; vsub.location = (-780, -600)
    l.new(ppos.outputs[0], vsub.inputs[0]); l.new(oi.outputs["Location"], vsub.inputs[1])
    vnor = n.new("ShaderNodeVectorMath"); vnor.operation = "NORMALIZE"; vnor.location = (-620, -600)
    l.new(vsub.outputs[0], vnor.inputs[0])
    vz = n.new("ShaderNodeVectorMath"); vz.operation = "SCALE"; vz.location = (-450, -600)
    l.new(vnor.outputs[0], vz.inputs[0]); l.new(gi.outputs[4], vz.inputs["Scale"])
    vadd = n.new("ShaderNodeVectorMath"); vadd.operation = "ADD"; vadd.location = (-300, -450)
    l.new(sc.outputs[0], vadd.inputs[0]); l.new(vz.outputs[0], vadd.inputs[1])
    l.new(merge.outputs[0], sp.inputs["Geometry"]); l.new(vadd.outputs[0], sp.inputs["Offset"])
    fl = n.new("GeometryNodeFlipFaces"); fl.location = (-50, -150)
    l.new(sp.outputs[0], fl.inputs[0])
    sm = n.new("GeometryNodeSetMaterial"); sm.location = (150, -150)
    l.new(fl.outputs[0], sm.inputs["Geometry"]); l.new(gi.outputs[2], sm.inputs["Material"])
    shade = n.new("GeometryNodeSetShadeSmooth"); shade.location = (300, -150)
    l.new(sm.outputs[0], shade.inputs[0])
    j = n.new("GeometryNodeJoinGeometry"); j.location = (500, 0)
    l.new(gi.outputs[0], j.inputs[0]); l.new(shade.outputs[0], j.inputs[0])
    l.new(j.outputs[0], go.inputs[0])
    return g


def skin_smooth_gn():
    """GN modifier group: Phong tessellation of the skin (v17).

    Video: the shoulder silhouette is a smooth arc through the mesh vertices; the raw mesh gives a polyline there
    (3 vertices, edges 17-33 px in the close-up, corners of 15-25 deg). Catmull-Clark rounds it too but shrinks the
    shoulder by 3 px. Phong tessellation keeps every vertex and moves a subdivided point p toward the tangent planes
    of its triangle's corners:  p' = p + a * sum_i w_i * ((P_i - p) . N_i) N_i.  With linearly interpolated point
    attributes T = N N^T (rows r0..r2) and v = (P . N) N this is  p' = p + a * (v - T p),  so no per-triangle data is
    needed after Subdivide Mesh. a = Strength where the ID map says skin (R > 0.85), 0 elsewhere (cloth creases and
    metal edges stay sharp). Strength 0.5 puts an edge midpoint on the circle through its two vertices.
    Normals come from a welded copy so UV-seam vertices move together."""
    name = "ZZZ_SkinSmooth"
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.is_modifier = True
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    lv = g.interface.new_socket("Level", in_out="INPUT", socket_type="NodeSocketInt")
    lv.default_value = 2; lv.min_value = 0; lv.max_value = 3
    st = g.interface.new_socket("Strength", in_out="INPUT", socket_type="NodeSocketFloat")
    st.default_value = 0.5
    g.interface.new_socket("ID Map", in_out="INPUT", socket_type="NodeSocketImage")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = g.nodes; l = g.links
    gi = n.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = n.new("NodeGroupOutput"); go.location = (600, 0)

    def scale(vec, f, x, y):
        s = n.new("ShaderNodeVectorMath"); s.operation = "SCALE"; s.location = (x, y)
        l.new(vec, s.inputs[0]); l.new(f, s.inputs["Scale"])
        return s.outputs[0]

    merge = n.new("GeometryNodeMergeByDistance"); merge.location = (-1200, -200)
    merge.inputs["Distance"].default_value = 1e-5
    l.new(gi.outputs[0], merge.inputs[0])
    pos = n.new("GeometryNodeInputPosition"); pos.location = (-1200, -400)
    nor = n.new("GeometryNodeInputNormal"); nor.location = (-1200, -500)
    near = n.new("GeometryNodeSampleNearest"); near.domain = "POINT"; near.location = (-1000, -300)
    l.new(merge.outputs[0], near.inputs["Geometry"]); l.new(pos.outputs[0], near.inputs["Sample Position"])
    si = n.new("GeometryNodeSampleIndex"); si.data_type = "FLOAT_VECTOR"; si.domain = "POINT"; si.location = (-800, -300)
    l.new(merge.outputs[0], si.inputs["Geometry"]); l.new(nor.outputs[0], si.inputs["Value"])
    l.new(near.outputs[0], si.inputs["Index"])
    N = si.outputs[0]
    sx = n.new("ShaderNodeSeparateXYZ"); sx.location = (-600, -300); l.new(N, sx.inputs[0])
    rows = [scale(N, sx.outputs[i], -400, -250 - 120 * i) for i in range(3)]
    dot = n.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"; dot.location = (-600, -650)
    l.new(pos.outputs[0], dot.inputs[0]); l.new(N, dot.inputs[1])
    V = scale(N, dot.outputs["Value"], -400, -650)
    uv = n.new("GeometryNodeInputNamedAttribute"); uv.data_type = "FLOAT_VECTOR"; uv.location = (-1200, -850)
    uv.inputs["Name"].default_value = "UV0"
    itex = n.new("GeometryNodeImageTexture"); itex.interpolation = "Linear"; itex.location = (-1000, -850)
    l.new(gi.outputs[3], itex.inputs["Image"]); l.new(uv.outputs[0], itex.inputs["Vector"])
    isep = n.new("FunctionNodeSeparateColor"); isep.location = (-800, -850); l.new(itex.outputs["Color"], isep.inputs[0])
    skin = n.new("ShaderNodeMath"); skin.operation = "GREATER_THAN"; skin.location = (-600, -850)
    l.new(isep.outputs[0], skin.inputs[0]); skin.inputs[1].default_value = 0.85
    al = n.new("ShaderNodeMath"); al.operation = "MULTIPLY"; al.location = (-400, -850)
    l.new(skin.outputs[0], al.inputs[0]); l.new(gi.outputs[2], al.inputs[1])
    cap = n.new("GeometryNodeCaptureAttribute"); cap.domain = "POINT"; cap.location = (-150, 0)
    for nm in ("r0", "r1", "r2", "v"):
        cap.capture_items.new("VECTOR", nm)
    cap.capture_items.new("FLOAT", "a")
    # v19: the shading normal must stay the mesh's own smooth (custom) normal. After Set Position Blender recomputes
    # normals from the tessellated surface, which is only C0 across the original edges: the toon terminator on the
    # chest came out as a zig-zag. Capture the corner normals before, interpolate, write them back as custom normals.
    cn = n.new("GeometryNodeInputNormal"); cn.location = (-600, 250)
    try:
        cn.legacy_corner_normals = False
    except Exception:
        pass
    ccap = n.new("GeometryNodeCaptureAttribute"); ccap.domain = "CORNER"; ccap.location = (-380, 150)
    ccap.capture_items.new("VECTOR", "n")
    l.new(gi.outputs[0], ccap.inputs["Geometry"]); l.new(cn.outputs[0], ccap.inputs["n"])
    l.new(ccap.outputs["Geometry"], cap.inputs["Geometry"])
    for nm, s in zip(("r0", "r1", "r2", "v"), rows + [V]):      # by name: 5.x has a Selection socket before the items
        l.new(s, cap.inputs[nm])
    l.new(al.outputs[0], cap.inputs["a"])
    sub = n.new("GeometryNodeSubdivideMesh"); sub.location = (50, 0)
    l.new(cap.outputs["Geometry"], sub.inputs[0]); l.new(gi.outputs[1], sub.inputs["Level"])
    p2 = n.new("GeometryNodeInputPosition"); p2.location = (-150, -300)
    comb = n.new("ShaderNodeCombineXYZ"); comb.location = (150, -300)
    for i, nm in enumerate(("r0", "r1", "r2")):
        d = n.new("ShaderNodeVectorMath"); d.operation = "DOT_PRODUCT"; d.location = (0, -250 - 120 * i)
        l.new(cap.outputs[nm], d.inputs[0]); l.new(p2.outputs[0], d.inputs[1]); l.new(d.outputs["Value"], comb.inputs[i])
    sb = n.new("ShaderNodeVectorMath"); sb.operation = "SUBTRACT"; sb.location = (300, -300)
    l.new(cap.outputs["v"], sb.inputs[0]); l.new(comb.outputs[0], sb.inputs[1])
    off = scale(sb.outputs[0], cap.outputs["a"], 300, -450)
    sp = n.new("GeometryNodeSetPosition"); sp.location = (400, 0)
    l.new(sub.outputs[0], sp.inputs["Geometry"]); l.new(off, sp.inputs["Offset"])
    nn = n.new("ShaderNodeVectorMath"); nn.operation = "NORMALIZE"; nn.location = (250, 200)
    l.new(ccap.outputs["n"], nn.inputs[0])
    smn = n.new("GeometryNodeSetMeshNormal"); smn.mode = "FREE"; smn.domain = "CORNER"; smn.location = (500, 100)
    l.new(sp.outputs[0], smn.inputs["Mesh"]); l.new(nn.outputs[0], smn.inputs["Custom Normal"])
    l.new(smn.outputs[0], go.inputs[0])
    return g


def set_gn_input(md, ident, value):
    """Blender 5.x: md.properties.inputs.<Socket_N>.value ; older: md[Socket_N]"""
    props = getattr(md, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        getattr(props.inputs, ident).value = value
    else:
        md[ident] = value


def eye_shadow_mat(face_d):
    m = bpy.data.materials.get("ZZZ_EyeShadow") or bpy.data.materials.new("ZZZ_EyeShadow")
    m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    b = NB(m)
    t = b.tex(img(face_d), x=-600, y=0)
    c = b.mixc(P["eye_shadow"], (1, 1, 1, 1), t.outputs["Color"], x=-300, y=0)
    tr = b.node("ShaderNodeBsdfTransparent", x=0, y=0)
    b.link(c, tr.inputs["Color"])
    out = b.node("ShaderNodeOutputMaterial", x=250, y=0)
    b.link(tr.outputs[0], out.inputs["Surface"])
    m.surface_render_method = "BLENDED"
    m.use_backface_culling = False
    return m


def face_vectors_gn():
    name = "ZZZ_FaceVectors"
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.is_modifier = True
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Head", in_out="INPUT", socket_type="NodeSocketObject")
    g.interface.new_socket("Sun", in_out="INPUT", socket_type="NodeSocketObject")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = g.nodes; l = g.links
    gi = n.new("NodeGroupInput"); gi.location = (-800, 0)
    go = n.new("NodeGroupOutput"); go.location = (800, 0)
    geo = gi.outputs[0]
    x = -400
    for obj_sock, vec, attr in ((1, (0, 1, 0), "zzz_front"), (1, (0, 0, -1), "zzz_right"), (2, (0, 0, 1), "zzz_sun")):
        oi = n.new("GeometryNodeObjectInfo"); oi.transform_space = "RELATIVE"; oi.location = (x, -300)
        l.new(gi.outputs[obj_sock], oi.inputs["Object"])
        rv = n.new("FunctionNodeRotateVector"); rv.location = (x + 150, -300)
        rv.inputs["Vector"].default_value = vec
        l.new(oi.outputs["Rotation"], rv.inputs["Rotation"])
        st = n.new("GeometryNodeStoreNamedAttribute"); st.location = (x + 300, 0)
        st.data_type = "FLOAT_VECTOR"; st.domain = "POINT"
        st.inputs["Name"].default_value = attr
        l.new(geo, st.inputs["Geometry"]); l.new(rv.outputs[0], st.inputs["Value"])
        geo = st.outputs[0]; x += 400
    l.new(geo, go.inputs[0])
    return g


def caster_offset_gn():
    name = "ZZZ_CasterOffset"
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.is_modifier = True
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketVector")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = g.nodes; l = g.links
    gi = n.new("NodeGroupInput"); gi.location = (-600, 0)
    go = n.new("NodeGroupOutput"); go.location = (300, 0)
    acam = n.new("GeometryNodeInputActiveCamera"); acam.location = (-600, -200)
    oi = n.new("GeometryNodeObjectInfo"); oi.transform_space = "RELATIVE"; oi.location = (-400, -200)
    l.new(acam.outputs[0], oi.inputs["Object"])
    rv = n.new("FunctionNodeRotateVector"); rv.location = (-200, -150)
    l.new(gi.outputs[1], rv.inputs["Vector"]); l.new(oi.outputs["Rotation"], rv.inputs["Rotation"])
    sp = n.new("GeometryNodeSetPosition"); sp.location = (50, 0)
    l.new(gi.outputs[0], sp.inputs["Geometry"]); l.new(rv.outputs[0], sp.inputs["Offset"])
    l.new(sp.outputs[0], go.inputs[0])
    return g


def ll_coll(name, objs, state):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    c.use_fake_user = True                      # not linked to a scene: keep it alive on save
    for o in list(c.objects):
        c.objects.unlink(o)
    for o in objs:
        c.objects.link(o)
    for co in c.collection_objects:
        co.light_linking.link_state = state
    return c


def bake_outline_normals(ob, layer="UV3"):
    me = ob.data
    if layer not in me.uv_layers or "UV0" not in me.uv_layers:
        return "no UV3"
    nl = len(me.loops)
    uv = np.zeros(nl * 2); me.uv_layers[layer].data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
    uv0 = np.zeros(nl * 2); me.uv_layers["UV0"].data.foreach_get("uv", uv0); uv0 = uv0.reshape(-1, 2)
    if uv.min() > -0.05 or (uv ** 2).sum(1).max() > 1.001 or np.abs(uv - uv0).max() < 1e-4:
        return "UV3 is not a normal map (range %.2f..%.2f)" % (uv.min(), uv.max())
    me.calc_tangents(uvmap="UV0")
    T = np.zeros(nl * 3); me.loops.foreach_get("tangent", T); T = T.reshape(-1, 3)
    N = np.zeros(nl * 3); me.loops.foreach_get("normal", N); N = N.reshape(-1, 3)
    sg = np.zeros(nl); me.loops.foreach_get("bitangent_sign", sg)
    B = np.cross(N, T) * sg[:, None]
    me.free_tangents()
    z = np.sqrt(np.clip(1.0 - (uv ** 2).sum(1), 0.0, 1.0))
    G = uv[:, 0, None] * T + uv[:, 1, None] * B + z[:, None] * N
    lv = np.zeros(nl, dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    nv = len(me.vertices)
    Gv = np.zeros((nv, 3)); np.add.at(Gv, lv, G)
    ln = np.linalg.norm(Gv, axis=1); ok = ln > 1e-6
    Gv[ok] /= ln[ok][:, None]
    co = np.zeros(nv * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    # neighbours: previous / next vertex in the vertex's best face (largest |e1 x e2|)
    npoly = len(me.polygons)
    ls = np.zeros(npoly, dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
    lt = np.zeros(npoly, dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lp = np.repeat(np.arange(npoly), lt); k = np.arange(nl) - ls[lp]
    nxt = lv[ls[lp] + (k + 1) % lt[lp]]; prv = lv[ls[lp] + (k - 1) % lt[lp]]
    e1 = co[nxt] - co[lv]; e2 = co[prv] - co[lv]
    q = np.linalg.norm(np.cross(e1, e2), axis=1)
    order = np.argsort(q)                                     # the last write per vertex wins = largest
    nb1 = np.zeros(nv, dtype=np.int32); nb2 = np.zeros(nv, dtype=np.int32); have = np.zeros(nv, bool)
    nb1[lv[order]] = nxt[order]; nb2[lv[order]] = prv[order]; have[lv[order]] = q[order] > 1e-14
    a = co[nb1] - co; b_ = co[nb2] - co
    t = a / np.maximum(np.linalg.norm(a, axis=1), 1e-12)[:, None]
    n = np.cross(a, b_); n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    b = np.cross(n, t)
    c = np.stack([(Gv * t).sum(1), (Gv * b).sum(1), (Gv * n).sum(1)], 1)
    c[~(ok & have)] = 0.0                                     # zero = "no baked normal": the hull falls back
    for nm, typ, arr in (("zzz_nb1", "INT", nb1), ("zzz_nb2", "INT", nb2), ("zzz_olc", "FLOAT_VECTOR", c)):
        at = me.attributes.get(nm)
        if at is not None:
            me.attributes.remove(at)
        at = me.attributes.new(nm, typ, "POINT")
        at.data.foreach_set("value" if typ == "INT" else "vector", arr.ravel())
    me.update()
    return "%d vertices, %d without a frame" % (nv, int((~(ok & have)).sum()))


def hull_normal_gn():
    name = "ZZZ_HullNormal"
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.is_modifier = True
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = g.nodes; l = g.links
    gi = n.new("NodeGroupInput"); gi.location = (-1200, 0)
    go = n.new("NodeGroupOutput"); go.location = (600, 0)
    pos = n.new("GeometryNodeInputPosition"); pos.location = (-1200, -200)

    def attr(nm, typ, y):
        a = n.new("GeometryNodeInputNamedAttribute"); a.data_type = typ; a.location = (-1200, y)
        a.inputs["Name"].default_value = nm
        return a.outputs[0]

    def vm(op, a, b=None, x=0, y=0):
        v = n.new("ShaderNodeVectorMath"); v.operation = op; v.location = (x, y)
        l.new(a, v.inputs[0])
        if b is not None:
            l.new(b, v.inputs[1])
        return v

    def nbpos(idx, y):
        s = n.new("GeometryNodeSampleIndex"); s.data_type = "FLOAT_VECTOR"; s.domain = "POINT"; s.location = (-950, y)
        l.new(gi.outputs[0], s.inputs["Geometry"]); l.new(pos.outputs[0], s.inputs["Value"]); l.new(idx, s.inputs["Index"])
        return vm("SUBTRACT", s.outputs[0], pos.outputs[0], -750, y).outputs[0]

    e1 = nbpos(attr("zzz_nb1", "INT", -350), -350)
    e2 = nbpos(attr("zzz_nb2", "INT", -550), -550)
    t = vm("NORMALIZE", e1, None, -550, -350).outputs[0]
    nn = vm("NORMALIZE", vm("CROSS_PRODUCT", e1, e2, -550, -550).outputs[0], None, -380, -550).outputs[0]
    b = vm("CROSS_PRODUCT", nn, t, -200, -450).outputs[0]
    c = n.new("ShaderNodeSeparateXYZ"); c.location = (-550, -750)
    l.new(attr("zzz_olc", "FLOAT_VECTOR", -750), c.inputs[0])
    acc = None
    for i, ax in enumerate((t, b, nn)):
        s = n.new("ShaderNodeVectorMath"); s.operation = "SCALE"; s.location = (0, -350 - 130 * i)
        l.new(ax, s.inputs[0]); l.new(c.outputs[i], s.inputs["Scale"])
        acc = s.outputs[0] if acc is None else vm("ADD", acc, s.outputs[0], 180, -350 - 130 * i).outputs[0]
    st = n.new("GeometryNodeStoreNamedAttribute"); st.data_type = "FLOAT_VECTOR"; st.domain = "POINT"; st.location = (380, 0)
    st.inputs["Name"].default_value = "zzz_oln"
    l.new(gi.outputs[0], st.inputs["Geometry"]); l.new(acc, st.inputs["Value"])
    l.new(st.outputs[0], go.inputs[0])
    return g
