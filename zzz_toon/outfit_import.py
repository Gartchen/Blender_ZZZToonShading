"""Resolve first-import assets and the SkinPreviewPage body/hair light inputs."""
import json
import math
from pathlib import Path
import re
import struct

from . import light_profiles
from .odin_page import Reader, expand_references


BASIS = [[1, 0, 0], [0, 0, 1], [0, 1, 0]]


def light_profile(body, hair, source, scope):
    result = dict(schema="zzz-toon-light-snapshot/1", body=list(body), hair=list(hair),
                  unity_to_blender_world=BASIS, source=str(source), scope=scope)
    light_profiles.resolve(result)
    return result


def asset_paths(root):
    root = Path(root)
    prefabs = sorted(p for p in root.glob("*.fbx") if not p.stem.lower().endswith("_uv"))
    if len(prefabs) != 1:
        raise ValueError("Choose the asset folder containing one prefab FBX, or select the prefab FBX explicitly")
    return prefabs[0]


def discover_page(fbx):
    role = re.fullmatch(r"Avatar_[^_]+_Size[^_]+_(.+)_UI", fbx.stem, re.I)
    if not role:
        return None
    stem = "UIAvatarShowSettings_" + role[1]
    folders = [fbx.parent, fbx.parent/"Presentation"]
    for suffix in ("_resolved.json", ".dat", ".json"):
        hits = [p for folder in folders if folder.is_dir()
                for p in (folder.glob("*"+suffix) if folder == fbx.parent else folder.rglob("*"+suffix))
                if re.sub(r"^-?\d+_", "", p.name).casefold() == (stem+suffix).casefold()]
        if len(hits) > 1:
            raise ValueError("Multiple matching outfit page exports; select the intended page file")
        if hits:
            return hits[0]
    return None


def _read_page(path):
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Invalid UIAvatarShowSettings document")
        if "avatarShowSettings" in data:
            return data
        if data.get("m_Script", {}).get("Name") != "ConfigUIAvatarShowSettings":
            raise ValueError("Expected resolved UIAvatarShowSettings JSON or its AnimeStudio raw export")
        path = path.with_suffix(".dat")
    blob = path.read_bytes()
    header = (32 + struct.unpack_from("<i", blob, 28)[0] + 3) & ~3
    start = header + 8
    end = start + struct.unpack_from("<i", blob, header + 4)[0]
    if header < 32 or not start < end <= len(blob):
        raise ValueError("Invalid outfit page payload length")
    reader = Reader(blob[:end], start)
    tree = []
    while reader.p < end and blob[reader.p] != 0x31:
        tree.append(reader.entry())
    if reader.p != end and (reader.p+1 != end or blob[reader.p] != 0x31):
        raise ValueError("Incomplete outfit page export")
    return expand_references(tree)


def _directions(setting):
    if not setting["EnableLightAvatarPart"]:
        return None
    angles = setting["LightAvatarPartRotOffset"]["$items"]
    if len(angles) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in angles):
        raise ValueError("Expected four finite body/hair angles in the outfit page")
    return [[math.sin(yaw)*math.cos(elev), math.sin(elev), math.cos(yaw)*math.cos(elev)]
            for yaw, elev in (angles[:2], angles[2:])]


def page_light(path, skin_id):
    try:
        data = _read_page(path)
        pages = [row["AvatarShowSetting"] for group in data["avatarShowSettings"]["$items"]
                 for row in group if row["AvatarShowType"].get("UIName") == "SkinPreviewPage"]
        if len(pages) != 1:
            raise ValueError("Expected exactly one SkinPreviewPage")
        page = pages[0]
        base = _directions(page["AvatarLightSetting"])
        choices = {}
        configs = page.get("SkinOverrideShowSettingConfigs") or {}
        for group in (configs.get("SkinOverrideShowSettingList") or {}).get("$items", []):
            for entry in group:
                override = entry["LightOverrideShowSetting"]
                key = str(entry["SkinID"])
                value = _directions(override["AvatarLightSetting"]) if override["IsApplyOverride"] else base
                if key in choices and choices[key] != value:
                    raise ValueError(f"Conflicting light settings for SkinID {key}")
                choices[key] = value
        scope = "SkinPreviewPage"
        if skin_id:
            selected = choices.get(skin_id, base)
            scope += " / SkinID " + skin_id
        elif choices:
            selected = next(iter(choices.values()))
            if base != selected or any(v != selected for v in choices.values()):
                return None, "SkinID or matching capture needed to distinguish default and outfit light settings"
            scope += " / all outfit settings agree"
        else:
            selected = base
        if selected is None:
            return None, "No separate body/hair light enabled on the selected outfit page"
        return light_profile(*selected, path, scope), scope
    except (KeyError, IndexError, TypeError, struct.error, RecursionError) as exc:
        raise ValueError(f"Unsupported outfit page data in {path.name}: {exc}") from exc
