"""Explicit replacement from exported Unity Mesh JSON, with verified skeleton.

Requires caller-selected source and the documented X-reflection convention.
Does not infer a character, download resources or change materials/animation.
"""
import json,hashlib,zlib
import numpy as np

def replace(ob,path,basis,*,bind_tolerance=2e-5):
    import bpy
    from pathlib import Path
    from . import character
    if not 0 < bind_tolerance <= 5e-5:
        raise ValueError('Explicit bind-origin tolerance must be at most 50 micrometers')
    if list(basis)!=[-1,1,1]:raise ValueError('Only verified X-reflected mesh convention supported')
    p=Path(path);d=json.loads(p.read_text(encoding='utf-8-sig'))
    arms=[m for m in ob.modifiers if m.type=='ARMATURE']
    if len(arms)!=1 or arms[0].use_deform_preserve_volume:raise ValueError('Requires one linear armature')
    if d['m_Shapes']['vertices']:raise ValueError('Morph mesh replacement is not implemented')
    arm=arms[0].object;lookup={}
    for bone in arm.data.bones:
        chain=[];b=bone
        while b:chain.insert(0,b.name);b=b.parent
        for start in range(len(chain)):
            path='/'.join(chain[start:])
            # Some FBX exports promote the Unity root transform to the
            # armature object (e.g. Bone_Root), instead of retaining a bone.
            for candidate in (path,arm.name+'/'+path):
                h=zlib.crc32(candidate.encode())
                if h in lookup and lookup[h]!=bone.name:raise ValueError('Ambiguous bone path hash')
                lookup[h]=bone.name
    hashes=d['m_BoneNameHashes']
    if not hashes or len(hashes)!=len(d['m_BindPose']):raise ValueError('Invalid bone/bind-pose streams')
    if any(h not in lookup for h in hashes):raise ValueError('Source bone missing in destination armature')
    F=np.diag([*basis,1.]);errors=[]
    for h,bind in zip(hashes,d['m_BindPose']):
        raw=np.array([[bind[f'M{i}{j}'] for j in range(4)] for i in range(4)]).T
        want=(F@np.linalg.inv(raw))[:3,3];got=np.array(ob.matrix_world.inverted()@arm.matrix_world@arm.data.bones[lookup[h]].matrix_local)[:3,3]
        errors.append(float(np.linalg.norm(want-got)))
    if max(errors)>bind_tolerance:raise ValueError('Source bind-pose bone origins differ from destination skeleton')
    pos=np.array(d['m_Vertices']).reshape(-1,3)*basis;n=np.array(d['m_Normals']).reshape(-1,3)*basis
    tangent=np.array(d['m_Tangents']).reshape(-1,4);tangent[:,:3]*=basis;tangent[:,3]*=-1
    tri=np.array(d['m_Indices']).reshape(-1,3)[:,[0,2,1]];old=ob.data
    if len(d['m_Skin'])!=len(pos) or tri.max()>=len(pos) or tri.min()<0:raise ValueError('Invalid native mesh streams')
    if n.shape!=pos.shape or tangent.shape!=(len(pos),4) or not all(np.isfinite(a).all() for a in (pos,n,tangent)):raise ValueError('Invalid native vertex inputs')
    if len(d['m_UV0'])!=len(pos)*2 or len(d['m_UV1'])!=len(pos)*2 or len(d['m_Colors'])!=len(pos)*4:raise ValueError('Incomplete native UV/colour inputs')
    count=0
    for i,sub in enumerate(d['m_SubMeshes']):
        if i>=len(old.materials) or sub['topology']!='Triangles' or sub['baseVertex']!=0 or sub['firstByte']!=count*2 or sub['indexCount']%3:raise ValueError('Unsupported submesh layout; requires contiguous 16-bit triangle indices and existing material slots')
        count+=sub['indexCount']
    if count!=tri.size:raise ValueError('Submesh ranges do not cover index stream')
    for skin in d['m_Skin']:
        if len(skin['boneIndex'])!=4 or len(skin['weight'])!=4 or any(i<0 or i>=len(hashes) for i in skin['boneIndex']) or not np.isfinite(skin['weight']).all() or min(skin['weight'])<0 or abs(sum(skin['weight'])-1)>2e-4:raise ValueError('Invalid skin weights')
    me=bpy.data.meshes.new(old.name+'_Native');me.from_pydata(pos.tolist(),[],tri.tolist());me.update();loop=np.array([l.vertex_index for l in me.loops])
    for m in old.materials:me.materials.append(m)
    # Shader rebuilding resolves the original material JSON names, not the
    # current ZZZ_* generated material names. Keep that mapping across remesh.
    if 'zzz_slots' in old:me['zzz_slots']=list(old['zzz_slots'])
    for i,sub in enumerate(d['m_SubMeshes']):
        if i>=len(me.materials):raise ValueError('Source material slot missing')
        first=sub['firstByte']//2//3;count=sub['indexCount']//3
        for poly in list(me.polygons)[first:first+count]:poly.material_index=i
    for i in range(8):
        uv=np.array(d.get(f'm_UV{i}',[]))
        if uv.size==len(pos)*2:me.uv_layers.new(name=f'UV{i}').data.foreach_set('uv',uv.reshape(-1,2)[loop].ravel())
    me.uv_layers.active=me.uv_layers['UV0'];me.uv_layers['UV0'].active_render=True
    col=np.array(d['m_Colors']).reshape(-1,4);ca=me.color_attributes.new(name='Attribute',type='FLOAT_COLOR',domain='CORNER');ca.data.foreach_set('color_srgb',col[loop].ravel())
    me.polygons.foreach_set('use_smooth',[True]*len(me.polygons));me.normals_split_custom_set_from_vertices(n.tolist())
    ob.data=me;ob.vertex_groups.clear();groups=[ob.vertex_groups.new(name=lookup[h]) for h in hashes]
    for i,skin in enumerate(d['m_Skin']):
        for bone,weight in zip(skin['boneIndex'],skin['weight']):
            if weight>0:groups[bone].add([i],weight,'REPLACE')
    character.bake_surface_flags(me)
    report={'source':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'previous_vertices':len(old.vertices),'vertices':len(pos),'triangles':len(tri),'bones':len(hashes),'max_bind_origin_error':max(errors),'basis':list(basis)}
    ob['zzz_native_mesh_source']=json.dumps(report,sort_keys=True)
    return report,dict(position=pos,normal=n,tangent=tangent[:,:3],packed=np.c_[np.array(d['m_UV1']).reshape(-1,2),tangent[:,3]],raw_color=col,valid=np.ones(len(pos),bool),loop_vertex=loop,uv0=np.array(d['m_UV0']).reshape(-1,2)[loop])
