"""Unity AnimationClip (parsed by parse_clip -> JSON) -> Blender action on the imported prefab armature.
GENERATED from scripts/zzz_anim_import.py + scripts/parse_unity_anim.py by the project (see README v24).

Unity binds curves by CRC32 of the bone path below the prefab root. The FBX export mirrors X, so a Unity local
(q, p) becomes R = (w, x, -y, -z), T = (-x, y, z); pose basis = rest_local^-1 @ anim_local.
blendShape curves: path = crc32(mesh name), attribute = crc32(shape key name), values 0..100.
"""
import bpy, json, zlib
from bisect import bisect_right
from mathutils import Quaternion, Vector, Matrix


def bone_path(b):
    names = []
    while b:
        names.append(b.name); b = b.parent
    if names[-1].startswith("Avatar_"):     # prefab root node (Origin UI FBX): not part of Unity's path
        names.pop()
    return "/".join(reversed(names))


def rest_local(b):
    return (b.parent.matrix_local.inverted() @ b.matrix_local) if b.parent else b.matrix_local.copy()


def conv_q(x, y, z, w):
    return Quaternion((w, x, -y, -z)).normalized()


def conv_p(x, y, z):
    return Vector((-x, y, z))



def _aligned(rot, pos, scale):
    # Most baked exports use a common grid. Some channels contain only a
    # constant, or start/end at different keys. Sample each in its own time.
    times=[k[0] for k in rot]
    if all(not curve or [k[0] for k in curve]==times for curve in (pos,scale)):
        return rot,pos,scale
    grid=sorted({row[0] for curve in (rot,pos,scale) for row in curve})
    def resample(curve, quaternion=False):
        if not curve:
            return []
        axis=[k[0] for k in curve]
        result=[]
        for t in grid:
            i=bisect_right(axis,t)-1
            if i<0 or i==len(curve)-1 or axis[i]==t:
                values=curve[max(i,0)][1:]
            else:
                weight=(t-axis[i])/(axis[i+1]-axis[i])
                a,b=curve[i][1:],curve[i+1][1:]
                if quaternion:
                    q=Quaternion((a[3],*a[:3])).normalized().slerp(Quaternion((b[3],*b[:3])).normalized(),weight)
                    values=(q.x,q.y,q.z,q.w)
                else:
                    values=[x+(y-x)*weight for x,y in zip(a,b)]
            result.append([t,*values])
        return result
    return resample(rot,True),resample(pos),resample(scale)


def apply_clip(arm, clip_path, start=1, repeat=1, action_name=None, mute_shapes=(), skip_windows=()):
    if isinstance(clip_path,dict):
        clip=clip_path
        action_name=action_name or "ZZZ_"+arm.name+"_Idle"
    else:
        from .clip_io import load
        clip=load(clip_path)
    rate = clip.get("rate", 60.0)
    action_name = action_name or "ZZZ_" + arm.name + "_" + clip_path.replace("\\", "/").split("/")[-1].replace(".json", "")
    mute_shapes = set(mute_shapes); skip_windows = list(skip_windows)
    # ---- action (Blender 5 layered actions)
    act = bpy.data.actions.get(action_name)
    if act:
        bpy.data.actions.remove(act)
    act = bpy.data.actions.new(action_name)
    slot = act.slots.new(id_type="OBJECT", name=arm.name)
    layer = act.layers.new("Layer")
    strip = layer.strips.new(type="KEYFRAME")
    cb = strip.channelbag(slot, ensure=True)

    applied = 0
    for b in arm.data.bones:
        # curve keys: plain paths when the exporter resolved them, else "path_<crc32>"; the armature object
        # is the prefab's Bone_Root node when the importer did not keep it as a bone
        bp = bone_path(b)
        h = next((k for k in (bp, arm.name + "/" + bp, "path_%d" % zlib.crc32(bp.encode("utf-8")),
                              "path_%d" % zlib.crc32((arm.name + "/" + bp).encode("utf-8"))) if k in clip["rot"]), None)
        rk = clip["rot"].get(h)
        if not rk:
            continue
        pk = clip["pos"].get(h) or []
        sk = clip["scl"].get(h) or []
        rk,pk,sk=_aligned(rk,pk,sk)
        rinv = rest_local(b).inverted()
        n = len(rk)
        frames, locs, rots, scls = [], [], [], []
        prev = None
        for loop in range(repeat):
            for i in range(n):
                t = rk[i][0]
                q = conv_q(*rk[i][1:5])
                p = conv_p(*pk[i][1:4]) if i < len(pk) else rest_local(b).to_translation()
                s = Vector(sk[i][1:4]) if i < len(sk) else Vector((1, 1, 1))
                m = Matrix.Translation(p) @ q.to_matrix().to_4x4() @ Matrix.Diagonal((*s, 1.0))
                basis = rinv @ m
                l, r, s_ = basis.decompose()
                if prev is not None and prev.dot(r) < 0:
                    r = -r
                prev = r
                frames.append(start + loop * clip.get("stop", n / rate) * 60 + t * 60)
                locs.append(l); rots.append(r); scls.append(s_)
        pb = arm.pose.bones[b.name]
        pb.rotation_mode = "QUATERNION"
        base = 'pose.bones["%s"].' % b.name
        for prop, data, comps in (("location", locs, 3), ("rotation_quaternion", rots, 4), ("scale", scls, 3)):
            for ci in range(comps):
                fc = cb.fcurves.new(base + prop, index=ci, group_name=b.name)
                fc.keyframe_points.add(len(frames))
                co = []
                for f, v in zip(frames, data):
                    co += [f, v[ci]]
                fc.keyframe_points.foreach_set("co", co)
                fc.keyframe_points.foreach_set("interpolation", [bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value] * len(frames))
                fc.update()
        applied += 1

    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = act
    arm.animation_data.action_slot = slot
    print("applied clip", action_name, "to", applied, "bones; frames", start, "->", frames[-1] if applied else None)

    # ---- blendShape curves: "path_<crc32(mesh path)>|blendShape.<crc32(shape key name)>", values 0..100
    # clip-time windows (s) whose blendShape keys are dropped so the curve bridges them: the Idle clip blinks at
    # 1.0 s (Eye_Close 99, MidUp 9, Sad 69) but the outfit-screen video never blinks
    nshape = 0
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.data.shape_keys is None or ob.find_armature() is not arm:
            continue
        key = ob.data.shape_keys
        ph = "path_%d" % zlib.crc32(ob.name.encode("utf-8"))
        bykey = {zlib.crc32(k.name.encode("utf-8")): k for k in key.key_blocks[1:]}
        sact = bpy.data.actions.get(action_name + "_" + ob.name)
        if sact:
            bpy.data.actions.remove(sact)
        sact = bpy.data.actions.new(action_name + "_" + ob.name)
        sslot = sact.slots.new(id_type="KEY", name=key.name)
        scb = sact.layers.new("Layer").strips.new(type="KEYFRAME").channelbag(sslot, ensure=True)
        for curve, kv in clip["float"].items():
            p, _, a = curve.partition("|")
            if p not in (ph, ob.name) or not a.startswith("blendShape."):
                continue
            an = a.split(".", 1)[1]
            kb = bykey.get(int(an)) if an.isdigit() else key.key_blocks.get(an)
            if kb is None:
                continue
            # ZZZ facial controls are bidirectional: each opposite pair gets +v / -v (In 48 / Out -48,
            # Wink 30 / Open -30) and the game clamps blendshape weights to 0..100. Clamp the same way.
            kb.slider_min, kb.slider_max = 0.0, 1.0
            fc = scb.fcurves.new('key_blocks["%s"].value' % kb.name)
            co = []
            for loop in range(repeat):
                for t, v in kv:
                    if any(a_ < t < b_ for a_, b_ in skip_windows):
                        continue
                    val = 0.0 if kb.name in mute_shapes else min(max(v / 100.0, 0.0), 1.0)
                    co += [start + loop * clip.get("stop", 4.0) * 60 + t * 60, val]
            fc.keyframe_points.add(len(co) // 2)
            fc.keyframe_points.foreach_set("co", co)
            fc.update()
            nshape += 1
        if key.animation_data is None:
            key.animation_data_create()
        key.animation_data.action = sact
        key.animation_data.action_slot = sslot
    print("blendShape curves bound:", nshape, "muted:", sorted(mute_shapes), "skipped windows:", skip_windows)
    return applied, nshape
