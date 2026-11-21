"""Native-size fullscreen character rendering with independently timed artwork."""
import math
from pathlib import Path
import time
import zipfile

import bpy
import gpu
import numpy as np

from .preview_frames import FrameUpload,ViewFrames
from .preview_loops import RecordedLoop
from .preview_page import ScrollingPage
from .preview_layers import CharacterLayers
from .preview_aa import CharacterAA
from . import native_sampling


class IdlePlayback:
    def __init__(self,owner):
        self.owner=owner
        self.scene=owner.scene
        render=self.scene.render
        self.fps=render.fps/render.fps_base
        if self.scene.use_preview_range:
            self.start,self.end=self.scene.frame_preview_start,self.scene.frame_preview_end
        else:
            self.start,self.end=self.scene.frame_start,self.scene.frame_end
        self.length=max(1,self.end-self.start+1)
        self.count=min(self.length,max(1,round(60*self.length/self.fps)))
        self.cacheable=2<=self.count<=240
        self.frames=[self.start+round(i*self.length/self.count) for i in range(self.count)] if self.cacheable else []
        self.clock=time.perf_counter()
        self.offset=(self.scene.frame_current-self.start)%self.length
        self.page=None
        self.layers=None
        self.aa=CharacterAA()
        self.source=None
        self.output=None
        self.linear_output=False
        self.upload=FrameUpload()
        self.cache=ViewFrames()
        self.recorded=None
        self.checked=set()
        self.rejected=set()
        self.reference_rgb_mae=None
        self.display_key=None
        self.current_texture=None
        self.rect=(0.,0.,1.,1.)
        self.dirty=True
        self.view_changed=self.clock
        self.last_displayed=None
        self.draw_handle=None
        self.busy=False
        self.failure=None
        self.last_size=None
        self.last_tick=-1
        self.sampling_group=bpy.data.node_groups.get(self.scene.get('zzz_sampling_uniform_group',''))
        self.sampling_projection=None

    def open(self):
        visible={w.screen for w in bpy.context.window_manager.windows}
        self.source=next((a.spaces.active for sc in bpy.data.screens if sc not in visible
                          for a in sc.areas if a.type=='VIEW_3D'),None)
        space=self.owner.viewport()[0].spaces.active
        self.source=self.source or space
        try:
            self.page=ScrollingPage(self.owner)
        except (AttributeError,IndexError,KeyError,ValueError,StopIteration):
            self.source=None
        if self.source:
            self.layers=CharacterLayers(self.owner)
            self.page.connect_cloth(self.layers.cloth)
            keep=self.owner.keep
            keep(self.source.shading,'type','RENDERED')
            keep(self.source.shading,'use_compositor','ALWAYS')
            keep(self.source.overlay,'show_overlays',False)
            keep(self.source,'show_object_viewport_mesh',True)
            keep(self.source.region_3d,'view_perspective','CAMERA')
            if self.source!=space:
                keep(space.shading,'type','SOLID')
                keep(space,'show_object_viewport_mesh',False)
        self.draw_handle=bpy.types.SpaceView3D.draw_handler_add(self.draw,(),'WINDOW','POST_PIXEL')

    def invalidate(self):
        # Keep the last complete frame and the live render target until the
        # replacement is drawn. A mouse event never presents an empty surface.
        self.cache.close()
        if self.recorded:
            self.recorded.close()
            self.recorded=None
        self.display_key=None
        self.dirty=True
        self.view_changed=time.perf_counter()

    def release_output(self):
        output,self.output=self.output,None
        if output:
            try:output.free()
            except (ReferenceError,RuntimeError):pass
        if self.page and self.page.bloom:
            self.page.bloom.release_targets()

    def close(self):
        try:
            if self.draw_handle:
                bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle,'WINDOW')
        finally:
            self.draw_handle=None
            try:
                self.invalidate()
            finally:
                self.current_texture=None
                self.upload.close()
                self.release_output()
                if self.aa:self.aa.close_target()
                if self.page:self.page.close()
                self.page=self.source=self.owner=None

    def seconds(self):
        return self.offset/self.fps+time.perf_counter()-self.clock

    def tick(self):
        area,_=self.owner.viewport()
        slot=int(self.seconds()*60)
        if self.last_tick==slot and not self.dirty:
            return
        self.last_tick=slot
        if self.source and not self.busy:
            with self.owner.override():
                self.prepare(bpy.context)
        elif not self.source:
            frame=self.start+int(self.seconds()*self.fps)%self.length
            if self.scene.frame_current!=frame:self.scene.frame_set(frame)
        area.tag_redraw()

    def choose_recording(self,w,h):
        if self.owner.secondary_turn.active:
            return None
        asset=self.owner.initial_loop
        zoom=self.scene.zzz_toon.wardrobe_zoom
        if not asset or not self.cacheable or zoom not in (0.,1.) or abs(self.owner.pan)>1e-6 or abs(math.remainder(self.owner.yaw,math.tau))>1e-6:
            return None
        path=Path(asset)
        if zoom==1.:path=path.with_name(path.stem+'_near.loop')
        key=(path,w,h)
        if key in self.rejected:return None
        if not self.recorded:
            try:
                archive=RecordedLoop(path,self.frames)
                if archive.canvas[0]<w or archive.canvas[1]<h:
                    archive.close()
                    self.rejected.add(key)
                    return None
                self.recorded=archive
            except (OSError,KeyError,ValueError,zipfile.BadZipFile):
                self.rejected.add(key)
                return None
        return key

    def render_camera(self,ctx,w,h,frame,interactive=False,supersample=False):
        target_size=(w,h)
        if supersample and self.aa:
            w,h=w*2,h*2
        if self.scene.frame_current!=frame or self.scene.frame_subframe:
            self.scene.frame_set(frame)
        fast=self.page.use_gpu_bloom(interactive)
        if self.output is None or (self.output.width,self.output.height)!=(w,h) or fast!=self.linear_output:
            self.release_output()
            self.output=gpu.types.GPUOffScreen(w,h,format='RGBA16F' if fast else 'RGBA8')
            self.linear_output=fast
        camera=self.owner.cam
        projection=camera.calc_matrix_camera(ctx.evaluated_depsgraph_get(),x=w,y=h,
            scale_x=self.scene.render.pixel_aspect_x,scale_y=self.scene.render.pixel_aspect_y)
        # The material's SampleBias footprint follows the source raster, including
        # supersampling. The regular scene handler is suspended during playback.
        if self.sampling_group:
            signature=(w,h,projection[0][0],projection[1][1])
            if signature!=self.sampling_projection:
                native_sampling.write_projection(self.sampling_group,w,h,projection)
                self.sampling_projection=signature
        cloth=self.layers.cloth if fast and self.page.before_cloth else None
        self.layers.update(ctx,self.source,camera.matrix_world.inverted(),projection,w,h,
                           moving=interactive and not supersample and self.owner.drag is not None,
                           gpu_cloth=cloth)
        self.output.draw_view3d(self.scene,self.owner.win.view_layer,self.source,ctx.region,
            camera.matrix_world.inverted(),projection,do_color_management=not fast,draw_background=False)
        if fast:
            try:
                color=self.page.bloom.apply(self.output.texture_color,cloth)
            except (ValueError,TypeError,RuntimeError):
                self.page.use_gpu_bloom(False)
                self.page.bloom.close()
                self.page.bloom=None
                return self.render_camera(ctx,*target_size,frame,supersample=supersample)
        else:
            color=self.output.texture_color
        return self.aa.resolve(color,*target_size) if self.aa else color

    def validate(self,ctx,key,w,h):
        archive=self.recorded
        live=np.asarray(self.render_camera(ctx,w,h,self.frames[0]).read()).reshape(h,w,4)
        image=archive.read(0)
        x,y,cw,ch=archive.crop
        # Compare inside the occupied character region even on letterboxed
        # displays. Use alpha as well as color to catch mismatched silhouettes.
        xs=np.linspace(0,cw-1,96).astype(int)
        ys=np.linspace(0,ch-1,64).astype(int)
        lx=np.minimum(w-1,((xs+x+.5)*w/archive.canvas[0]).astype(int))
        ly=np.minimum(h-1,((ys+y+.5)*h/archive.canvas[1]).astype(int))
        # Sample the recording at the centers of the same display pixels as
        # the live render. At 50% scale these lie between archive texels.
        u=np.clip((lx+.5)*archive.canvas[0]/w-x-.5,0,cw-1)
        v=np.clip((ly+.5)*archive.canvas[1]/h-y-.5,0,ch-1)
        x0,y0=u.astype(int),v.astype(int)
        x1,y1=np.minimum(x0+1,cw-1),np.minimum(y0+1,ch-1)
        fx,fy=(u-x0)[None,:,None],(v-y0)[:,None,None]
        row0=(1-fx)*image[y0[:,None],x0]+fx*image[y0[:,None],x1]
        row1=(1-fx)*image[y1[:,None],x0]+fx*image[y1[:,None],x1]
        target=(1-fy)*row0+fy*row1
        actual=live[ly[:,None],lx]
        occupied=(target[:,:,3]>15)|(actual[:,:,3]>15)
        self.reference_rgb_mae=float(np.abs(target.astype(float)-actual)[occupied].mean()) if occupied.any() else 255.
        if self.reference_rgb_mae<6.:
            self.checked.add(key)
            return True
        self.rejected.add(key)
        archive.close()
        self.recorded=None
        return False

    def present_pixels(self,key,rect,canvas,pixels):
        self.current_texture=self.upload.put(pixels)
        x,y,w,h=rect
        self.rect=(x/canvas[0],y/canvas[1],w/canvas[0],h/canvas[1])
        self.display_key=key

    def draw_camera(self,ctx):
        region=ctx.region
        sw,sh=region.width,region.height
        render=self.scene.render
        aspect=render.resolution_x*render.pixel_aspect_x/(render.resolution_y*render.pixel_aspect_y)
        w=max(1,min(sw,round(sh*aspect)))
        h=max(1,min(sh,round(w/aspect)))
        if self.last_size!=(w,h):
            self.invalidate()
            self.last_size=(w,h)
        key=self.choose_recording(w,h)
        if key and key not in self.checked and not self.validate(ctx,key,w,h):
            key=None
        sec=self.seconds()
        phase=(sec*self.fps)%self.length
        index=round(phase*self.count/self.length)%self.count
        frame=self.frames[index] if self.cacheable else self.start+int(phase)
        if key:
            if self.display_key!=(key,index):
                self.present_pixels((key,index),self.recorded.crop,self.recorded.canvas,self.recorded.read(index))
                if self.output and self.layers:
                    for layer in self.layers.inputs:layer.close()
                    if self.aa:self.aa.close_target()
                self.release_output()
        elif index in self.cache.entries:
            if self.display_key!=('cache',index):
                rect,pixels=self.cache.read(index)
                self.present_pixels(('cache',index),rect,(w,h),pixels)
        elif self.dirty or self.last_displayed!=frame or self.current_texture is None:
            texture=self.render_camera(ctx,w,h,frame,interactive=True)
            self.current_texture=texture
            self.rect=(0.,0.,1.,1.)
            self.display_key=('live',index)
            if self.cacheable and not self.cache.full and self.owner.drag is None and time.perf_counter()-self.view_changed>.75:
                pixels=np.asarray(texture.read()).reshape(h,w,4)
                self.cache.add(index,pixels)
        self.dirty=False
        self.last_displayed=frame
        self.page.compose(self.current_texture,self.rect,self.seconds(),w,h)

    def draw(self):
        ctx=bpy.context
        if self.owner is None or self.owner.closed or ctx.window!=self.owner.win or ctx.region!=self.owner.viewport()[1]:
            return
        if not self.source:
            self.last_displayed=self.scene.frame_current
            return
        # Window redraws can also be requested by the mouse or the OS. Only
        # present an already committed frame; never evaluate a view layer here.
        if self.page and self.page.display:
            blend,depth=gpu.state.blend_get(),gpu.state.depth_test_get()
            try:
                gpu.state.blend_set('NONE')
                gpu.state.depth_test_set('NONE')
                self.page.present(ctx.region)
            finally:
                gpu.state.depth_test_set(depth)
                gpu.state.blend_set(blend)

    def prepare(self,ctx):
        self.busy=True
        blend=gpu.state.blend_get()
        depth=gpu.state.depth_test_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            self.draw_camera(ctx)
        except (ValueError,TypeError,RuntimeError,MemoryError,BufferError,OSError,KeyError,zipfile.BadZipFile) as error:
            self.failure=str(error)
            # Restore the native camera compositor if the GPU backend cannot
            # run the page pass. The original editor will also be restored on Esc.
            self.page.restore_composite()
            if self.layers:self.layers.restore.close()
            self.current_texture=None
            self.invalidate()
            self.upload.close()
            self.release_output()
            self.source=None
            ctx.space_data.show_object_viewport_mesh=True
            ctx.space_data.shading.type='RENDERED'
            print('ZZZ fullscreen native viewport:',error,flush=True)
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.blend_set(blend)
            self.busy=False

