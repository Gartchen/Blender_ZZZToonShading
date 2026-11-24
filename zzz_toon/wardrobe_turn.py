"""Transient turntable inertia on the free ends of wardrobe MDB chains."""
import json
import math
import time
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

from .wardrobe_dynamics import KEY


class TurnResponse:
    def __init__(self, session):
        self.session = session
        self.links = []
        self.blocked_turn_sign = {}
        self.active = False
        self.ready = False
        self.yaw = session.yaw
        self.lag = self.velocity = 0.
        self.clock = time.perf_counter()
        self.enabled = KEY in session.scene

    def prepare(self):
        s = self.session
        role = s.scene.zzz_toon.wardrobe_role
        path = Path(__file__).with_name('wardrobe_data') / role / 'dynamics.json'
        calibrated = json.loads(s.scene[KEY]).get('clip_calibration', False)
        assets = json.loads(path.read_text(encoding='utf8'))['assets']
        rig = s.rig
        bones = rig.pose.bones
        seen = set()
        for asset in assets:
            for chain in asset['chains']:
                if chain[0] not in asset['fixed'] or chain[0] not in bones:
                    continue
                # Turn an early free segment. The attachment stays in the authored pose.
                name = next((n for n in chain[1:-1] if n in bones and n not in asset['fixed']), None)
                if not name or name in seen:
                    continue
                tip = next((bones[n] for n in reversed(chain) if n in bones and n != name), None)
                if not tip or chain.index(tip.name) <= chain.index(name):
                    continue
                bone = bones[name]
                lever = rig.matrix_world.to_3x3() @ (tip.head - bone.head)
                radius = rig.matrix_world @ tip.head - s.axis
                displacement = Vector((radius.y, -radius.x, 0.))
                if lever.length_squared < .000025:
                    continue
                # Bone and radial vector are measured in the current yaw. Store
                # the response in turntable coordinates so it rotates with the rig.
                axis = lever.cross(displacement) / lever.length_squared
                axis = Quaternion((0,0,1), -s.yaw) @ axis
                if axis.length < .001:
                    continue
                target = bpy.data.objects.new('ZZZ Wardrobe turn · '+name, None)
                s.scene.collection.objects.link(target)
                s.restore.callback(bpy.data.objects.remove, target, do_unlink=True)
                target.hide_render = True
                target.empty_display_size = .001
                target.rotation_mode = 'QUATERNION'
                c = bone.constraints.new('COPY_ROTATION')
                s.restore.callback(bone.constraints.remove,c)
                c.target = target
                c.owner_space = c.target_space = 'WORLD'
                c.mix_mode = 'BEFORE'
                self.links.append((target, axis, math.radians(asset['turn_limit_degrees'])))
                if calibrated and name in asset.get('blocked_turn_sign', {}):
                    self.blocked_turn_sign[target] = asset['blocked_turn_sign'][name]
                seen.add(name)
        self.ready = True

    def tick(self):
        if not self.enabled:
            return False
        yaw = self.session.yaw
        delta = math.remainder(yaw - self.yaw, math.tau)
        now = time.perf_counter()
        dt = min(.25, max(.001, now - self.clock))
        self.clock = now
        if not delta and not self.active:
            return False
        if not self.ready:
            self.prepare()
        self.yaw = yaw
        # Critically damped response integrated in wall-clock time, even when
        # the viewport skips frames. Values describe the Blender adaptation.
        omega = 12.
        self.lag = max(-.35, min(.35, self.lag + delta))
        slope = self.velocity + omega * self.lag
        decay = math.exp(-omega * dt)
        self.lag, self.velocity = (self.lag + slope * dt) * decay, (self.velocity - omega * slope * dt) * decay
        self.active = abs(self.lag) > .0008 or abs(self.velocity) > .005
        if not self.active:
            self.lag = self.velocity = 0.
        world_turn = Quaternion((0,0,1),yaw)
        for target, axis, limit in self.links:
            angle = max(-limit, min(limit, axis.length * self.lag))
            # Two fitted skirt drivers reach the fan on an inward swing.
            # Keep their baked motion on that side and allow the outward response.
            if self.blocked_turn_sign.get(target, 0) * angle > 0:
                angle = 0.
            target.rotation_quaternion = Quaternion(world_turn @ axis.normalized(), angle)
        return True
