"""Bake secondary bone motion for the wardrobe idle in the imported armature's space."""
import json
import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import anim, clip_io

KEY = 'zzz_wardrobe_dynamics'
REVISION = 1


def _curves(arm):
    animation = arm.animation_data
    return animation.action.layers[0].strips[0].channelbag(animation.action_slot)


def _sample(bone, curves, count):
    defaults = (bone.location, bone.rotation_quaternion, bone.scale)
    channels = [(curves.find(bone.path_from_id(prop), index=i), float(value))
                for prop, values in zip(('location', 'rotation_quaternion', 'scale'), defaults)
                for i, value in enumerate(values)]
    samples = []
    for frame in range(1, count + 1):
        v = [fc.evaluate(frame) if fc else value for fc, value in channels]
        samples.append((Vector(v[:3]), Quaternion(v[3:7]).normalized(), Vector(v[7:])))
    return samples


def _write(curves, bone, prop, values):
    # The extra key makes subframe evaluation at the loop seam explicit.
    values = np.asarray(values, dtype=np.float32)
    if prop == 'rotation_quaternion':
        for i in range(1, len(values)):
            if values[i - 1] @ values[i] < 0:
                values[i] *= -1
    end = values[0].copy()
    if prop == 'rotation_quaternion' and end @ values[-1] < 0:
        end *= -1
    values = np.vstack((values, end))
    frames = np.arange(1, len(values) + 1)
    path = bone.path_from_id(prop)
    linear = bpy.types.Keyframe.bl_rna.properties['interpolation'].enum_items['LINEAR'].value
    for i, column in enumerate(values.T):
        existing = curves.find(path, index=i)
        if existing:
            curves.remove(existing)
        curve = curves.new(path, index=i, group_name=bone.name)
        curve.keyframe_points.add(len(frames))
        curve.keyframe_points.foreach_set('co', np.column_stack((frames, column)).ravel())
        curve.keyframe_points.foreach_set('interpolation', [linear] * len(frames))
        curve.update()


def _outside(point, capsules, radius):
    for a, b, r0, r1 in capsules:
        axis = b - a
        t = min(1., max(0., (point - a).dot(axis) / max(axis.length_squared, 1.e-12)))
        center = a + t * axis
        normal = point - center
        gap = r0 + t * (r1 - r0) + radius - normal.length
        if gap > 0 and normal.length > 1.e-7:
            point += gap * normal.normalized()
    return point


def bake(scene, arm, clip, role, stage):
    path = Path(__file__).with_name('wardrobe_data') / role / 'dynamics.json'
    if not path.is_file() or scene.frame_end < 2:
        return
    profile = json.loads(path.read_text(encoding='utf8'))
    count = scene.frame_end
    matched = clip_io.curve_signature(clip) == profile['clip_hash'] and profile['reference_frame'] <= count
    anchors = set(profile['capture_bones']) if matched else set()
    bones = arm.pose.bones
    anchors.intersection_update(bones.keys(), stage['pose'].keys())
    edges = {}
    colliders = []
    needed = set(anchors)
    for asset in profile['assets']:
        surfaces = []
        for c in asset['colliders']:
            name = c['bone']
            if c['mode'] != 'Inner' or (name not in bones and name != arm.name):
                continue
            if name in bones:
                needed.add(name)
            surfaces.append(len(colliders))
            colliders.append((name, anim.conv_p(*c['start']), anim.conv_p(*c['end']), *c['radii']))
        for chain in asset['chains']:
            for name, child in zip(chain, chain[1:]):
                if name not in bones or child in asset['fixed']:
                    continue
                if child in bones:
                    lever = bones[name].bone.matrix_local.inverted() @ bones[child].bone.head_local
                elif child in asset['virtual'] and asset['virtual'][child]['parent'] == name:
                    lever = anim.conv_p(*asset['virtual'][child]['offset'])
                else:
                    continue
                if lever.length < 1.e-5:
                    continue
                edge = name, child
                edges[edge] = (lever, asset['particles'][child], surfaces, asset.get('preview_goal_follow', .42),
                               math.radians(asset.get('preview_swing_limit_degrees', 6.)))
                needed.add(name)
    for n in tuple(needed):
        needed.update(p.name for p in bones[n].parent_recursive)
    order = sorted(needed, key=lambda n: len(bones[n].parent_recursive))
    curves = _curves(arm)
    rest = {n: anim.rest_local(bones[n].bone) for n in order}
    parents = {n: bones[n].parent.name if bones[n].parent else None for n in order}
    basis = {}
    translations = {}
    for n in order:
        samples = _sample(bones[n], curves.fcurves, count)
        if n in anchors:
            # Calibrate only the demonstrated idle. Preserve its authored motion.
            l0, q0, _ = samples[profile['reference_frame'] - 1]
            ref_l, ref_q, _ = Matrix(profile.get('measured_basis', {}).get(n, stage['pose'][n])).decompose()
            samples = [(l + ref_l - l0, (q @ q0.inverted() @ ref_q).normalized(), s) for l, q, s in samples]
            translations[n] = [v[0] for v in samples]
        basis[n] = [Matrix.LocRotScale(*s) for s in samples]
    by_bone = {}
    for edge in edges:
        by_bone.setdefault(edge[0], []).append(edge)
    moving = set(by_bone) | anchors
    output = {n: [] for n in moving}
    tips, last, goals = {}, {}, {}
    # These response/angle limits are preview fits. Damping, inertia, radii,
    # capsule endpoints, root pins and per-chain membership come from the assets.
    identity = Matrix.Identity(4)
    for tick in range(count * 2):
        frame = tick % count
        world = {None: identity}
        for n in order:
            world[n] = world[parents[n]] @ rest[n] @ basis[n][frame]
        capsules = []
        for name, a, b, r0, r1 in colliders:
            tr = world.get(name, identity)
            capsules.append((tr @ a, tr @ b, r0, r1))
        for n in order:
            parent = world[parents[n]] @ rest[n]
            pose = parent @ basis[n][frame]
            origin = pose.translation
            rotation = Vector()
            for edge in by_bone.get(n, ()):
                lever, inputs, indices, follow_goal, limit = edges[edge]
                goal = pose @ lever
                previous = tips.get(edge, goal)
                velocity = previous - last.get(edge, previous)
                follow = 1. - inputs['inertia']
                point = previous + velocity * (1. - inputs['damping']) + (goal - goals.get(edge, goal)) * follow
                point = point.lerp(goal, follow_goal)
                point = _outside(point, (capsules[i] for i in indices), inputs['radius'])
                direction = point - origin
                axis = goal - origin
                if direction.length > 1.e-7 and axis.length > 1.e-7:
                    swing = axis.rotation_difference(direction)
                    rotation += swing.axis * min(swing.angle, limit)
                last[edge], goals[edge] = previous, goal
            if n in by_bone:
                rotation /= len(by_bone[n])
                swing = Quaternion(rotation.normalized(), rotation.length) if rotation.length > 1.e-8 else Quaternion()
                pose = Matrix.Translation(origin) @ swing.to_matrix().to_4x4() @ Matrix.Translation(-origin) @ pose
                for edge in by_bone[n]:
                    tips[edge] = pose @ edges[edge][0]
            world[n] = pose
            if tick >= count and n in output:
                output[n].append((parent.inverted_safe() @ pose).to_quaternion())
    for n, values in output.items():
        bones[n].rotation_mode = 'QUATERNION'
        _write(curves.fcurves, bones[n], 'rotation_quaternion', values)
    for n, values in translations.items():
        _write(curves.fcurves, bones[n], 'location', values)
    scene[KEY] = json.dumps(dict(revision=REVISION, bones=len(output), edges=len(edges), 
                                 clip_calibration=matched, frames=count, colliders=len(colliders)))
