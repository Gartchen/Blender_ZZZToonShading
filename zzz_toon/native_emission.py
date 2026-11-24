"""Primary emission from the opaque NapAvatarUI PS1119 program."""
from .native_lighting import Ops


def avatar_ui(source):
    name = source.get("Name")
    return name == "miHoYo/Character/NapAvatarUI" if name else source.get("m_PathID") == -1264760374214708068


def supported(params, kind, alpha_mode, material_map, auxiliary_map):
    f = params.get("floats", {})
    source = params.get("shader_ref")
    if source and not avatar_ui(source):
        return False
    return (kind == "body" and alpha_mode is None and bool(material_map and auxiliary_map)
            and f.get("_Emission", 0.) > .5 and f.get("_MatCap", 0.) > .5
            and f.get("_RenderType", 0.) == 0. and f.get("_Surface", 0.) == 0.
            and not any(f.get(k, 0.) > .5 for k in
                        ("_UseOverlayTex", "_SymmetryUV", "_SecondaryEmission", "_SpecialWeaponEmission")))


def matcap_mask(b, auxiliary_blue):
    o = Ops(b)
    return o.mul(o.sat(o.mul(auxiliary_blue, 5.1)), o.sub(1., o.m("GREATER_THAN", auxiliary_blue, .2)))


def outputs(b, surface, tint, auxiliary_blue, shared_mask):
    o = Ops(b)
    mask = o.sat(auxiliary_blue)
    if shared_mask:
        mask = o.m("MAXIMUM", 0., o.mul(1.25, o.sub(mask, .2)))
    emitted = o.scale(o.v("MULTIPLY", surface, tint), mask)
    emitted.node.label = "PS1119 emission RGB"
    # This is the alpha of an 8-bit UNORM G-buffer, independent of HDR colour.
    average = o.v("DOT_PRODUCT", emitted, (.3333, .3333, .3333))
    encoded = o.div(o.m("ROUND", o.mul(255., o.sat(average))), 255.)
    weight = o.sub(1., encoded)
    weight.node.label = "PS26 character LUT weight"
    return emitted, weight
