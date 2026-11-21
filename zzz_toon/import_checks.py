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


def read_cluster_binds(path):
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
    for did, e in defs.items():
        if e.props[2] != b"Cluster":
            continue
        bone = [models[i] for i in child.get(did, []) if i in models]
        mesh = [models[m] for s in parent.get(did, []) if s in defs for g in parent.get(s, []) if g in geoms
                for m in parent.get(g, []) if m in models]
        tl = next((x for x in e.elems if x.id == b"TransformLink"), None)
        if len(bone) == 1 and len(mesh) == 1 and tl is not None:
            binds.setdefault(mesh[0], {})[bone[0]] = Matrix(np.array(tl.props[0]).reshape(4, 4).T.tolist())
    return binds


def repair_clusters(arm, meshes, path):
    try:
        binds = read_cluster_binds(path)
    except (ImportError, OSError, ValueError, StopIteration) as e:
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
    return report


def inspect_and_repair(objs, path, enabled=True):
    arms = [o for o in objs if o.type == "ARMATURE"]
    if len(arms) != 1:
        return {"skipped": "repair requires one unambiguous armature"}
    arm = arms[0]
    report = {"source": path, "enabled": enabled}
    if enabled:
        report["bind"] = repair_bind(arm)
        report["clusters"] = repair_clusters(arm, [o for o in objs if o.type == "MESH"], path)
    arm["zzz_import_report"] = json.dumps(report)
    print("ZZZ import checks:", json.dumps(report))
    return report
