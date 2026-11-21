"""Interactive wardrobe preview. The session owns temporary viewport state until Esc."""
import ctypes
import json
import math
import os
import sys
from contextlib import ExitStack

import bpy
from mathutils import Matrix, Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d

from . import core, wardrobe, stencil_runtime, preview_loops, preview_uniforms, preview_hull
from .idle_cache import IdlePlayback

# Pan stops observed at the extremes of a vertical drag.
CROWN_FROM_TOP = .497
SOLE_FROM_TOP = .40
_session = None


def active_for(scene):
    return _session if _session and _session.scene == scene else None


def _check_window():
    session=_session
    if session is None:
        return None
    if not session.window_exists():
        session.close(from_watchdog=True)
        return None
    return .5


def _restore_property(obj,key,value):
    try:
        setattr(obj,key,value)
    except ReferenceError:
        # Closing a window can also remove its space and region data.
        pass


def _native_fullscreen(win):
    """Identify the current Win32 window state before toggling it."""
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes as wt
    user = ctypes.windll.user32
    user.GetActiveWindow.restype = wt.HWND
    # A new Blender window or one driven by an input device need not own
    # Win32 keyboard focus. Match this process's native GHOST client first.
    active=user.GetActiveWindow()
    candidates=[]
    @ctypes.WINFUNCTYPE(wt.BOOL,wt.HWND,wt.LPARAM)
    def collect(handle,_):
        pid=wt.DWORD()
        user.GetWindowThreadProcessId(wt.HWND(handle),ctypes.byref(pid))
        if pid.value==os.getpid():
            name=ctypes.create_unicode_buffer(128)
            user.GetClassNameW(wt.HWND(handle),name,128)
            client=wt.RECT()
            if name.value=='GHOST_WindowClass' and user.GetClientRect(wt.HWND(handle),ctypes.byref(client)):
                score=abs(client.right-win.width)+abs(client.bottom-win.height)
                candidates.append((score,handle!=active,handle))
        return True
    user.EnumWindows(collect,0)
    hwnd=min(candidates)[2] if candidates else active
    if not hwnd:
        return False
    user.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    user.MonitorFromWindow.restype = wt.HANDLE
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('rect', wt.RECT), ('work', wt.RECT), ('flags', wt.DWORD)]
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    # Blender's borderless Win32 window can have an invisible 8 px DWM
    # border beyond the monitor. Its client area covers the monitor exactly.
    rect=wt.RECT()
    if not user.GetClientRect(wt.HWND(hwnd),ctypes.byref(rect)):
        return None
    origin=wt.POINT(0,0)
    if not user.ClientToScreen(wt.HWND(hwnd),ctypes.byref(origin)):
        return None
    rect=wt.RECT(origin.x,origin.y,origin.x+rect.right,origin.y+rect.bottom)
    monitor = user.MonitorFromWindow(hwnd, 2)
    if not user.GetMonitorInfoW(wt.HANDLE(monitor), ctypes.byref(info)):
        return None
    return all(abs(getattr(rect,k)-getattr(info.rect,k))<=1 for k in ('left','top','right','bottom'))


def _gpu_receiver(material, output):
    # Same camera-linked face-only spot and HairShadow blocker used by the
    # existing SDF face path. Its radiance encodes visibility after removing
    # the inverse square distance and the cosine term.
    b=core.NB(material)
    geo=b.node('ShaderNodeNewGeometry')
    dot=b.node('ShaderNodeVectorMath',operation='DOT_PRODUCT')
    b.link(geo.outputs['Normal'],dot.inputs[0])
    b.link(geo.outputs['Incoming'],dot.inputs[1])
    cosine=dot.outputs['Value']
    diffuse=b.node('ShaderNodeBsdfDiffuse')
    diffuse.inputs['Color'].default_value=(1,1,1,1)
    converter=b.node('ShaderNodeShaderToRGB')
    b.link(diffuse.outputs[0],converter.inputs[0])
    distance=b.node('ShaderNodeCameraData').outputs['View Distance']
    power=b.math('MULTIPLY', converter.outputs['Color'],b.math('MULTIPLY',distance,distance))
    divisor=b.math('MULTIPLY',b.math('MAXIMUM',cosine,.02),core.HS_POWER/(4*math.pi**2))
    visible=b.smooth(b.math('DIVIDE',power,divisor),core.P['hair_shadow_lo'],core.P['hair_shadow_hi'])
    shadow=b.math('MULTIPLY',b.math('SUBTRACT',1.,visible),b.smooth(cosine,.03,.12))
    for link in list(output.links):
        b.link(shadow,link.to_socket)


class PreviewSession:
    def __init__(self, context):
        self.win=context.window
        self.window_id=self.win.as_pointer()
        self.scene=context.scene
        self.area=context.area
        self.restore=ExitStack()
        self.closed=False
        self.drag=None
        self.yaw=0.
        self.pan=0.
        self.turn_dirty=False
        self.height_dirty=False
        self.fit_size=None
        self.uniforms=set()
        scene=self.scene
        self.cam=scene.camera
        self.initial_loop=preview_loops.verified_initial(scene)
        self.rig=next(o for o in scene.objects if o.type=='ARMATURE')
        self.base_rig=self.rig.matrix_world.copy()
        self.limits=wardrobe.camera_stops(self.cam,scene.zzz_toon.wardrobe_role)
        lo,hi=map(Vector,self.limits['bounds'])
        head=self.rig.pose.bones.get('Bip001 Head')
        center=self.rig.matrix_world@head.head if head else (lo+hi)/2
        self.axis=Vector((center.x,center.y,0.))
        self.bottom=lo.z
        self.top=hi.z
        if scene.zzz_toon.wardrobe_role=='Vivian':
            # The parasol is included at the full-body stop. For height
            # navigation we follow the character's crown under the canopy.
            dg=context.evaluated_depsgraph_get()
            faces=[o.evaluated_get(dg) for o in scene.objects if o.type=='MESH' and not o.hide_render
                   and o.visible_get(view_layer=context.view_layer) and o.name.endswith(('Body_1','Body_2','_Face'))]
            z=max(((o.matrix_world@Vector(p)).z for o in faces for p in o.bound_box),default=self.top)
            self.top=min(self.top,z)
        from .wardrobe_turn import TurnResponse
        self.secondary_turn=TurnResponse(self)
        self.zoom0=scene.zzz_toon.wardrobe_zoom

    def keep(self,obj,key,value):
        old=getattr(obj,key)
        if hasattr(old,'copy') and not isinstance(old,bpy.types.ID):
            old=old.copy()
        self.restore.callback(_restore_property,obj,key,old)
        setattr(obj,key,value)

    def window_exists(self):
        return any(w.as_pointer()==self.window_id for w in bpy.context.window_manager.windows)

    def viewport(self):
        area=next(a for a in self.win.screen.areas if a.type=='VIEW_3D' and (self.win.screen.show_fullscreen or a==self.area))
        return area,next(r for r in area.regions if r.type=='WINDOW')

    def override(self):
        area,region=self.viewport()
        return bpy.context.temp_override(window=self.win,area=area,region=region)

    def fit(self):
        area,region=self.viewport()
        size=(region.width,region.height)
        if self.fit_size==size or min(size)<10:
            return
        rv=area.spaces.active.region_3d
        corners=[location_3d_to_region_2d(region,rv,self.cam.matrix_world@v) for v in self.cam.data.view_frame(scene=self.scene)]
        if any(p is None for p in corners):
            return
        width=max(p.x for p in corners)-min(p.x for p in corners)
        height=max(p.y for p in corners)-min(p.y for p in corners)
        if min(width,height)<1:
            return
        factor=min(region.width/width,region.height/height)
        rv.view_camera_zoom=max(-30,min(600,(math.sqrt(factor)*(math.sqrt(2)+rv.view_camera_zoom/50)-math.sqrt(2))*50))
        self.fit_size=size

    def camera(self):
        t=self.scene.zzz_toon.wardrobe_zoom
        far,near=self.limits['far'],self.limits['near']
        m=Matrix(far['world']).lerp(Matrix(near['world']),t)
        d=self.cam.data
        d.lens=far['lens']+t*(near['lens']-far['lens'])
        d.shift_y=far['shift_y']+t*(near['shift_y']-far['shift_y'])
        k=d.lens/d.sensor_width
        r=self.scene.render
        aspect=r.resolution_x*r.pixel_aspect_x/(r.resolution_y*r.pixel_aspect_y)
        inv=m.inverted()
        # For a point at a given height solve the camera's vertical translation
        # at which it reaches the recorded normalized ordinate.
        up=inv.to_3x3()@Vector((0,0,1))
        def height_offset(height,line):
            pt=inv@Vector((self.axis.x,self.axis.y,height))
            slope=((.5-line)/aspect+d.shift_y)/k
            return (pt.y+slope*pt.z)/(up.y+slope*up.z)
        # The close shoe view (12.6 s) ends lower than the far view (9.1 s).
        # Intermediate zooms use an interpolation of these observed bounds.
        self.floor_shift=height_offset(self.bottom,SOLE_FROM_TOP+(.82-SOLE_FROM_TOP)*t)
        self.crown_shift=height_offset(self.top,.464+(CROWN_FROM_TOP-.464)*t)
        shift=abs(self.pan)*(self.crown_shift if self.pan>0 else self.floor_shift)
        m.translation=m.translation+Vector((0.,0.,shift))
        # Keep the turntable axis in the middle of the UI-free camera frame.
        focus_z=(self.top+self.bottom)/2*(1-t)+(self.top-.12)*t
        pt=m.inverted()@Vector((self.axis.x,self.axis.y,focus_z))
        self.vertical_span=max(.01,-pt.z)/(aspect*k)
        d.shift_x=k*pt.x/max(.01,-pt.z)
        self.cam.matrix_world=m

    def rotate(self):
        pivot=Matrix.Translation(self.axis)
        self.rig.matrix_world=pivot@Matrix.Rotation(self.yaw,4,'Z')@pivot.inverted()@self.base_rig

    def performance(self):
        s=self.scene
        self.keep(s.eevee,'taa_samples',1)
        self.keep(s.eevee,'use_taa_reprojection',False)
        self.keep(s.eevee,'use_shadow_jitter_viewport',False)
        self.keep(s.render,'dither_intensity',0.)
        self.keep(s.render,'preview_pixel_size','1')
        # The wardrobe LUT encodes a 3D lookup in a 2D strip. Half-precision
        # texture coordinates shift samples across adjacent colors on Vivian, too.
        self.keep(s.render,'compositor_precision','FULL')
        for ob in s.objects:
            mod=ob.modifiers.get('ZZZ_SkinSmooth')
            if mod and mod.show_viewport:
                self.keep(mod,'show_viewport',False)
        image=json.loads(s[stencil_runtime.KEY])['image'] if stencil_runtime.KEY in s else None
        prepared={}
        props={}
        for ob in s.objects:
            if ob.type!='MESH' or ob.hide_render:
                continue
            for slot in ob.material_slots:
                mat=slot.material
                if not mat or not mat.node_tree:
                    continue
                if mat not in prepared:
                    masks=[n.name for n in mat.node_tree.nodes if image and getattr(getattr(n,'image',None),'name','')==image and n.outputs[0].is_linked]
                    vectors=preview_uniforms.head_vectors(mat.node_tree)
                    temp=None
                    if vectors or masks:
                        temp=mat.copy()
                        self.restore.callback(bpy.data.materials.remove,temp)
                        for name in masks:
                            _gpu_receiver(temp,temp.node_tree.nodes[name].outputs[0])
                    prepared[mat]=(temp,vectors)
                    if temp:
                        prepared[temp]=(temp,vectors)
                temp,vectors=prepared[mat]
                if temp:
                    preview_uniforms.bind(self,ob,temp,vectors,props)
                    if slot.material!=temp:
                        self.keep(slot,'material',temp)

    def enter(self):
        s=self.scene
        self.had_play=self.win.screen.is_animation_playing
        self.start_frame=s.frame_current
        self.start_subframe=s.frame_subframe
        self.was_full=self.win.screen.show_fullscreen
        self.toggled_area=False
        self.toggled_window=False
        self.playback=None
        if self.had_play:
            with self.override():
                bpy.ops.screen.animation_cancel(restore_frame=False)
        self.restore.callback(setattr,self.rig,'matrix_world',self.base_rig)
        self.restore.callback(setattr,self.cam,'matrix_world',self.cam.matrix_world.copy())
        for k in ('shift_x','shift_y','lens'):
            self.restore.callback(setattr,self.cam.data,k,getattr(self.cam.data,k))
        a=self.area.spaces.active
        self.keep(a.region_3d,'view_perspective','CAMERA')
        self.keep(a.region_3d,'view_camera_zoom',a.region_3d.view_camera_zoom)
        self.keep(a.region_3d,'view_camera_offset',(0.,0.))
        self.keep(a.overlay,'show_overlays',False)
        self.keep(a.shading,'type','RENDERED')
        self.keep(a.shading,'use_compositor','CAMERA')
        self.keep(s,'sync_mode','FRAME_DROP')
        from . import wardrobe_face
        wardrobe_face.apply(s,s.zzz_toon.wardrobe_role,self)
        self.performance()
        preview_hull.separate(self)
        self.camera()
        if _native_fullscreen(self.win) is False:
            if bpy.ops.wm.window_fullscreen_toggle()=={'FINISHED'}:
                self.toggled_window=True
        if not self.was_full:
            with self.override():
                bpy.ops.screen.screen_full_area(use_hide_panels=True)
            self.toggled_area=True
        self.playback=IdlePlayback(self)
        self.playback.open()
        self.timer=bpy.context.window_manager.event_timer_add(1/60,window=self.win)
        if not bpy.app.timers.is_registered(_check_window):
            bpy.app.timers.register(_check_window,first_interval=.5)

    def restore_layout(self):
        if self.win.screen.is_animation_playing:
            with self.override():
                bpy.ops.screen.animation_cancel(restore_frame=False)
        if self.toggled_area and self.win.screen.show_fullscreen:
            with self.override():
                bpy.ops.screen.screen_full_area(use_hide_panels=True)
        if self.toggled_window:
            with self.override():
                bpy.ops.wm.window_fullscreen_toggle()

    def close(self,from_watchdog=False):
        global _session
        if self.closed:
            return
        self.closed=True
        has_window=self.window_exists()
        try:
            with ExitStack() as cleanup:
                cleanup.callback(self.restore.close)
                cleanup.callback(_restore_property,self.scene.zzz_toon,'wardrobe_zoom',self.zoom0)
                if has_window:
                    cleanup.callback(self.restore_layout)
                if self.playback:
                    cleanup.callback(self.playback.close)
                if getattr(self,'timer',None) and has_window:
                    bpy.context.window_manager.event_timer_remove(self.timer)
        finally:
            self.timer=None
            self.playback=None
            _session=None
            if not from_watchdog and bpy.app.timers.is_registered(_check_window):
                bpy.app.timers.unregister(_check_window)
        self.scene.frame_set(self.start_frame,subframe=self.start_subframe)
        if has_window and self.had_play and not self.win.screen.is_animation_playing:
            with self.override():
                bpy.ops.screen.animation_play()


class ZZZ_OT_fullscreen(bpy.types.Operator):
    bl_idname='zzz_toon.fullscreen_preview'
    bl_label='进入全屏预览'
    bl_description='播放待机。左键拖动转身或调高度，滚轮推拉，Esc 返回'

    @classmethod
    def description(cls, context, properties):
        from .wardrobe_language import text
        return text(context.scene.zzz_toon,cls.bl_description)

    bl_options={'BLOCKING'}

    @classmethod
    def poll(cls,context):
        s=context.scene
        return bool(context.area and context.area.type=='VIEW_3D' and s and s.get('zzz_wardrobe') and s.camera and wardrobe.KEY in s.camera and _session is None)

    def invoke(self,context,event):
        global _session
        session=PreviewSession(context)
        _session=self.session=session
        try:
            session.enter()
            context.window_manager.modal_handler_add(self)
        except Exception as error:
            session.close()
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self,context,event):
        s=self.session
        if s.closed:
            return {'FINISHED'}
        try:
            if event.type=='ESC':
                s.close()
                return {'FINISHED'}
            if event.type=='TIMER':
                s.fit()
                if s.turn_dirty:
                    s.rotate()
                if s.height_dirty:
                    s.camera()
                settling=s.secondary_turn.tick()
                if s.turn_dirty or s.height_dirty or settling:
                    s.playback.invalidate()
                    s.turn_dirty=s.height_dirty=False
                s.playback.tick()
            elif event.type=='LEFTMOUSE':
                s.drag=(event.mouse_x,event.mouse_y) if event.value=='PRESS' else None
            elif event.type=='WINDOW_DEACTIVATE':
                s.drag=None
            elif event.type=='MOUSEMOVE' and s.drag:
                _,region=s.viewport()
                x,y=event.mouse_x,event.mouse_y
                dx,dy=x-s.drag[0],y-s.drag[1]
                s.drag=(x,y)
                if not (dx or dy):
                    return {'RUNNING_MODAL'}
                s.yaw=(s.yaw+math.tau*dx/max(1,region.width))%math.tau
                current=abs(s.pan)*(s.crown_shift if s.pan>0 else s.floor_shift)
                offset=current-s.vertical_span*dy/max(1,region.height)
                pan=max(-1.,min(1.,offset/max(.0001,s.crown_shift if offset>=0 else -s.floor_shift)))
                s.turn_dirty|=bool(dx)
                s.height_dirty|=bool(dy and abs(pan-s.pan)>1e-7)
                s.pan=pan
            elif event.type in ('WHEELUPMOUSE','WHEELDOWNMOUSE') and event.value=='PRESS':
                props=s.scene.zzz_toon
                delta=.05 if event.type=='WHEELUPMOUSE' else -.05
                props.wardrobe_zoom=max(0.,min(1.,props.wardrobe_zoom+delta))
            if event.type.startswith('TIMER'):
                return {'PASS_THROUGH'}
        except Exception as error:
            s.close()
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self,context):
        self.session.close()


@bpy.app.handlers.persistent
def stop(*args):
    if _session:
        _session.close()


def register():
    for handlers in (bpy.app.handlers.load_pre,bpy.app.handlers.save_pre,bpy.app.handlers.render_init):
        if stop not in handlers:
            handlers.append(stop)


def unregister():
    stop()
    for handlers in (bpy.app.handlers.load_pre,bpy.app.handlers.save_pre,bpy.app.handlers.render_init):
        if stop in handlers:
            handlers.remove(stop)
