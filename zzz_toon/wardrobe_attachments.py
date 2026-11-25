"""Bake measured local attachment corrections into a matched outfit's idle action."""
import json
from pathlib import Path

import numpy as np
from mathutils import Quaternion, Vector

from .wardrobe_dynamics import _curves, _sample, _write


KEY = "zzz_wardrobe_attachment_revision"
REVISION = 1


def _swing_weights(count, frame, window):
    """Match the current and preceding poses, tapering at each side's swing extrema."""
    before, after = window
    age = (np.arange(count) + 1 - frame) % count
    distance = np.minimum(age / after, (count - age) / before).clip(0, 1)
    current = 1 - distance**2 * (3 - 2 * distance)
    span = min(before, after)
    t = (age + count // 2) % count - count // 2
    previous = -t * np.maximum(0, 1 - np.abs(t) / span)**2 / (1 - 1 / span)**2
    current -= (1 - (1 / before)**2 * (3 - 2 / before)) * previous
    return current[:, None], previous[:, None]


def _rotation(vector):
    angle = np.linalg.norm(vector)
    return Quaternion(Vector(vector / angle), angle) if angle > 1e-10 else Quaternion()


def _apply_offsets(rig, curves, count, row):
    weights = {side: _swing_weights(count, row["frame"], window)
               for side, window in row.get("swing_window_frames", {}).items()}
    for name, offset in row["joints"].items():
        bone = rig.pose.bones[name]
        samples = _sample(bone, curves, count)
        if weights:
            w, prev = weights[name.split("_")[1]]
            previous = row["previous_joints"][name]
        else:
            w, prev, previous = np.ones((count, 1)), 0, {}
        if "rotation" in offset:
            turns = w * offset["rotation"] + prev * np.asarray(previous.get("rotation", 0))
            _write(curves, bone, "rotation_quaternion",
                   [(q @ _rotation(v)).normalized() for (_, q, _), v in zip(samples, turns)])
        if "translation" in offset:
            steps = w * offset["translation"] + prev * np.asarray(previous.get("translation", 0))
            _write(curves, bone, "location", [p + Vector(v) for (p, _, _), v in zip(samples, steps)])
        if "scale_ratio" in offset:
            _write(curves, bone, "scale", [np.asarray(s) * offset["scale_ratio"] for _, _, s in samples])


def _apply_phase(rig, curves, count, row):
    anchor = row["frame"] - 1
    shift = row["offset_frames"]
    for name in row["joints"]:
        bone = rig.pose.bones[name]
        track = _sample(bone, curves, count)
        shifted = [track[(i + shift) % count] for i in range(count)]
        base_p, base_q, _ = track[anchor]
        moved_p, moved_q, _ = shifted[anchor]
        correction = moved_q.inverted() @ base_q
        _write(curves, bone, "rotation_quaternion", [(q @ correction).normalized() for _, q, _ in shifted])
        _write(curves, bone, "location", [base_p + (p - moved_p) for p, _, _ in shifted])


def bake(scene, rig, role, clip_hash):
    """Called after outfit/skin identity checks and the ordinary dynamics bake."""
    profile = json.loads((Path(__file__).with_name("wardrobe_data") / role / "attachments.json")
                         .read_text(encoding="utf8"))
    if (clip_hash != profile["clip_hash"] or scene.frame_start != 1
            or scene.frame_end != profile["frames"]):
        return False
    action = rig.animation_data.action
    if action.get(KEY) == REVISION:
        return False
    for row in profile["operations"]:
        if row["mesh"] not in scene.objects or any(n not in rig.pose.bones for n in row["joints"]):
            raise ValueError("Attachment calibration does not match the imported rig: " + row["mesh"])
    curves = _curves(rig).fcurves
    for row in profile["operations"]:
        apply = _apply_phase if row["operation"] == "phase" else _apply_offsets
        apply(rig, curves, profile["frames"], row)
        scene.frame_set(scene.frame_start)
    action[KEY] = REVISION
    scene[KEY] = REVISION
    return True
