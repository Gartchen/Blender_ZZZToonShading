"""Pass the current EEVEE eye layer to the original compositor via hair AOVs."""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader


class EyeTransfer:
    def __init__(self,tree,main,render_layers,source,materials,restore):
        self.source=source
        self.layer=main
        self.image=bpy.data.images.new('ZZZ Preview Eye RGBA',width=2,height=2,
                                       alpha=True,float_buffer=True,is_data=True)
        restore.callback(bpy.data.images.remove,self.image)
        self.image.colorspace_settings.name='Non-Color'
        self.image.alpha_mode='CHANNEL_PACKED'
        self.texture=self.framebuffer=self.shader=self.batch=None

        rgb='zzz_preview_feature_rgb'
        alpha='zzz_preview_feature_alpha'
        for name,kind in ((rgb,'COLOR'),(alpha,'VALUE')):
            if name in main.aovs:
                raise ValueError('Eye transfer pass already exists')
            aov=main.aovs.add()
            restore.callback(main.aovs.remove,aov)
            aov.name=name
            aov.type=kind
        main.update_render_passes()
        main.update()

        def node(group,kind):
            result=group.nodes.new(kind)
            restore.callback(group.nodes.remove,result)
            return result

        for mat in materials:
            group=mat.node_tree
            coords=node(group,'ShaderNodeTexCoord')
            sampler=node(group,'ShaderNodeTexImage')
            sampler.image=self.image
            sampler.interpolation='Closest'
            sampler.extension='CLIP'
            group.links.new(coords.outputs['Window'],sampler.inputs['Vector'])
            for name,socket,dst in ((rgb,'Color','Color'),(alpha,'Alpha','Value')):
                output=node(group,'ShaderNodeOutputAOV')
                output.aov_name=name
                group.links.new(sampler.outputs[socket],output.inputs[dst])

        # The feature term is multiplied by the hair pass in the existing
        # graph. Only materials that actually write transmittance need to read it.
        for original,replacement in ((source.mapped.outputs[0],render_layers.outputs[rgb]),
                                      (source.channels.outputs['Alpha'],render_layers.outputs[alpha])):
            for link in list(original.links):
                if link.to_node!=source.channels:
                    destination=link.to_socket
                    restore.callback(tree.links.new,original,destination)
                    tree.links.new(replacement,destination)
        restore.callback(self.release)

    def release(self):
        self.framebuffer=self.texture=None

    def publish(self):
        rect,w,h=self.source.rectangle
        if tuple(self.image.size)!=(w,h):
            self.release()
            self.image.scale(w,h)
            self.image.update()
            self.image.update_tag()
            self.layer.update()
        if self.texture is None:
            self.texture=gpu.texture.from_image(self.image)
            self.framebuffer=gpu.types.GPUFrameBuffer(color_slots=self.texture)
        if self.shader is None:
            info=gpu.types.GPUShaderCreateInfo()
            interface=gpu.types.GPUStageInterfaceInfo('zzz_eye_uv')
            interface.smooth('VEC2','uv')
            info.vertex_in(0,'VEC2','pos')
            info.vertex_out(interface)
            info.fragment_out(0,'VEC4','pixel')
            info.sampler(0,'FLOAT_2D','src')
            info.vertex_source('void main(){uv=pos*0.5+0.5;gl_Position=vec4(pos,0.,1.);}')
            info.fragment_source('void main(){pixel=textureLod(src,uv,0.);}')
            self.shader=gpu.shader.create_from_info(info)
            self.batch=batch_for_shader(self.shader,'TRIS',{'pos':[(-1.,-1.),(3.,-1.),(-1.,3.)]})
        viewport=gpu.state.viewport_get()
        try:
            with self.framebuffer.bind():
                self.framebuffer.clear(color=(0.,0.,0.,0.))
                gpu.state.viewport_set(*rect)
                self.shader.bind()
                self.shader.uniform_sampler('src',self.source.target.texture_color)
                self.batch.draw(self.shader)
        finally:
            gpu.state.viewport_set(*viewport)
