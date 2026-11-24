"""Conservative FBX export repair. Does not run on already assembled characters.

Bind repair requires a duplicated rest matrix plus a compensating child pose.
Cluster repair requires unique exact matrix matches and a closed permutation.
Unsupported/ambiguous cases are reported, never guessed from character names.
"""
import json, math, re
import bpy
import numpy as np
from mathutils import Matrix


def same(a, b):
    angle = math.degrees(a.to_quaternion().rotation_difference(b.to_quaternion()).angle) % 360
    return ((a.translation - b.translation).length < 1e-4 and
            min(angle, 360 - angle) < 0.1 and
            (a.to_scale() - b.to_scale()).length < 1e-4)


def repair_bind(arm):
    if arm.animation_data and arm.animation_data.action:
        return {"skipped": "armature already has an action"}
    bpy.context.view_layer.update()
    rest = {b.name: b.matrix_local.copy() for b in arm.data.bones}
    pose = {p.name: p.matrix.copy() for p in arm.pose.bones}
    candidates = {}
    for b in arm.data.bones:
        if same(rest[b.name], pose[b.name]):
            continue
        duplicates = [n for n, m in rest.items() if n != b.name and same(m, rest[b.name])]
        # A legitimate posed skeleton is not a corrupt bind. Require the child
        # to compensate back to its rest matrix, and the bad rest to duplicate
        # another descendant's matrix, as observed in the exporter.
        descendants = {c.name for c in b.children_recursive}
        if not (set(duplicates) & descendants):
            continue
        if not b.children or not all(same(pose[c.name], rest[c.name]) for c in b.children):
            continue
        if (pose[b.name].to_scale() - rest[b.name].to_scale()).length > 1e-4:
            continue
        candidates[b.name] = pose[b.name]
    if not candidates:
        return {"changed": {}}
    old_active = bpy.context.view_layer.objects.active
    selected = arm.select_get()
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        children = {c.name: c.matrix.copy() for n in candidates for c in arm.data.edit_bones[n].children}
        for name, matrix in candidates.items():
            b = arm.data.edit_bones[name]
            length = b.length
            b.use_connect = False
            b.matrix = matrix
            b.length = length
        for name, matrix in children.items():
            if name not in candidates:
                arm.data.edit_bones[name].matrix = matrix
        bpy.ops.object.mode_set(mode="OBJECT")
        # Re-express the imported prefab pose relative to the corrected rest.
        # Resetting every pose bone would discard valid rotations on other rigs.
        for b in sorted(arm.data.bones, key=lambda b: len(b.parent_recursive)):
            arm.pose.bones[b.name].matrix = pose[b.name]
            bpy.context.view_layer.update()
    finally:
        if arm.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        arm.select_set(selected)
        bpy.context.view_layer.objects.active = old_active
    return {"changed": {n: {"translation_delta_m": (m.translation - rest[n].translation).length}
                        for n, m in candidates.items()}}


def read_cluster_binds(path, details=None):
    from io_scene_fbx import parse_fbx
    root, _ = parse_fbx.parse(path)
    elements = next(e for e in root.elems if e.id == b"Objects").elems
    models = {e.props[0]: e.props[1].split(b"\x00")[0].decode() for e in elements if e.id == b"Model"}
    geoms = {e.props[0] for e in elements if e.id == b"Geometry"}
    defs = {e.props[0]: e for e in elements if e.id == b"Deformer"}
    child, parent = {}, {}
    for c in next(e for e in root.elems if e.id == b"Connections").elems:
        if c.props[0] == b"OO":
            child.setdefault(c.props[2], []).append(c.props[1])
            parent.setdefault(c.props[1], []).append(c.props[2])
    binds = {}
    geometries = {e.props[0]: e for e in elements if e.id == b"Geometry"}
    for did, e in defs.items():
        if e.props[2] != b"Cluster":
            continue
        bone = [models[i] for i in child.get(did, []) if i in models]
        mesh = [models[m] for s in parent.get(did, []) if s in defs for g in parent.get(s, []) if g in geoms
                for m in parent.get(g, []) if m in models]
        tl = next((x for x in e.elems if x.id == b"TransformLink"), None)
        if len(bone) == 1 and len(mesh) == 1 and tl is not None:
            links = binds.setdefault(mesh[0], {})
            if details is not None:
                row = details.setdefault(mesh[0], {"inverse": {}, "ambiguous": False})
                geometry, = [g for s in parent[did] if s in defs for g in parent[s] if g in geoms
                             for m in parent[g] if m in models]
                if bone[0] in links or row.get("geometry", geometry) != geometry:
                    row["ambiguous"] = True
                if "geometry" not in row:
                    row["geometry"] = geometry
                    row["vertices"] = next((x.props[0] for x in geometries[geometry].elems
                                            if x.id == b"Vertices"), None)
                transform = next((x for x in e.elems if x.id == b"Transform"), None)
                if transform is not None:
                    row["inverse"][bone[0]] = np.array(transform.props[0]).reshape(4, 4).T
            links[bone[0]] = Matrix(np.array(tl.props[0]).reshape(4, 4).T.tolist())
    return binds


def close_matrix(a, b):
    return np.allclose(a, b, rtol=0., atol=1e-4)


def translate_bind_islands(ob, arm, offsets, inverse):
    """An equal rest-space translation for every influence commutes with linear skinning."""
    mesh = ob.data
    rig_mods = [mod for mod in ob.modifiers if mod.type == 'ARMATURE']
    if (mesh.shape_keys or len(rig_mods) != 1 or rig_mods[0].object != arm
            or rig_mods[0].use_deform_preserve_volume or rig_mods[0].vertex_group
            or rig_mods[0].use_bone_envelopes or not rig_mods[0].use_vertex_groups):
        return {"skipped": "needs single linear skinning without shape keys or modifier masks"}
    groups = {g.index: g.name for g in ob.vertex_groups if g.name in arm.data.bones
              and arm.data.bones[g.name].use_deform}
    shifts = np.zeros((len(mesh.vertices), 3))
    counts = {}
    for vertex in mesh.vertices:
        names = [groups[g.group] for g in vertex.groups if g.weight > 0 and g.group in groups]
        affected = [n for n in names if n in offsets]
        if not affected:
            continue
        shift = offsets[affected[0]]
        if any(n not in inverse or not close_matrix(offsets.get(n, np.zeros(3)), shift) for n in names):
            return {"skipped": "mixed vertex requires different inverse binds"}
        for n in affected:
            counts[n] = counts.get(n, 0) + 1
        shifts[vertex.index] = shift
    if not counts:
        return {}
    ends = np.empty(len(mesh.edges)*2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", ends)
    a, b = ends.reshape(-1, 2).T
    if not close_matrix(shifts[a], shifts[b]):
        return {"skipped": "translation would change a connected surface"}
    if mesh.users > 1:
        ob.data = mesh.copy()
        mesh = ob.data
    coords = np.empty(len(mesh.vertices)*3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    mesh.vertices.foreach_set("co", (coords.reshape(-1, 3) + shifts).astype(np.float32).ravel())
    mesh.update()
    ob["zzz_mesh_bind_fix"] = True
    return {"translated_vertices": int(np.any(shifts != 0, axis=1).sum()),
            "groups": {n: {"vertices": count, "local_offset": offsets[n].tolist()} for n, count in counts.items()}}


def repair_mesh_binds(arm, meshes, binds, details):
    """Accept only FBX-local evidence for a shared bone with distinct per-mesh bind poses."""
    rest = {b.name: np.array(b.matrix_local) for b in arm.data.bones}
    anchors = {}
    for name, links in binds.items():
        if details[name]["ambiguous"]:
            continue
        for bone, link in links.items():
            if bone in rest and close_matrix(rest[bone], link):
                anchors.setdefault(bone, set()).add(name)
    report = {}
    for ob in meshes:
        name = re.sub(r"\.\d{3}$", "", ob.name)
        links = binds.get(name, {})
        differing = [n for n, m in links.items() if n in rest and not close_matrix(m, rest[n])]
        # A foreign mesh must corroborate at least one differing bind before any intervention.
        if ob.get("zzz_mesh_bind_fix") or not any(anchors.get(n, set()) - {name} for n in differing):
            continue
        row = details[name]
        reason = None
        if ob.get("zzz_cluster_fix") or row["ambiguous"]:
            reason = "ambiguous or remapped FBX clusters"
        elif (not set(links).issubset(rest) or set(links) != set(row["inverse"])
              or len(differing) == len(links)
              or not all(anchors.get(n, set()) - {name} for n in differing)):
            reason = "cannot corroborate every inverse bind in this mesh"
        if reason:
            report[ob.name] = {"skipped": reason}
            continue
        if abs(arm.matrix_world.determinant()) < 1e-10:
            report[ob.name] = {"skipped": "singular armature transform"}
            continue
        local = np.linalg.solve(np.array(arm.matrix_world), np.array(ob.matrix_world))
        inverses = row["inverse"]
        if (not np.isfinite(local).all() or abs(np.linalg.det(local)) < 1e-10
                or not all(close_matrix(np.array(links[n]) @ t, local) for n, t in inverses.items())):
            report[ob.name] = {"skipped": "FBX bind space differs from imported mesh"}
            continue
        raw = row.get("vertices")
        coords = np.empty(len(ob.data.vertices)*3, dtype=np.float32)
        ob.data.vertices.foreach_get("co", coords)
        if raw is None or len(raw) != len(coords) or not close_matrix(raw, coords):
            report[ob.name] = {"skipped": "FBX control points differ from imported mesh"}
            continue
        offsets = {}
        for bone in differing:
            delta = np.linalg.solve(local, rest[bone] @ inverses[bone])
            translation = np.eye(4)
            translation[:3, 3] = delta[:3, 3]
            if not close_matrix(delta, translation):
                break
            offsets[bone] = delta[:3, 3]
        if len(offsets) != len(differing):
            report[ob.name] = {"skipped": "per-mesh bind also rotates or scales geometry"}
        else:
            result = translate_bind_islands(ob, arm, offsets, inverses)
            if result:
                report[ob.name] = result
    return report


def repair_clusters(arm, meshes, path, mesh_report=None):
    try:
        details = {} if mesh_report is not None else None
        binds = read_cluster_binds(path, details)
    except (ImportError, OSError, ValueError, StopIteration) as e:
        if mesh_report is not None:
            mesh_report["skipped"] = str(e)
        return {"skipped": str(e)}
    bones = arm.data.bones
    report = {}
    for ob in meshes:
        name = re.sub(r"\.\d{3}$", "", ob.name)
        d = binds.get(name, {})
        if len(d) < 2 or ob.get("zzz_cluster_fix"):
            continue
        best = None
        for ref in d:
            if ref not in bones:
                continue
            g = bones[ref].matrix_local @ d[ref].inverted()
            ok = sum(n in bones and same(g @ m, bones[n].matrix_local) for n, m in d.items())
            if best is None or ok > best[0]:
                best = (ok, g)
            if ok > len(d) * 0.6:
                break
        if best is None or best[0] <= len(d) * 0.6:
            report[ob.name] = {"skipped": "no majority bind-space agreement"}
            continue
        perm = {}
        for n, m in d.items():
            if n not in bones or same(best[1] @ m, bones[n].matrix_local):
                continue
            hit = [b.name for b in bones if same(best[1] @ m, b.matrix_local)]
            if len(hit) == 1:
                perm[n] = hit[0]
        if not perm:
            continue
        if set(perm) != set(perm.values()) or any(n not in ob.vertex_groups for n in perm):
            report[ob.name] = {"skipped": "ambiguous or non-bijective cluster mapping", "candidates": perm}
            continue
        groups = {n: ob.vertex_groups[n] for n in perm}
        for n, group in groups.items():
            group.name = "__zzz_repair__" + n
        for n, group in groups.items():
            group.name = perm[n]
        ob["zzz_cluster_fix"] = len(perm)
        report[ob.name] = {"changed": perm}
    if mesh_report is not None:
        mesh_report.update(repair_mesh_binds(arm, meshes, binds, details))
    return report


def inspect_and_repair(objs, path, enabled=True, *, per_mesh_binds=False):
    arms = [o for o in objs if o.type == "ARMATURE"]
    if len(arms) != 1:
        return {"skipped": "repair requires one unambiguous armature"}
    arm = arms[0]
    report = {"source": path, "enabled": enabled}
    if enabled:
        report["bind"] = repair_bind(arm)
        mesh_report = report.setdefault("mesh_binds", {}) if per_mesh_binds else None
        report["clusters"] = repair_clusters(arm, [o for o in objs if o.type == "MESH"], path, mesh_report)
    arm["zzz_import_report"] = json.dumps(report)
    print("ZZZ import checks:", json.dumps(report))
    return report
