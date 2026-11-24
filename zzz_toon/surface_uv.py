"""Secondary surface coordinates for the opaque NapAvatarUI program."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .native_emission import avatar_ui


ATTRIBUTE = "zzz_surface_texcoord3"


def opaque_matcap(params, kind, alpha_mode, material_map, auxiliary_map):
    if not params or kind != "body" or alpha_mode is not None or not (material_map and auxiliary_map):
        return False
    source = params.get("shader_ref")
    f = params["floats"]
    return (not source or avatar_ui(source)) and (
        f.get("_RenderType", 0.) == 0. and f.get("_Surface", 0.) == 0.
        and f.get("_MatCap", 0.) > .5 and f.get("_SymmetryUV", 0.) <= .5)


def supported(params, kind, alpha_mode, material_map, auxiliary_map):
    return (opaque_matcap(params, kind, alpha_mode, material_map, auxiliary_map)
            and params["floats"].get("_DoubleSided", 0.) > .5)


def character_overlay(b, surface, primary_uv, image, scale):
    """PS1119 t2 is the shared single-level, bilinear repeat data texture."""
    from .native_lighting import Ops
    op = Ops(b)
    size = (*image.size, 1.)
    coords = op.v("SUBTRACT", op.v("MULTIPLY", op.scale(primary_uv, scale), size), (.5, .5, 0.))
    origin = op.v("FLOOR", coords)
    fraction = b.node("ShaderNodeSeparateXYZ")
    b.link(op.v("FRACTION", coords), fraction.inputs[0])
    # A regular minified Blender image introduces mips absent from this resource.
    rows = []
    for y in (0, 1):
        taps = []
        for x in (0, 1):
            tex = b.node("ShaderNodeTexImage")
            tex.image = image
            tex.interpolation = "Closest"
            tex.extension = "REPEAT"
            pixel = op.v("ADD", origin, (x + .5, y + .5, 0.))
            b.link(op.v("DIVIDE", pixel, size), tex.inputs["Vector"])
            taps.append(tex.outputs["Color"])
        rows.append(op.cmix(fraction.outputs[0], *taps))
    sampled = op.cmix(fraction.outputs[1], *rows)
    sampled.node.label = "PS1119 character overlay sample"
    result = op.v("MAXIMUM", op.v("ADD", surface, op.v("SUBTRACT", sampled, (.5,) * 3)), (0.,) * 3)
    result.node.label = "PS1119 character overlay surface"
    return result


def refracted_matcap(b, primary, view_uv, auxiliary_red, transform, depth):
    """PS1119 takes the UV transform from TEXCOORD0, even on a backface."""
    from .native_lighting import Ops
    op = Ops(b)
    sx, sy, ox, oy = transform
    origin = op.v("ADD", op.v("MULTIPLY", primary, (sx, sy, 0.)), (ox, oy, 0.))
    offset = op.scale(view_uv, op.mul(op.sat(auxiliary_red), depth))
    result = op.v("ADD", origin, offset)
    result.node.label = "PS1119 refracted MatCap UV"
    return result


def _uv(layer, count):
    data = np.empty((count, 2), dtype="f")
    layer.data.foreach_get("uv", data.ravel())
    return data


def _from_mesh(mesh, path, item):
    data = path.read_bytes()
    if item.get("source_sha256") and hashlib.sha256(data).hexdigest() != item["source_sha256"]:
        raise ValueError("original Mesh digest differs")
    source = json.loads(data.decode("utf-8-sig"))
    count = len(mesh.vertices)
    if len(source.get("m_UV3") or []) != count * 2:
        raise ValueError("original TEXCOORD3 missing or vertex count differs")
    pos = np.empty((count, 3), dtype="f")
    mesh.vertices.foreach_get("co", pos.ravel())
    native_pos = np.asarray(source["m_Vertices"]).reshape(-1, 3) * [-1, 1, 1]
    corners = np.empty(len(mesh.loops), dtype="i")
    mesh.loops.foreach_get("vertex_index", corners)
    tri = np.asarray(source["m_Indices"]).reshape(-1, 3)
    if (native_pos.shape != pos.shape or not np.allclose(pos, native_pos, rtol=0, atol=2e-5)
            or len(mesh.polygons) * 3 != corners.size or any(len(face.vertices) != 3 for face in mesh.polygons)
            or corners.size != tri.size or not np.array_equal(np.sort(corners.reshape(-1, 3), axis=1), np.sort(tri, axis=1))):
        raise ValueError("original vertex order or triangle correspondence differs")
    uv0 = np.asarray(source["m_UV0"]).reshape(count, 2)[corners]
    if not np.allclose(_uv(mesh.uv_layers["UV0"], len(corners)), uv0, rtol=0, atol=2e-5):
        raise ValueError("primary coordinates differ from original Mesh")
    coords = np.asarray(source["m_UV3"], dtype="f").reshape(count, 2)[corners]
    if not np.isfinite(coords).all():
        raise ValueError("nonfinite original TEXCOORD3")
    return coords


def bind(users, mesh_inputs, params):
    """Keep the named UV layers intact, including legacy FBXs with reordered layers."""
    fields, warnings, sources = {}, [], {}
    for obj in users:
        mesh = obj.data
        item = mesh_inputs.get(obj.name, {})
        path = Path(item.get("source") or "")
        declared = mesh.get("zzz_texcoord3_uv", "UV3")
        if "UV0" not in mesh.uv_layers:
            warnings.append(obj.name + ": missing UV0")
            continue
        if path.suffix.lower() == ".json":
            try:
                fields[obj] = _from_mesh(mesh, path, item)
            except (OSError, KeyError, TypeError, ValueError, IndexError) as error:
                warnings.append(obj.name + ": " + str(error))
                continue
            sources[obj.name] = str(path)
        elif declared in mesh.uv_layers:
            fields[obj] = declared
            sources[obj.name] = declared
        else:
            warnings.append(obj.name + ": missing " + declared)
    params["surface_uv3_available"] = bool(fields)
    if not fields:
        return warnings, sources
    params["mixed_surface_uv3"] = len(fields) != len(users)
    if params["mixed_surface_uv3"]:
        for obj in users:
            obj["zzz_ps1119_has_uv3"] = float(obj in fields)
    names = {value for value in fields.values() if isinstance(value, str)}
    if len(names) == 1 and all(isinstance(value, str) for value in fields.values()):
        params["surface_uv3_name"] = names.pop()
    else:
        # A single material can span meshes with different source channel layouts.
        # CORNER vectors also work on meshes that already have eight UV layers.
        params["surface_uv3_name"] = ATTRIBUTE
        for obj, value in fields.items():
            mesh = obj.data
            coords = _uv(mesh.uv_layers[value], len(mesh.loops)) if isinstance(value, str) else value
            field = mesh.attributes.get(ATTRIBUTE)
            if field is None:
                field = mesh.attributes.new(ATTRIBUTE, "FLOAT_VECTOR", "CORNER")
            if field.domain != "CORNER" or field.data_type != "FLOAT_VECTOR":
                raise ValueError("Incompatible surface coordinates on " + obj.name)
            xyz = np.zeros((len(coords), 3), dtype="f")
            xyz[:, :2] = coords
            field.data.foreach_set("vector", xyz.ravel())
    return warnings, sources


def captured_overlay(profile):
    import bpy
    from .capture_import import overlay_pixels
    digest = profile['sha256']
    existing = next((im for im in bpy.data.images if im.get('zzz_overlay_sha256')==digest and im.packed_file),None)
    if existing:
        return existing
    data = Path(profile['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Character overlay has changed since matching")
    rgb = overlay_pixels(data)
    height,width = rgb.shape[:2]
    image = bpy.data.images.new("ZZZ Capture Overlay "+digest[:16], width=width, height=height,
                                alpha=True, float_buffer=True, is_data=True)
    image.file_format = 'OPEN_EXR'
    image.colorspace_settings.name = 'Non-Color'
    image.alpha_mode = 'CHANNEL_PACKED'
    rgba = np.ones((height,width,4),'f')
    rgba[:,:,:3] = rgb.astype('f')/255.
    image.pixels.foreach_set(rgba.ravel())
    image.update()
    image['zzz_overlay_sha256'] = digest
    image.pack()
    return image

