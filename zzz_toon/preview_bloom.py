"""GPU execution of the certified NAP compositor's four taps, packed atlas and final resolve."""
import gpu
from gpu_extras.batch import batch_for_shader

from .native_bloom import KERNELS


def _program(body,samplers=('src',),uniforms=()):
    info=gpu.types.GPUShaderCreateInfo()
    uv=gpu.types.GPUStageInterfaceInfo('zzz_bloom_uv')
    uv.smooth('VEC2','uv')
    info.vertex_in(0,'VEC2','pos')
    info.vertex_out(uv)
    info.fragment_out(0,'VEC4','pixel')
    for i,name in enumerate(samplers):info.sampler(i,'FLOAT_2D',name)
    for kind,name in uniforms:info.push_constant(kind,name)
    info.vertex_source('void main(){uv=pos*0.5+0.5;gl_Position=vec4(pos,0.,1.);}')
    info.fragment_source('''
    vec3 stored(vec3 v){
        v=clamp(v,vec3(0.),vec3(65024.,65024.,64512.));
        vec3 scale=exp2(vec3(6.,6.,5.)-floor(log2(max(v,vec3(0.00006103515625)))));
        return floor(v*scale)/scale;
    }
    vec3 toDisplay(vec3 x){return mix(12.92*x,1.055*pow(max(x,vec3(0.)),vec3(1./2.4))-.055,greaterThan(x,vec3(.0031308)));}
    void main(){'''+body+'}')
    shader=gpu.shader.create_from_info(info)
    batch=batch_for_shader(shader,'TRIS',{'pos':[(-1.,-1.),(3.,-1.),(-1.,3.)]})
    return shader,batch


class Bloom:
    def __init__(self,profile):
        self.p=profile
        self.jobs={}
        self.targets={}
        self.dims=None

    def setup(self,w,h):
        if self.dims==(w,h):return
        self.release_targets()
        self.dims=(w,h)
        p=self.p
        def sized(size):return max(2,round(size[0]*w/h*p['reference_resolution'][1]/p['reference_resolution'][0])),size[1]
        self.base=sized(p['base_size'])
        self.atlas=sized(p['atlas']['size'])
        self.compose=sized(p['compose_size'])
        self.tiles=[(0,row,*sized(layer['size'])) for layer,row in zip(p['layers'],p['atlas']['rows_from_v0'])]
        for name,dims in [('bright',self.base),('horizontal',self.base),('base',self.base),
                          ('down',self.atlas),('hblur',self.atlas),('vblur',self.atlas),('compose',self.compose),('final',(w,h))]:
            tex=gpu.types.GPUTexture(dims,format='RGBA8' if name=='final' else 'RGBA16F')
            tex.filter_mode(True)
            tex.extend_mode('EXTEND')
            fbo=gpu.types.GPUFrameBuffer(color_slots=tex)
            with fbo.bind():fbo.clear(color=(0,0,0,0))
            self.targets[name]=(tex,fbo)
        if self.jobs:
            return
        self.jobs['four']=_program('''
            vec3 q=(texture(src,uv+delta).rgb+texture(src,uv-delta).rgb+
              texture(src,uv+vec2(delta.x,-delta.y)).rgb+texture(src,uv+vec2(-delta.x,delta.y)).rgb)*0.25;
            pixel=vec4(stored(max(q-vec3(threshold),vec3(0.))*gain),1.);''',
            uniforms=(('VEC2','delta'),('FLOAT','threshold'),('FLOAT','gain')))
        for k in {6,*(l['kernel'] for l in p['layers'])}:
            taps='\n'.join(f'acc+=texture(src,clamp(at+axis*({x:.10f}),rect.xy,rect.xy+rect.zw)).rgb*{wt:.12f};'
                           for x,wt in KERNELS[str(k)])
            self.jobs[k]=_program('vec2 at=rect.xy+uv*rect.zw;vec3 acc=vec3(0.);'+taps+'pixel=vec4(stored(acc),1.);',
                                  uniforms=(('VEC4','rect'),('VEC2','axis')))
        self.jobs['compose']=_program('''vec3 b=texture(src,uv).rgb*weights.x+
          texture(atlas,uv*r0.zw+r0.xy).rgb*weights.y+texture(atlas,uv*r1.zw+r1.xy).rgb*weights.z+
          texture(atlas,uv*r2.zw+r2.xy).rgb*weights.w;pixel=vec4(stored(b),1.);''',
          samplers=('src','atlas'),uniforms=tuple(('VEC4',n) for n in ('weights','r0','r1','r2')))
        self.jobs['final']=_program('''
           vec4 s=texture(src,uv);
           vec3 b=texture(glow,uv).rgb;
           vec3 add=mix(b,1.49380159*pow(abs(b),vec3(.333333343))-.699999988,greaterThanEqual(b,vec3(.300000012)))*amount;
           pixel=vec4(toDisplay(clamp(s.rgb+add,0.,1.)),clamp(s.a+.333299994*(add.r+add.g+add.b),0.,1.));''',
          samplers=('src','glow'),uniforms=(('FLOAT','amount'),))

    def run(self,name,target,src,box=None,**properties):
        tex,fbo=self.targets[target]
        shader,batch=self.jobs[name]
        with fbo.bind():
            gpu.state.viewport_set(*(box or (0,0,tex.width,tex.height)))
            shader.bind()
            shader.uniform_sampler('src',src)
            for key,value in properties.items():
                if isinstance(value,gpu.types.GPUTexture):shader.uniform_sampler(key,value)
                else:shader.uniform_float(key,value)
            batch.draw(shader)
        return tex

    def apply(self,source,cloth=None):
        vp=gpu.state.viewport_get()
        blend=gpu.state.blend_get()
        depth=gpu.state.depth_test_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            self.setup(source.width,source.height)
            if cloth is not None:
                if 'cloth' not in self.targets:
                    texture=gpu.types.GPUTexture((source.width,source.height),format='RGBA16F')
                    self.targets['cloth']=(texture,gpu.types.GPUFrameBuffer(color_slots=texture))
                if 'cloth' not in self.jobs:
                    self.jobs['cloth']=_program('''
                        vec2 q=(uv-rect.xy)/rect.zw;
                        vec4 c=vec4(0.);
                        if(all(greaterThanEqual(q,vec2(0.))) && all(lessThan(q,vec2(1.)))){
                            c=textureLod(layerImage,q,0.);
                        }
                        pixel=texture(src,uv)*(1.-c.a)+c;''',
                        samplers=('src','layerImage'),uniforms=(('VEC4','rect'),))
                (x,y,w,h),cw,ch=cloth.rectangle
                layer=cloth.target.texture_color
                layer.filter_mode(True)
                layer.extend_mode('CLAMP_TO_BORDER')
                source=self.run('cloth','cloth',source,layerImage=layer,rect=(x/cw,y/ch,w/cw,h/ch))
            p=self.p
            full=(0.,0.,1.,1.)
            rw=max(2,round(source.width/source.height*p['reference_resolution'][1]))
            q=self.run('four','bright',source,delta=(1./rw,1./p['reference_resolution'][1]),threshold=p['threshold'],gain=p['prefilter_gain'])
            q=self.run(6,'horizontal',q,rect=full,axis=(1/self.base[0],0))
            base=self.run(6,'base',q,rect=full,axis=(0,1/self.base[1]))
            aw,ah=self.atlas
            rects=[(x/aw,y/ah,w/aw,h/ah) for x,y,w,h in self.tiles]
            for box in self.tiles:
                self.run('four','down',base,box,delta=(.959999979/self.base[0],.25/self.base[1]),threshold=0.,gain=1.)
            for dest,src,axis in [('hblur','down',(1/aw,0)),('vblur','hblur',(0,1/ah))]:
                for box,rect,layer in zip(self.tiles,rects,p['layers']):
                    self.run(layer['kernel'],dest,self.targets[src][0],box,rect=rect,axis=axis)
            glow=self.run('compose','compose',base,atlas=self.targets['vblur'][0],weights=p['weights'],
                          r0=rects[0],r1=rects[1],r2=rects[2])
            return self.run('final','final',source,glow=glow,amount=p['intensity'])
        finally:
            gpu.state.viewport_set(*vp)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)

    def release_targets(self):
        self.targets.clear()
        self.dims=None

    def close(self):
        self.release_targets()
        self.jobs.clear()


