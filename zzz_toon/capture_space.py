"""Register a measured PS1119 environment to the imported outfit's Blender world."""
from collections import Counter
import hashlib
import json
import re
import struct

import numpy as np

from .outfit_import import BASIS
from . import native_environment


FACE_VS = 'bd0cf06981a8f054'
FEATURE_PASSES = {
    (FACE_VS, '202c3cc697d36317'): 'face',
    (FACE_VS, '3969ce67a7c8092e'): 'face_or_brow',
    ('a03f4eeb03077f4c','720a454f81097a64'): 'eye',
    ('c280f6945b23a42a','050d63a297594dff'): 'fringe',
    ('c280f6945b23a42a','1bccd522268b6343'): 'fringe',
}
ENTITY_DISPATCH = re.compile(r'^(\d+)-cs-t5=.+?-cs=2fca446aa62a4f29\.buf$',re.I)
ANCHOR_KEY = 'zzz_capture_world_registration'


def _uv_triangles(coords):
    # The FBX may split vertices and reverse winding. Keep multiplicity and both UV axes.
    if not np.isfinite(coords).all():
        raise ValueError('Invalid feature UV')
    uv = np.array(coords,dtype='<f4',order='C',copy=True)
    uv[uv==0] = 0
    triples = np.sort(uv.view('<u8').reshape(-1,3),axis=1)
    return Counter(map(bytes,triples.view('V24').ravel()))


def match_features(features, objects, read_triangles):
    slots = {}
    for obj in objects:
        if obj.type!='MESH' or obj.get('zzz_outline_helper'):
            continue
        mesh = obj.data
        uv = mesh.uv_layers.get('UV0') or mesh.uv_layers.active
        if not uv or len(mesh.loops)!=3*len(mesh.polygons):
            continue
        coords = np.empty(2*len(mesh.loops),'f')
        uv.data.foreach_get('uv',coords)
        indices = np.empty(len(mesh.polygons),'i')
        mesh.polygons.foreach_get('material_index',indices)
        names = mesh.get('zzz_slots') or [m.name.split('.')[0] if m else '' for m in mesh.materials]
        # The material builder moves eyelid triangles into a generated slot. Reconstruct
        # the original FBX slot for capture comparison without changing the mesh.
        lid = mesh.get('zzz_eyeshadow_slot',-1)
        if len(names)<=lid<len(mesh.materials) and mesh.materials[lid] and mesh.materials[lid].name=='ZZZ_EyeShadow':
            source = mesh.get('zzz_eyeshadow_source')
            if source is None:
                from .character import kind_of
                faces = [i for i,name in enumerate(names) if kind_of(name)=='face']
                source = faces[0] if len(faces)==1 else -1
            if 0<=source<len(names):
                indices[indices==lid] = source
        for index,name in enumerate(names):
            if name and np.any(indices==index):
                triangles = coords.reshape(-1,3,2)[indices==index]
                if np.isfinite(triangles).all():
                    slots[obj.name,index,name] = _uv_triangles(triangles)
    report = {}
    for event,(pair,files) in sorted(features.items()):
        row = dict(kind=FEATURE_PASSES[pair],vs=pair[0],ps=pair[1],status='unmatched')
        report[event] = row
        try:
            parsed = read_triangles(files)
            if parsed is None:
                continue
            indices,uv,active = parsed
            signature = _uv_triangles(uv[indices[active]])
        except (KeyError, OSError, ValueError, IndexError):
            continue
        row['triangles'] = signature.total()
        exact = [key for key,other in slots.items() if signature==other]
        if len(exact)==1:
            row['object'],row['slot'],row['material'] = exact[0]
            row['status'] = 'exact_uv_triangles'
        elif exact:
            row['status'] = 'ambiguous'
        else:
            # Diagnostics only. An intersection never authorizes a material change.
            overlaps = [(sum((signature & other).values()),key) for key,other in slots.items()]
            best = max((count for count,_ in overlaps),default=0)
            candidates = [key for count,key in overlaps if count==best]
            if best and len(candidates)==1:
                row.update(overlap=best,candidate_object=candidates[0][0],candidate_material=candidates[0][2])
    return report


def _vec(data,offset,count):
    values = np.array(struct.unpack_from(f'<{count}f',data,offset))
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite spatial environment value')
    return values


def _source(files):
    cb0 = files['ps-cb0.buf'].read_bytes()
    cb2 = files['ps-cb2.buf'].read_bytes()
    if (len(cb0),len(cb2))!=(3392,656):
        raise ValueError('Unsupported spatial constant-buffer layout')
    index = float(_vec(cb2,40,1)[0])
    records = files['ps-t1.buf'].read_bytes()
    if not index.is_integer() or not 0<=index<len(records)//128 or len(records)%128:
        raise ValueError('Invalid PS1119 entity selection')
    entity = _vec(records,128*int(index),7)
    return dict(main_color=_vec(cb0,3152,3).tolist(),ambient_color=_vec(cb0,32,3).tolist(),
                gradient_color=_vec(cb0,240,4).tolist(),plane=_vec(cb2,48,4).tolist(),
                entity_color=entity[:3].tolist(),entity_range=float(entity[3]),
                entity_position=entity[4:7].tolist(),index=int(index))


def _point(point):
    try:
        p = np.asarray(point,dtype=float)
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError('Expected a finite 3D registration point') from exc
    if p.shape!=(3,) or not np.isfinite(p).all():
        raise ValueError('Expected a finite 3D registration point')
    return p


def _head_registration(objects, matched, features, feature_matches, dispatches, index, folder):
    arms = [ob for ob in objects if ob.type=='ARMATURE']
    if len(arms)!=1:
        raise ValueError('A unique imported armature is required for head registration')
    arm = arms[0]
    # The standard humanoid export names this joint Bip001 Head. Require its anatomical
    # parent and face geometry from the same rig as additional identity checks.
    bone = arm.data.bones.get('Bip001 Head')
    if not bone or not bone.parent or bone.parent.name!='Bip001 Neck':
        raise ValueError('Unknown FBX head joint; supply a registered space')
    members = {o.name for o in arm.children_recursive}
    if not set(matched.values()).issubset(members):
        raise ValueError('Matched body geometry belongs to a different rig')
    heads = []
    for event,row in feature_matches.items():
        if row['vs']!=FACE_VS or row['status']!='exact_uv_triangles' or row['object'] not in members:
            continue
        earlier = [n for n in dispatches if n<int(event)]
        if not earlier:
            continue
        producer = max(earlier)
        if not any(producer<int(draw)<int(event) for draw in matched):
            continue
        data = dispatches[producer].read_bytes()
        if len(data)%224 or (index+1)*224>len(data):
            continue
        head = _vec(data,index*224+32,3)
        inverse = _vec(data,index*224+96,16).reshape(4,4).T
        files = features[event][1]
        if 'vs-cb1.buf' not in files:
            continue
        cb = files['vs-cb1.buf'].read_bytes()
        if len(cb)!=464:
            continue
        forward,back = _vec(cb,0,32).reshape(2,4,4).transpose(0,2,1)
        if (np.allclose(head,forward[:3,3],rtol=0,atol=2e-5)
                and np.allclose(inverse,back,rtol=0,atol=8e-4)
                and np.allclose(forward@back,np.eye(4),rtol=0,atol=.002)):
            heads.append((head,event,producer))
    if not heads or any(not np.allclose(p,heads[0][0],rtol=0,atol=2e-5) for p,_,_ in heads):
        raise ValueError('No unique entity head verified by the matching face draws')
    head,event,producer = heads[0]
    # A frozen translation places the captured head at the FBX bind head. Do not
    # parent lighting to the head, or recompute this placement when the timeline moves.
    token = hashlib.sha256((str(folder.resolve())+'|'+bone.name).encode()+head.tobytes()).hexdigest()
    saved = json.loads(arm.get(ANCHOR_KEY,'{}'))
    target = (_point(saved['blender_point']) if saved.get('key')==token else
              _point(arm.matrix_world @ bone.head_local))
    return dict(method='fbx_bind_head',key=token,bone=bone.name,unity_point=head.tolist(),
                blender_point=target.tolist(),compute_draw=producer,face_draw=event)


def environment(folder, objects, matched, draws, features, feature_matches, dispatches, registration):
    rows = [_source(draws[event]) for event in matched]
    if not rows or any(row!=rows[0] for row in rows[1:]):
        raise ValueError('Matched body draws disagree on spatial inputs')
    values = rows[0].copy()
    index = values.pop('index')
    if registration is None:
        placement = _head_registration(objects,matched,features,feature_matches,dispatches,index,folder)
    else:
        # Explicit points belong to the named native constant buffer of this frame.
        hashes = {hashlib.sha256(draws[event]['ps-cb0.buf'].read_bytes()).hexdigest() for event in matched}
        if (not isinstance(registration,dict) or registration.get('schema')!='zzz-toon-capture-space/1'
                or not isinstance(registration.get('source'),str) or not registration['source'].strip()
                or not isinstance(registration.get('body_cb0_sha256'),str)
                or registration.get('body_cb0_sha256') not in hashes
                or registration.get('frame_log_sha256') != hashlib.sha256((folder/'log.txt').read_bytes()).hexdigest()):
            raise ValueError('Explicit space source does not match the selected frame')
        placement = dict(method='registered_points',source=registration['source'],
                         unity_point=_point(registration['unity_point']).tolist(),
                         blender_point=_point(registration['blender_point']).tolist())
    basis = np.asarray(BASIS,dtype=float)
    origin = _point(placement['unity_point']) - basis.T @ _point(placement['blender_point'])
    plane = np.asarray(values.pop('plane'))
    values['entity_position'] = (basis @ (_point(values['entity_position'])-origin)).tolist()
    return native_environment.validate(dict(schema='zzz-toon-environment/1', source=dict(frame=str(folder),
        body_draws=sorted(matched),entity_index=index),scope='Frozen placement of this captured environment',
        registration=placement,gradient_direction=(basis@plane[:3]).tolist(),
        gradient_offset=float(plane[3]-plane[:3]@origin),gradient_enabled=bool(plane[3]!=0), **values))
