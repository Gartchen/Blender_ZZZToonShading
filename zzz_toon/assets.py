"""Material input boundary: native exports or explicitly typed normalized materials.

The shading core consumes one schema. No character names are required by the
normalized format; packed-map semantics remain those of the ZZZ shader.
"""
import json
from .material_inputs import ENCODINGS

KINDS = {"body", "hair", "face", "eye", "brow", "wing"}
TEXTURES = {"base_color": "_MainTex", "light": "_LightTex",
            "material": "_OtherDataTex", "auxiliary": "_OtherDataTex2",
            "eye_lut": "_EyeColorMap"}


def load_material(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get('schema') == 'zzz-wardrobe-preset/1':
        textures = {key: {'m_Texture': {'Name': name, 'IsNull': False}}
                    for key, name in data['textures'].items()}
        colors = {key: dict(zip('rgba', value)) for key, value in data['colors'].items()}
        return {'m_Shader': {'m_PathID': data['shader_id'], 'Name': data['shader_name']},
                'm_SavedProperties': {'m_TexEnvs': textures, 'm_Colors': colors,
                                      'm_Floats': data['parameters']}}, None
    if "m_SavedProperties" in data:
        return data, None
    if data.get("schema") != "zzz-toon-material/1":
        raise ValueError("Unknown material schema: " + path)
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError("Material kind must be one of %s: %s" % (sorted(KINDS), path))
    encoding = data.get('color_encoding', 'linear')
    if encoding not in ENCODINGS:
        raise ValueError('Unknown color_encoding: ' + path)
    te = {}
    for key, value in data.get("textures", {}).items():
        if not isinstance(value, str) or not value:
            raise ValueError("Texture names must be nonempty strings: " + path)
        te[TEXTURES.get(key, key)] = {"m_Texture": {"Name": value, "IsNull": False}}
    if "_MainTex" not in te:
        raise ValueError("Material needs textures.base_color: " + path)
    co = {}
    for key, value in data.get("colors", {}).items():
        if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
            raise ValueError("Colours must be linear RGB or RGBA arrays: " + path)
        co[key] = dict(zip("rgba", list(value) + ([1.0] if len(value) == 3 else [])))
    return {"zzz_color_encoding": encoding, "m_SavedProperties": {"m_TexEnvs": te,
             "m_Floats": data.get("parameters", {}), "m_Colors": co}}, kind
