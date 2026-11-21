"""NapAvatarUI shape-highlight sphere (PS1119 lines 546-564).

Only the shape-highlight normal and light mask change. Diffuse, shadow,
regular specular and rim continue using their original normals.
"""
import json,math

def validate(profile):
    if profile is None:return None
    if profile.get('schema')!='zzz-toon-head-sphere/1':raise ValueError('Unknown head-sphere schema')
    if not profile.get('source') or not profile.get('scope') or not profile.get('bone'):
        raise ValueError('Head sphere requires source, scope and an explicit bone')
    c=profile.get('center_bone_local');r=profile.get('radius')
    if not isinstance(c,(list,tuple)) or len(c)!=3 or any(not math.isfinite(v) for v in c):
        raise ValueError('Head sphere requires a finite bone-local center')
    if not isinstance(r,(int,float)) or not math.isfinite(r) or r<0:
        raise ValueError('Head-sphere radius must be a nonnegative world-space value')
    return profile

def bind(arm,scene,profile):
    profile=validate(profile)
    if profile is None or profile['radius']==0:return None
    import bpy
    from mathutils import Vector
    if arm is None or profile['bone'] not in arm.pose.bones:
        raise ValueError('Head-sphere binding bone not found: '+profile['bone'])
    name='ZZZ_HeadSphere_'+arm.name
    ob=bpy.data.objects.get(name)
    if ob is None:
        ob=bpy.data.objects.new(name,None);scene.collection.objects.link(ob)
    ob.parent=arm;ob.parent_type='BONE';ob.parent_bone=profile['bone']
    ob.matrix_parent_inverse.identity();ob.matrix_basis.identity()
    # Blender bone parenting is relative to the bone's tail. The measured
    # transform is relative to its origin, so cancel that exact tail offset.
    ob.location=Vector(profile['center_bone_local'])-Vector((0,arm.data.bones[profile['bone']].length,0))
    ob.empty_display_type='SPHERE';ob.empty_display_size=profile['radius'];ob.hide_render=True
    ob['zzz_head_sphere_profile']=json.dumps(profile,sort_keys=True)
    return ob

def shape_inputs(b,normal,light,position,center,radius):
    """Return blended normal and sqrt(half-Lambert). No normalization of blend."""
    def vec(op,a,c=None):
        n=b.node('ShaderNodeVectorMath',operation=op)
        for i,v in enumerate((a,c)):
            if v is None:continue
            if isinstance(v,tuple):n.inputs[i].default_value=v
            else:b.link(v,n.inputs[i])
        return n.outputs['Value' if op in ('DOT_PRODUCT','LENGTH') else 0]
    delta=vec('SUBTRACT',position,center);dist=vec('LENGTH',delta)
    fade=b.math('SUBTRACT',1.,b.math('MULTIPLY',b.math('SUBTRACT',dist,radius),20.,clamp=True))
    sphere=vec('NORMALIZE',delta)
    diff=vec('SUBTRACT',sphere,normal)
    scale=b.node('ShaderNodeVectorMath',operation='SCALE');b.link(diff,scale.inputs[0]);b.link(fade,scale.inputs['Scale'])
    blended=vec('ADD',normal,scale.outputs[0]);blended.node.label='Native head-sphere shape normal (not normalized)'
    half=b.math('MULTIPLY_ADD',vec('DOT_PRODUCT',blended,light),.5,clamp=True);half.node.inputs[2].default_value=.5
    mask=b.math('SQRT',half);mask.node.label='Native head sphere sqrt half-Lambert'
    return blended,mask

def center_socket(b,obj):
    node=b.node('ShaderNodeCombineXYZ');node.label='Head sphere world center (bone-bound)'
    for i,axis in enumerate('XYZ'):
        drv=node.inputs[i].driver_add('default_value').driver;drv.type='AVERAGE'
        v=drv.variables.new();v.name='center';v.type='TRANSFORMS'
        t=v.targets[0];t.id=obj;t.transform_type='LOC_'+axis;t.transform_space='WORLD_SPACE'
    return node.outputs[0]
