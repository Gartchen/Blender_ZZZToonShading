"""Draw the wardrobe artwork on its own clock and composite the premultiplied character."""
import json
import math

import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d

from . import wardrobe, preview, transparent_layers, preview_signature
from .preview_bloom import Bloom


def _shader(custom):
    info=gpu.types.GPUShaderCreateInfo()
    interface=gpu.types.GPUStageInterfaceInfo('zzz_preview_uv')
    interface.smooth('VEC2','uv')
    info.vertex_in(0,'VEC2','pos')
    info.vertex_out(interface)
    info.fragment_out(0,'VEC4','pixel')
    info.sampler(0,'FLOAT_2D','character')
    info.sampler(1,'FLOAT_2D','art')
    info.push_constant('VEC4','characterRect')
    if custom:
        info.push_constant('VEC4','mapping')
        background='return texture(art, fract((uv-0.5)*mapping.xy+0.5+mapping.zw)).rgb;'
    else:
        info.sampler(2,'FLOAT_2D','smallArt')
        info.sampler(3,'FLOAT_2D','shade')
        for name in ('top','middle','bottom'):
            info.push_constant('VEC4',name)
        info.push_constant('VEC3','phase')
        background='''
        vec2 p=(uv-0.5)*vec2(1920.,1080.);
        float x=dot(p,vec2(0.9659258263,0.2588190451));
        float y=dot(p,vec2(-0.2588190451,0.9659258263));
        float mask=rowMask(art,top,phase.x,x,y)+rowMask(smallArt,middle,phase.y,x,y)
                  +rowMask(art,bottom,phase.z,x,y);
        return texture(shade,uv).rgb*mask;'''
    info.vertex_source('void main(){uv=pos*0.5+0.5;gl_Position=vec4(pos,0.,1.);}')
    info.fragment_source('''
    float rowMask(sampler2D tex,vec4 row,float offset,float x,float y){
        vec2 q=vec2(mod(x-offset,row.x)/row.y,1.-(y-row.z)/row.w);
        if(any(lessThan(q,vec2(0.))) || any(greaterThan(q,vec2(1.))))return 0.;
        return texture(tex,q).r;
    }
    vec3 background(){'''+background+'''}
    vec3 linearRGB(vec3 x){return mix(x/12.92,pow((x+0.055)/1.055,vec3(2.4)),greaterThan(x,vec3(0.04045)));}
    vec3 displayRGB(vec3 x){return mix(12.92*x,1.055*pow(max(x,vec3(0.)),vec3(1./2.4))-.055,greaterThan(x,vec3(0.0031308)));}
    void main(){
        vec2 q=(uv-characterRect.xy)/characterRect.zw;
        vec4 fg=(all(greaterThanEqual(q,vec2(0.))) && all(lessThanEqual(q,vec2(1.))))?texture(character,q):vec4(0.);
        pixel=vec4(displayRGB(linearRGB(fg.rgb)+background()*(1.-fg.a)),1.);
    }''')
    return gpu.shader.create_from_info(info)


class ScrollingPage:
    def __init__(self,session):
        scene=session.scene
        settings=scene.view_settings
        if (settings.view_transform!='Standard' or settings.exposure!=0. or settings.gamma!=1.
                or scene.display_settings.display_device!='sRGB' or settings.look not in ('None','NONE')):
            raise ValueError('使用工程的显示颜色变换')
        tree=scene.compositing_node_group
        output=next(n for n in tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output).inputs[0]
        wrap=output.links[0].from_node
        if wrap.type!='GROUP' or wrap.label!='滚动背景与角色' or not wrap.inputs[0].is_linked:
            raise ValueError('找不到服装预览的背景合成节点')
        images={n.label:n.image for n in wrap.node_tree.nodes if n.type=='IMAGE' and n.image}
        self.custom=not {'large','small','screen_shade'}.issubset(images)
        if self.custom:
            if len(images)!=1:
                raise ValueError('无法识别自定义预览背景')
            image=next(iter(images.values()))
            self.images={'art':image}
            self.image_size=tuple(image.size)
        else:
            self.images={'art':images['large'],'smallArt':images['small'],'shade':images['screen_shade']}
        page=json.loads((wardrobe.DATA/'page.json').read_text(encoding='utf8'))
        self.rows=page['rows']
        self.motion=wardrobe.wardrobe_page.scroll_motion(page, scene.zzz_toon.wardrobe_role)
        self.shader=None
        self.batch=None
        self.display=None
        self.staging=None
        self.textures={}
        original=output.links[0].from_socket
        self.original_link=(tree,original,output)
        session.restore.callback(self.restore_composite)
        self.character=wrap.inputs[0].links[0].from_socket
        tree.links.new(self.character,output)
        self.before_bloom=None
        self.before_cloth=None
        self.bloom=None
        self.fast=False
        if self.character.node.label=='Native dynamic NAP Bloom / explicit scene profile':
            try:
                saturate=self.character.node.inputs['Alpha'].links[0].from_node
                add=saturate.inputs[0].links[0].from_node
                source=add.inputs[0].links[0].from_socket
                if source.node.type=='SEPARATE_COLOR' and source.name=='Alpha':
                    before=source.node.inputs[0].links[0].from_socket
                    if not session.initial_loop and not preview_signature.matches(scene,self.character,before):
                        return
                    profile=json.loads((preview.DATA/scene.zzz_toon.wardrobe_role/'preview.json').read_text(encoding='utf8'))['inputs']['native_bloom']
                    if profile.get('atlas') and profile.get('intermediate_storage')=='r11g11b10_truncate' and profile.get('output_alpha')=='native':
                        self.before_bloom=before
                        self.bloom=Bloom(profile)
            except (KeyError,IndexError,OSError,ValueError):
                pass

    def connect_cloth(self,layer):
        if not layer or not self.before_bloom:
            return
        # Match the generated premultiplied "over" graph, including its two
        # original inputs, before bypassing the CPU image in interactive mode.
        over=self.before_bloom.node
        if over.label!='Native transparent cloth after opaque LUT/rim':
            return
        try:
            add=over.inputs['Image'].links[0].from_node
            scaled=add.inputs[0].links[0].from_node
            original=scaled.inputs[0].links[0].from_socket
            if add.operation=='ADD' and scaled.operation=='SCALE' and layer.layer.name==transparent_layers.LAYER:
                self.before_cloth=original
        except (IndexError,AttributeError,KeyError):
            pass

    def use_gpu_bloom(self,enable):
        enable=bool(enable and self.bloom)
        if self.fast!=enable:
            tree,_,output=self.original_link
            tree.links.new((self.before_cloth or self.before_bloom) if enable else self.character,output)
            self.fast=enable
        return enable

    def restore_composite(self):
        if self.original_link:
            tree,original,output=self.original_link
            tree.links.new(original,output)
            self.original_link=None

    def draw(self,character,character_rect,seconds,box):
        if self.shader is None:
            self.shader=_shader(self.custom)
            self.batch=batch_for_shader(self.shader,'TRIS',{'pos':[(-1.,-1.),(3.,-1.),(-1.,3.)]})
            self.textures={name:gpu.texture.from_image(image) for name,image in self.images.items()}
        shader=self.shader
        shader.bind()
        shader.uniform_sampler('character',character)
        shader.uniform_float('characterRect',character_rect)
        for name,texture in self.textures.items():
            shader.uniform_sampler(name,texture)
        if self.custom:
            w,h=self.image_size
            fit=max(1920/w,1080/h)
            speed=self.motion['top']['logical_pixels_per_second']
            angle=math.pi/12
            shader.uniform_float('mapping',(1920/(w*fit),1080/(h*fit),
                (-speed*math.cos(angle)*seconds/(w*fit))%1,(-speed*math.sin(angle)*seconds/(h*fit))%1))
        else:
            phase=[]
            for name in ('top','middle','bottom'):
                row=self.rows[name]
                motion=self.motion[name]
                shader.uniform_float(name,(row['period'],row['width'],row['low'],row['height']))
                phase.append((motion['phase_at_zero']+seconds*motion['logical_pixels_per_second'])%row['period'])
            shader.uniform_float('phase',phase)
        viewport=gpu.state.viewport_get()
        try:
            gpu.state.viewport_set(*box)
            self.batch.draw(shader)
        finally:
            gpu.state.viewport_set(*viewport)

    def close(self):
        self.display=self.staging=None
        if self.bloom:self.bloom.close()
        self.textures.clear()
        self.shader=self.batch=None
        self.images.clear()

    def compose(self,character,rect,seconds,w,h):
        # Keep the previous page presentable throughout all three scene draws,
        # including resize and recording/live transitions.
        if not self.staging or (self.staging[0].width,self.staging[0].height)!=(w,h):
            texture=gpu.types.GPUTexture((w,h),format='RGBA8')
            self.staging=(texture,gpu.types.GPUFrameBuffer(color_slots=texture))
        with self.staging[1].bind():
            self.draw(character,rect,seconds,(0,0,w,h))
        self.display,self.staging=self.staging,self.display

    def present(self,region):
        if self.display:
            texture=self.display[0]
            w,h=texture.width,texture.height
            scale=min(region.width/w,region.height/h)
            w,h=round(w*scale),round(h*scale)
            draw_texture_2d(texture,((region.width-w)//2,(region.height-h)//2),w,h)

