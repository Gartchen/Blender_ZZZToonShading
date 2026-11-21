"""Configure an animated outfit preview in its own Blender project."""
import json
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import bpy
import numpy as np
from mathutils import Matrix,Vector
from bpy.props import IntProperty

from . import anim,character,clip_io,native_sampling,preview,wardrobe_page,stencil_runtime,wardrobe_slots

DATA=Path(__file__).with_name('wardrobe_data')
ROLES=wardrobe_slots.ROLES
KEY='zzz_wardrobe_camera'
KEYMAPS=[]
_pending={}
_jobs=[]


def _receiver_refresh():
    now=time.monotonic()
    for name,when in list(_pending.items()):
        if now>=when:
            scene=bpy.data.scenes.get(name)
            if scene and scene==bpy.context.scene:
                bpy.context.view_layer.update()
                if scene.get('zzz_sampling_uniform_group'):
                    native_sampling.update(scene)
                if stencil_runtime.KEY in scene:
                    stencil_runtime._handler(scene)
            _pending.pop(name,None)
    return .08 if _pending else None


def locate(settings):
    return wardrobe_slots.request(settings)


def apply_material_look(scene, role):
    path=DATA/role/'render_look.json'
    if not path.is_file():
        return
    look=json.loads(path.read_text(encoding='utf8'))
    for name,multiplier in look['linear_material_rgb'].items():
        material=bpy.data.materials[name]
        tree=material.node_tree
        emission,=[node for node in tree.nodes if node.type=='EMISSION']
        color=emission.inputs['Color']
        source=color.links[0].from_socket
        tint=tree.nodes.new('ShaderNodeVectorMath')
        tint.label='Wardrobe · video-matched material color'
        tint.operation='MULTIPLY'
        tint.inputs[1].default_value=multiplier
        tint.location=(emission.location.x-190,emission.location.y-90)
        tree.links.new(source,tint.inputs[0])
        tree.links.new(tint.outputs['Vector'],color)
    scene['zzz_wardrobe_render_revision']=look['revision']


def camera_stops(camera, role):
    limits=json.loads(camera[KEY])
    if role=='Vivian':
        # The close recording measures 3.3% greater scale and more headroom.
        # At the far stop leave clearance for the canopy and shoes during turns.
        limits['near']['lens']*=1.033
        limits['near']['shift_y']+=.040
        limits['far']['lens']*=1.02
        limits['far']['shift_y']-=.004
    return limits


def asset_signature(objects):
    """Verify a re-export against the data the stored mesh/material profiles address."""
    result={}
    for ob in objects:
        if ob.type not in ('MESH','ARMATURE'):
            continue
        digest=hashlib.sha256()
        def numbers(array):
            a=np.asarray(array)
            if a.dtype.kind=='f':
                a=np.rint(a*100000)
            digest.update(a.astype('<i8').tobytes())
        def name(value):
            digest.update(value.encode('utf8')+b'\0')
        numbers(ob.matrix_world)
        if ob.type=='ARMATURE':
            for b in sorted(ob.data.bones,key=lambda b:b.name):
                name(b.name)
                name(b.parent.name if b.parent else '')
                numbers(b.matrix_local)
        else:
            me=ob.data
            for seq,field,width,kind in ((me.vertices,'co',3,'f'),(me.loops,'vertex_index',1,'i'),
                                        (me.polygons,'material_index',1,'i'),(me.corner_normals,'vector',3,'f')):
                data=np.empty(len(seq)*width,kind)
                seq.foreach_get(field,data)
                numbers(data)
            for material in me.materials:
                name(material.name if material else '')
            for layer in me.uv_layers:
                name(layer.name)
                uv=np.empty(len(layer.data)*2,'f')
                layer.data.foreach_get('uv',uv)
                numbers(uv)
            for layer in me.color_attributes:
                name(layer.name)
                colors=np.empty(len(layer.data)*4,'f')
                layer.data.foreach_get('color',colors)
                numbers(colors)
            groups={g.index:g.name for g in ob.vertex_groups}
            for v in me.vertices:
                weights=sorted((groups[g.group],g.weight) for g in v.groups)
                numbers(len(weights))
                for group,weight in weights:
                    name(group)
                    numbers(weight)
        result[ob.name]=digest.hexdigest()
    return result


def set_zoom(settings,context):
    scene=settings.id_data
    from .fullscreen import active_for
    session=active_for(scene)
    if session:
        if not session.closed:
            if session.playback:
                session.playback.invalidate()
            session.camera()
        return
    cam=scene.camera
    if not cam or KEY not in cam:
        return
    bounds=camera_stops(cam,settings.wardrobe_role)
    t=settings.wardrobe_zoom
    far,near=bounds['far'],bounds['near']
    cam.matrix_world=Matrix(far['world']).lerp(Matrix(near['world']),t)
    for key in ('lens','shift_x','shift_y'):
        setattr(cam.data,key,far[key]+t*(near[key]-far[key]))
    from .preview_layers import QUALITY_ROLES
    if settings.wardrobe_role in QUALITY_ROLES:
        axis=bounds.get('focus_axis')
        if axis is None:
            rig=next((ob for ob in scene.objects if ob.type=='ARMATURE' and ob.pose.bones.get('Bip001 Head')),None)
            if rig:
                origin=rig.matrix_world@rig.pose.bones['Bip001 Head'].head
                axis=(origin.x,origin.y)
        if axis:
            lo,hi=bounds['bounds']
            height=(lo[2]+hi[2])*.5*(1-t)+(hi[2]-.12)*t
            focus=cam.matrix_world.inverted()@Vector((*axis,height))
            cam.data.shift_x=cam.data.lens/cam.data.sensor_width*focus.x/max(.01,-focus.z)
    # Apply every input event to the camera immediately. Coalesce costly
    # dependency-graph evaluations while scrolling; each render refreshes both.
    sampling=scene.get('zzz_sampling_uniform_group')
    if bpy.app.background:
        if sampling:
            native_sampling.update(scene)
    elif sampling or stencil_runtime.KEY in scene:
        _pending[scene.name]=time.monotonic()+.12
        if not bpy.app.timers.is_registered(_receiver_refresh):
            bpy.app.timers.register(_receiver_refresh,first_interval=.12)
    if context and context.screen:
        for area in context.screen.areas:
            if area.type=='VIEW_3D':
                area.tag_redraw()


def bounds(scene,meshes):
    lo,hi=np.full(3,np.inf),np.full(3,-np.inf)
    for frame in np.linspace(scene.frame_start,scene.frame_end,9).astype(int):
        scene.frame_set(int(frame))
        dg=bpy.context.evaluated_depsgraph_get()
        for obj in meshes:
            if obj.hide_render or not obj.visible_camera:
                continue
            ev=obj.evaluated_get(dg)
            coords=np.empty(len(ev.data.vertices)*3,'f')
            ev.data.vertices.foreach_get('co',coords)
            if coords.size:
                m=np.asarray(ev.matrix_world)
                world=coords.reshape(-1,3)@m[:3,:3].T+m[:3,3]
                lo=np.minimum(lo,world.min(axis=0))
                hi=np.maximum(hi,world.max(axis=0))
    scene.frame_set(scene.frame_start)
    if not np.isfinite([lo,hi]).all() or hi[2]-lo[2]<.1:
        raise ValueError('No visible animated character geometry')
    return lo,hi


def zoom_limits(scene,meshes,role,page):
    lo,hi=bounds(scene,meshes)
    center=(lo+hi)/2
    near=page['models'][role].get('near')
    if near is None:
        # Vivian has a measured full-body capture; a close recording is not available.
        # Frame her animated head at the same head-to-frame scale as the two video closeups.
        other=page['models']['Velina']['near']
        rig=next(o for o in scene.objects if o.type=='ARMATURE')
        head=rig.matrix_world@rig.pose.bones['Bip001 Head'].head
        m=Matrix.Rotation(math.pi/2,4,'X')
        m.translation=(head.x,head.y-2.35,head.z-.055)
        near=dict(world=[list(v) for v in m],lens=other['lens'],shift_x=0.,shift_y=0.)
    # The far stop fits the animated outfit including accessories, in a UI-free 16:9 frame.
    lens=near['lens']
    distance=max((hi[0]-lo[0]),(hi[2]-lo[2])*16/9)*1.055*lens/36
    m=Matrix.Rotation(math.pi/2,4,'X')
    m.translation=(center[0],lo[1]-distance,center[2])
    far=dict(world=[list(v) for v in m],lens=lens,shift_x=0.,shift_y=0.)
    limits=dict(far=far,near=near,bounds=[lo.tolist(),hi.tolist()])
    if role in ('Remielle','Velina'):
        rig=next((ob for ob in scene.objects if ob.type=='ARMATURE' and ob.pose.bones.get('Bip001 Head')),None)
        if rig:
            head=rig.matrix_world@rig.pose.bones['Bip001 Head'].head
            limits['focus_axis']=[head.x,head.y]
    return limits


def setup_view(screens=None):
    for screen in (screens if screens is not None else bpy.data.screens):
        for area in screen.areas:
            if area.type=='VIEW_3D':
                view=area.spaces.active
                view.region_3d.view_perspective='CAMERA'
                view.region_3d.view_camera_offset=(0,0)
                view.region_3d.view_camera_zoom=27
                view.overlay.show_overlays=False
                view.shading.type='RENDERED'
                view.shading.use_compositor='CAMERA'


def _open_view():
    for win in bpy.context.window_manager.windows:
        if win.scene.get('zzz_wardrobe'):
            setup_view([win.screen])
    return None


@bpy.app.handlers.persistent
def _load_view(*_):
    for scene in bpy.data.scenes:
        if hasattr(scene,'zzz_toon'):
            wardrobe_slots.legacy(scene.zzz_toon)
    # Blender opens a saved Rendered viewport in Solid mode by default.
    if not bpy.app.background and not bpy.app.timers.is_registered(_open_view):
        bpy.app.timers.register(_open_view,first_interval=.3)


def build(scene,request):
    from . import _apply
    role=request['role']
    source,action=map(Path,(request['model'],request['clip']))
    bg=Path(request['background']) if request.get('background') else None
    index=preview._load(preview.DATA/'index.json', require_files=False)
    spec=index['outfits'][role]
    verified_file=preview.digest(source)==spec['fbx']
    if isinstance(request['textures'],str):
        folder=Path(request['textures'])
        request=dict(request,textures={s.name:str(s.default or folder/s.name)
                     for s in wardrobe_slots.slots(role) if s.prop.startswith('tex_')})
    clip=clip_io.load(action)
    if clip.get('name','').startswith('Avatar_') and role.lower() not in clip['name'].lower():
        raise ValueError('动画与所选角色不匹配：'+clip['name'])
    page=json.loads((DATA/'page.json').read_text(encoding='utf8'))
    cfg=preview._load(preview.DATA/role/'preview.json', require_files=False)
    wardrobe_slots.configure_inputs(role,cfg,request)
    extra=request.get('extras',{})
    aux=Path(extra.get('uv',DATA/role/'uv.fbx')) if spec['uv'] else None
    if aux and not aux.is_file():
        aux = None
    props=scene.zzz_toon
    wardrobe_slots.save_inputs(props,request)
    props.preview_pose=False
    props.skin_smooth=any('ZZZ_SkinSmooth' in o['modifiers'] for o in cfg['stage']['objects'].values())
    props.fbx=str(source)
    props.fbx_uv=str(aux or '')
    props.mat_dir=str(DATA/role/'Materials')
    scene.name=role+' · 服装预览'
    character.setup_scene(scene)
    objects=character.import_fbx(str(source),str(aux) if aux else None)
    if not verified_file:
        reference=json.loads((DATA/role/'geometry.json').read_text(encoding='utf8'))
        if asset_signature(objects)!=reference:
            raise ValueError('模型的骨架、蒙皮、网格或材质槽与所选服装不匹配：'+role)
    from .fbx_support import recover
    from .public_inputs import resolve_support
    generated = recover(source, spec['fbx'], objects, cfg) if verified_file else []
    availability = resolve_support(cfg, index['common'], request, generated)
    scene['zzz_local_inputs'] = json.dumps(availability, ensure_ascii=False)
    arm,=[o for o in objects if o.type=='ARMATURE']
    stage=cfg['stage']
    # The reference camera and lighting are in the registered preview world.
    # Apply the outfit placement while leaving its local bones to the supplied action.
    placement=Matrix.Identity(4)
    registered=page['models'][role].get('idle_placement')
    if registered and clip_io.curve_signature(clip)==registered['curve_hash']:
        placement=Matrix(registered['world'])
    arm.matrix_world=placement@Matrix(stage['rig_world'])
    bpy.context.view_layer.update()
    for obj in objects:
        if obj.name in stage['objects']:
            obj.matrix_world=placement@Matrix(stage['objects'][obj.name]['world'])
    bpy.context.view_layer.update()
    choice=dict(outfit=role,status='Animated outfit preview: '+role,common=index['common'],config=cfg)
    paths=dict(tex='',mats=props.mat_dir,fbx=source,uv=aux,frame=None,preview=choice,fresh=True,
               page='',page_status=None,page_light=None,registration=None)
    preview.camera(scene,cfg['stage'])
    # Prepare the rest rig for a user-supplied action; static capture vertices do not follow it.
    _apply(bpy.context,objects,None,paths)
    apply_material_look(scene,role)
    for light in scene.objects:
        if light.type=='LIGHT':
            row=stage['lights'].get(preview._light_name(light))
            if row:
                if preview._light_name(light)=='ZZZ_HairShadowLight':
                    # This light tests screen-space hair visibility only. The
                    # receiver shader normalizes by camera distance and N.V.
                    light.parent=scene.camera
                    light.matrix_parent_inverse=Matrix.Identity(4)
                    light.matrix_basis=Matrix.Identity(4)
                else:
                    light.parent=None
                    light.matrix_world=Matrix(row['world'])
                light.data.energy=row['energy']
                light.data.color=row['color']
    bone_count,shapes=anim.apply_clip(arm,clip,action_name=role+' · '+action.stem)
    if not bone_count:
        raise ValueError('待机动画没有绑定到所选模型的骨架')
    scene.render.fps,scene.render.fps_base=60,1
    scene.sync_mode='FRAME_DROP'
    scene.frame_start=1
    scene.frame_end=max(1,round(60*clip['stop']))
    from . import wardrobe_dynamics
    wardrobe_dynamics.bake(scene,arm,clip,role,stage)
    scene.render.resolution_x,scene.render.resolution_y=1920,1080
    scene.render.resolution_percentage=100
    scene.render.pixel_aspect_x=scene.render.pixel_aspect_y=1
    scene.render.film_transparent=True
    meshes=[scene.objects[name] for name in stage['objects']]
    # A screen-space receiver raster cannot affect mesh extents. Recompute it
    # once the real camera and resolution are in place.
    receiver=scene.pop(stencil_runtime.KEY,None)
    try:
        limits=zoom_limits(scene,meshes,role,page)
    finally:
        if receiver is not None:
            scene[stencil_runtime.KEY]=receiver
    cam=scene.camera
    cam.name='服装预览相机'
    cam.data.sensor_fit='HORIZONTAL'
    cam.data.sensor_width=36
    cam.data.passepartout_alpha=1
    cam[KEY]=json.dumps(limits)
    from . import wardrobe_face
    wardrobe_face.apply(scene,role)
    props.wardrobe_zoom=0.
    scene.frame_set(1)
    wardrobe_page.attach(scene,DATA,page,role,bg,extra)
    scene['zzz_wardrobe']=json.dumps(dict(role=role,clip=str(action),bones=bone_count,shape_curves=shapes,
                  frames=scene.frame_end,background=str(bg or 'Layered wardrobe page'),camera=limits),ensure_ascii=False)
    setup_view()


class ZZZ_OT_wardrobe_zoom(bpy.types.Operator):
    bl_idname='zzz_toon.wardrobe_wheel'
    bl_label='服装预览相机推拉'
    direction:IntProperty(default=1,options={'SKIP_SAVE'})

    @classmethod
    def poll(cls,context):
        space=context.space_data
        return (space and space.type=='VIEW_3D' and space.region_3d.view_perspective=='CAMERA'
                and context.scene.camera and KEY in context.scene.camera)

    def execute(self,context):
        props=context.scene.zzz_toon
        props.wardrobe_zoom=max(0.,min(1.,props.wardrobe_zoom+self.direction*.05))
        return {'FINISHED'}


def _finish_job(job):
    process,tmp,filename,scene=job
    request=Path(tmp.name)/'request.json'
    try:
        if process.returncode or not filename.is_file():
            error=request.with_suffix('.error')
            if error.is_file():
                raise ValueError(error.read_text(encoding='utf8'))
            raise ValueError((Path(tmp.name)/'worker.log').read_text(encoding='utf8',errors='replace')[-600:])
        if scene:
            scene.zzz_toon.wardrobe_result=str(filename)
            scene.zzz_toon.wardrobe_status=''
        if not bpy.app.background and scene:
            subprocess.Popen([bpy.app.binary_path,'--addons',__package__,str(filename)])
    finally:
        tmp.cleanup()


def _poll_jobs():
    for job in list(_jobs):
        if job[0].poll() is None:
            continue
        _jobs.remove(job)
        try:
            scene=job[3]
            if not hasattr(bpy.types.Scene,'zzz_toon') or scene not in list(bpy.data.scenes):
                job[3]=None
            _finish_job(job)
        except (OSError,ValueError,ReferenceError) as err:
            if job[3]:
                job[3].zzz_toon.wardrobe_status=str(err)
            print('ZZZ preview:',err)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':
                area.tag_redraw()
    return .5 if _jobs else None


class ZZZ_OT_wardrobe(bpy.types.Operator):
    bl_idname='zzz_toon.configure_wardrobe'
    bl_label='配置并打开服装预览'
    bl_description='使用所选角色的参数在独立工程中生成可播放的服装预览'

    @classmethod
    def description(cls, context, properties):
        from .wardrobe_language import text
        return text(context.scene.zzz_toon,cls.bl_description)


    def execute(self,context):
        props=context.scene.zzz_toon
        try:
            if any(job[3]==context.scene for job in _jobs):
                self.report({'INFO'},wardrobe_slots.wardrobe_language.text(props,'正在生成预览工程'))
                return {'CANCELLED'}
            request=locate(props)
            out=Path(bpy.path.abspath(props.wardrobe_output)).resolve() if props.wardrobe_output else Path(
                bpy.utils.user_resource('DATAFILES',path='ZZZ Outfit Previews',create=True))
            out.mkdir(parents=True,exist_ok=True)
            filename=out/(request['role']+'_'+uuid.uuid4().hex[:10]+'.blend')
            tmp=tempfile.TemporaryDirectory(prefix='zzz-outfit-')
            try:
                p=Path(tmp.name)/'request.json'
                p.write_text(json.dumps(dict(request=request,output=str(filename))),encoding='utf8')
                args=[bpy.app.binary_path,'-b','--factory-startup','--python-exit-code','1',
                      '-P',str(Path(__file__).with_name('wardrobe_worker.py')),'--',str(p)]
                with (Path(tmp.name)/'worker.log').open('wb') as log:
                    process=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,
                                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except Exception:
                tmp.cleanup()
                raise
            job=[process,tmp,filename,context.scene]
            if bpy.app.background:
                process.wait()
                _finish_job(job)
            else:
                _jobs.append(job)
                props.wardrobe_status='正在配置角色、待机与滚动背景…'
                if not bpy.app.timers.is_registered(_poll_jobs):
                    bpy.app.timers.register(_poll_jobs,first_interval=.5,persistent=True)
            self.report({'INFO'},str(filename))
        except (OSError,ValueError,RuntimeError) as err:
            self.report({'ERROR'},wardrobe_slots.wardrobe_language.text(props,str(err)))
            return {'CANCELLED'}
        return {'FINISHED'}


def register_keys():
    if _load_view not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_view)
    # Blender restricts scene data during Preferences > Enable Add-on.
    if not bpy.app.background and not bpy.app.timers.is_registered(_load_view):
        bpy.app.timers.register(_load_view, first_interval=0.1)
    kc=bpy.context.window_manager.keyconfigs.addon
    if kc:
        km=kc.keymaps.new(name='3D View',space_type='VIEW_3D')
        for wheel,step in [('WHEELUPMOUSE',1),('WHEELDOWNMOUSE',-1)]:
            item=km.keymap_items.new(ZZZ_OT_wardrobe_zoom.bl_idname,wheel,'PRESS',head=True)
            item.properties.direction=step
            KEYMAPS.append((km,item))


def unregister_keys():
    if bpy.app.timers.is_registered(_load_view):
        bpy.app.timers.unregister(_load_view)
    if bpy.app.timers.is_registered(_poll_jobs):
        bpy.app.timers.unregister(_poll_jobs)
    for process,tmp,filename,_scene in _jobs:
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            tmp.cleanup()
            # The worker publishes the final scene by renaming this staging file.
            filename.with_name('.'+filename.stem+'.blend').unlink(missing_ok=True)
    _jobs.clear()
    if _load_view in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_view)
    if bpy.app.timers.is_registered(_open_view):
        bpy.app.timers.unregister(_open_view)
    _pending.clear()
    if bpy.app.timers.is_registered(_receiver_refresh):
        bpy.app.timers.unregister(_receiver_refresh)
    for km,item in KEYMAPS:
        km.keymap_items.remove(item)
    KEYMAPS.clear()

