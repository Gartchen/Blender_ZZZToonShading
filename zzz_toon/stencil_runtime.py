"""Opt-in CPU coverage adapter for the verified native stencil caster.

Projection is native_stencil.project; raster coverage is an explicit Blender
approximation, not recovered D3D stencil state. One receiver profile per scene.
Rebuilds on frame change and before render; never reuses a failed update.
"""
import json,time
import numpy as np
from .native_stencil import project

KEY='zzz_stencil_runtime'
_busy=False


def validate(profile):
    if profile is None:return None
    if profile.get('schema')!='zzz-toon-stencil-coverage/1':raise ValueError('Unknown stencil coverage schema')
    for key in ('source','scope','caster','inputs','unity_to_blender','red_path'):
        if not profile.get(key):raise ValueError('Stencil coverage requires '+key)
    if profile.get('vs')!='bcddceab76ee10ce' or profile.get('ps')!='c07b54c6554b2591':raise ValueError('Unverified caster variant')
    matrix=np.asarray(profile['unity_to_blender'],dtype=float)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix))<1e-8:raise ValueError('Invalid scene registration')
    sizes={'_AvatarMainLightPosition':3,'_PackedParams0':4,'_OverrideHairShadowMainLight':1,'_OverrideHairShadowMainLightDir':3,'_ZOffset':1,'_Length':1,'_XOffsetNew':1,'_YOffset':1,'_XOffsetEnter':1,'_YOffsetEnter':1,'_StencilShadowBlendDebugMode':1,'_ClipSpaceOffset':2}
    for key,size in sizes.items():
        value=np.asarray(profile['inputs'].get(key,[]),dtype=float)
        if value.ndim!=1 or len(value)<size or not np.isfinite(value).all():raise ValueError('Missing/nonfinite captured input: '+key)
    return profile


def raster_depth(clip,tri,w,h,roi=None):
    """Pixel-centre reversed-Z coverage. No original MSAA/stencil-state claim."""
    depth=np.full((h,w),-np.inf)
    bounds=(0,0,w-1,h-1) if roi is None else roi
    tri=tri[(clip[tri,3]>0).all(axis=1)]
    if not len(tri):
        return depth
    ndc=np.divide(clip[:,:3],clip[:,3:4],out=np.zeros_like(clip[:,:3]),where=clip[:,3:4]!=0)
    xy=(ndc[:,:2]+1)*[w/2,h/2]
    pts=xy[tri]
    a,b,c=pts.transpose(1,0,2)
    area=(b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0])
    lo=np.maximum(np.ceil(pts.min(axis=1)-.5),bounds[:2])
    hi=np.minimum(np.floor(pts.max(axis=1)-.5),bounds[2:])
    valid=(abs(area)>=1e-10)&(hi>=lo).all(axis=1)
    if not valid.any():
        return depth
    lo,hi=lo[valid].astype('i8'),hi[valid].astype('i8')
    width,height=(hi-lo+1).T
    ends=np.cumsum(width*height)
    starts=ends-width*height
    a,b,c=pts[valid].transpose(1,0,2)
    area=area[valid]
    z=ndc[tri[valid],2]
    # Batch independent pixel centres. Keeping the same edge tolerance and
    # reversed-Z maximum preserves the scalar raster's shared-edge behaviour.
    for i in range(0,int(ends[-1]),131072):
        samples=np.arange(i,min(i+131072,ends[-1]))
        k=np.searchsorted(ends,samples,side='right')
        dy,dx=np.divmod(samples-starts[k],width[k])
        ix,iy=lo[k,0]+dx,lo[k,1]+dy
        x,y=ix+.5,iy+.5
        u=((b[k,0]-x)*(c[k,1]-y)-(b[k,1]-y)*(c[k,0]-x))/area[k]
        v=((c[k,0]-x)*(a[k,1]-y)-(c[k,1]-y)*(a[k,0]-x))/area[k]
        t=1-u-v
        inside=(u>=-1e-8)&(v>=-1e-8)&(t>=-1e-8)
        values=u*z[k,0]+v*z[k,1]+t*z[k,2]
        np.maximum.at(depth,(iy[inside],ix[inside]),values[inside])
    return depth


def world_mesh(ob,dg):
    eo=ob.evaluated_get(dg);me=eo.to_mesh()
    try:
        me.calc_loop_triangles();pos=np.empty((len(me.vertices),3),np.float64);me.vertices.foreach_get('co',pos.ravel());tri=np.empty((len(me.loop_triangles),3),np.int32);me.loop_triangles.foreach_get('vertices',tri.ravel());m=np.asarray(eo.matrix_world);return pos@m[:3,:3].T+m[:3,3],tri
    finally:eo.to_mesh_clear()


def setup(scene,head,profile):
    import bpy
    validate(profile)
    if head is None:raise ValueError('Stencil coverage needs native face head binding')
    caster=scene.objects.get(profile['caster'])
    if caster is None or caster.type!='MESH':raise ValueError('Caster mesh not found')
    caster.data.calc_loop_triangles();tri=np.array([t.vertices[:] for t in caster.data.loop_triangles])
    with np.load(profile['red_path']) as data:
        red=data['red'].copy();expected=data['tri']
        if len(red)!=len(caster.data.vertices) or tri.shape!=expected.shape or not np.array_equal(np.sort(tri,axis=1),np.sort(expected,axis=1)):raise ValueError('Caster topology does not match original float red stream')
    if not np.isfinite(red).all():raise ValueError('Nonfinite caster red')
    at=caster.data.attributes.get('zzz_caster_red') or caster.data.attributes.new('zzz_caster_red','FLOAT','POINT');at.data.foreach_set('value',red.astype(np.float32))
    name='ZZZ_NativeReceiver_'+scene.name;image=bpy.data.images.get(name) or bpy.data.images.new(name,width=1,height=1,alpha=True,float_buffer=True);image.colorspace_settings.name='Non-Color'
    scene[KEY]=json.dumps({'profile':profile,'head':head.name,'image':image.name},sort_keys=True)
    register();return coverage(scene)


def coverage(scene):
    import bpy
    image=bpy.data.images[json.loads(scene[KEY])['image']]
    def build(b):
        coord=b.node('ShaderNodeTexCoord');tex=b.node('ShaderNodeTexImage');tex.image=image;tex.interpolation='Closest';tex.extension='CLIP';tex.label='Dynamic projected receiver coverage';b.link(coord.outputs['Window'],tex.inputs[0]);return tex.outputs['Color']
    return build


def update(scene):
    import bpy
    if KEY not in scene:return None
    start=time.perf_counter();spec=json.loads(scene[KEY]);prof=spec['profile'];image=bpy.data.images[spec['image']]
    # Clear before calculation: invalid geometry/camera cannot retain stale shadow.
    w=max(1,scene.render.resolution_x*scene.render.resolution_percentage//100);h=max(1,scene.render.resolution_y*scene.render.resolution_percentage//100)
    if tuple(image.size)!=(w,h):image.scale(w,h)
    blank=np.zeros((h,w,4),np.float32);blank[...,3]=1;image.pixels.foreach_set(blank.ravel());image.update()
    validate(prof)
    if scene.camera is None or scene.camera.data.type!='PERSP' or scene.render.use_border:raise ValueError('Receiver coverage currently requires a full perspective render')
    layer=scene.view_layers[0];layer.update();caster=scene.objects[prof['caster']];head=scene.objects[spec['head']]
    mod=caster.modifiers.get(prof.get('excluded_modifier',''));flags=(mod.show_viewport,mod.show_render) if mod else None
    try:
        if mod:mod.show_viewport=False;mod.show_render=False;layer.update()
        dg=layer.depsgraph;world,tri=world_mesh(caster,dg)
    finally:
        if mod:mod.show_viewport,mod.show_render=flags;layer.update()
    dg=layer.depsgraph;red=np.empty(len(caster.data.vertices),np.float32);caster.data.attributes['zzz_caster_red'].data.foreach_get('value',red)
    if len(world)!=len(red):raise ValueError('Evaluated caster topology changed')
    a=np.asarray(prof['unity_to_blender']);ia=np.linalg.inv(a);q=np.asarray(scene.camera.calc_matrix_camera(dg,x=w,y=h,scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y))@np.asarray(scene.camera.matrix_world.inverted())
    flip=np.diag([1.,-1.,-.5,1.]);flip[2,3]=.5;vp=flip@q@a;p=dict(prof['inputs']);off=np.asarray(p['_ClipSpaceOffset'][:2]);vp[:2]-=off[:,None]*vp[3]
    m=ia@np.asarray(head.matrix_world)@np.diag([-1.,1.,1.,1.]);unity=np.c_[world,np.ones(len(world))]@ia.T;local=(unity@np.linalg.inv(m).T)[:,:3];cam=ia@np.r_[np.asarray(scene.camera.matrix_world.translation),1.]
    p.update(unity_ObjectToWorld=m.T.ravel().tolist(),unity_MatrixVP=vp.T.ravel().tolist(),_WorldSpaceCameraPos=cam[:3].tolist())
    clip=project(local,red,p);z=raster_depth(clip,tri,w,h);ys,xs=np.where(np.isfinite(z));depth=np.full((h,w),-np.inf)
    if len(xs):
        roi=(int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max()))
        for ob in scene.objects:
            if ob.type!='MESH' or ob==caster or ob.hide_render or ob.get('zzz_outline_helper') or ob.name in prof.get('excluded_depth_objects',[]) or not ob.visible_get(view_layer=layer):continue
            ps,tr=world_mesh(ob,dg)
            if len(tr):depth=np.maximum(depth,raster_depth(np.c_[ps,np.ones(len(ps))]@(flip@q).T,tr,w,h,roi))
    mask=np.isfinite(z)&(z>=depth);blank[...,:3]=mask[::-1,:,None];image.pixels.foreach_set(blank.ravel());image.update()
    result={'frame':scene.frame_current,'size':[w,h],'mask_pixels':int(mask.sum()),'seconds':time.perf_counter()-start}
    scene['zzz_stencil_status']=json.dumps(result);scene.pop('zzz_stencil_error',None);return result


def _handler(scene,*args):
    global _busy
    if _busy or KEY not in scene:return
    from .fullscreen import active_for
    if active_for(scene):return
    _busy=True
    try:update(scene)
    except Exception as error:
        scene['zzz_stencil_error']=str(error)
        print('ZZZ receiver coverage failed; stale coverage cleared:',error,flush=True)
    finally:_busy=False


def _load(*args):
    import bpy
    for scene in bpy.data.scenes:_handler(scene)


def register():
    import bpy
    for collection,fn in [(bpy.app.handlers.frame_change_post,_handler),(bpy.app.handlers.render_pre,_handler),(bpy.app.handlers.load_post,_load)]:
        bpy.app.handlers.persistent(fn)
        if fn not in collection:collection.append(fn)


def unregister():
    import bpy
    for collection,fn in [(bpy.app.handlers.frame_change_post,_handler),(bpy.app.handlers.render_pre,_handler),(bpy.app.handlers.load_post,_load)]:
        if fn in collection:collection.remove(fn)
