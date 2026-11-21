"""Spatial antialiasing in the current pose and view. No previous-frame history."""
import gpu
from gpu_extras.batch import batch_for_shader


class CharacterAA:
    def __init__(self):
        self.output=None
        self.shader=None

    def resolve(self,texture,w,h):
        if self.shader is None:
            info=gpu.types.GPUShaderCreateInfo()
            vary=gpu.types.GPUStageInterfaceInfo('zzz_aa_uv')
            vary.smooth('VEC2','uv')
            info.vertex_in(0,'VEC2','position')
            info.vertex_out(vary)
            info.fragment_out(0,'VEC4','pixel')
            info.sampler(0,'FLOAT_2D','source')
            info.push_constant('VEC2','stepUV')
            info.push_constant('FLOAT','supersampled')
            info.vertex_source('void main(){uv=.5*position+.5;gl_Position=vec4(position,0,1);}')
            info.fragment_source("""
            vec3 decode(vec3 c){return mix(c/12.92,pow((c+.055)/1.055,vec3(2.4)),greaterThan(c,vec3(.04045)));}
            vec4 encode(vec4 c){return vec4(mix(12.92*c.rgb,1.055*pow(max(c.rgb,vec3(0)),vec3(1./2.4))-.055,
                                                   greaterThan(c.rgb,vec3(.0031308))),c.a);}
            vec4 linearTap(vec2 q){vec4 p=texture(source,q);return vec4(decode(p.rgb),p.a);}
            float contrast(vec4 p){return dot(p.rgb,vec3(.299,.587,.114))+.18*p.a;}
            void main(){
                if(supersampled>.5){
                    vec2 d=stepUV*.25;
                    pixel=encode(.25*(linearTap(uv+d)+linearTap(uv-d)+linearTap(uv+vec2(d.x,-d.y))+linearTap(uv+vec2(-d.x,d.y))));
                    return;
                }
                vec4 center=texture(source,uv);
                float nw=contrast(texture(source,uv+stepUV*vec2(-1,1)));
                float ne=contrast(texture(source,uv+stepUV));
                float sw=contrast(texture(source,uv-stepUV));
                float se=contrast(texture(source,uv+stepUV*vec2(1,-1)));
                float c=contrast(center);
                float lo=min(c,min(min(nw,ne),min(sw,se))),hi=max(c,max(max(nw,ne),max(sw,se)));
                if(hi-lo<max(.035,hi*.1)){pixel=center;return;}
                vec2 dir=vec2(-((sw+se)-(nw+ne)),(sw+nw)-(se+ne));
                float reduce=max((sw+se+ne+nw)*.03125,.0078125);
                dir=clamp(dir/(min(abs(dir.x),abs(dir.y))+reduce),-vec2(6),vec2(6))*stepUV;
                vec4 a=.5*(linearTap(uv-dir/6.)+linearTap(uv+dir/6.));
                vec4 b=.5*a+.25*(linearTap(uv-dir*.5)+linearTap(uv+dir*.5));
                float candidate=contrast(encode(b));
                pixel=encode(candidate<lo||candidate>hi?a:b);
            }""")
            self.shader=gpu.shader.create_from_info(info)
            self.batch=batch_for_shader(self.shader,'TRIS',{'position':[(-1.,-1.),(3.,-1.),(-1.,3.)]})
        if self.output is None or (self.output.width,self.output.height)!=(w,h):
            self.close_target()
            self.output=gpu.types.GPUOffScreen(w,h)
        viewport=gpu.state.viewport_get()
        try:
            with self.output.bind():
                gpu.state.viewport_set(0,0,w,h)
                self.shader.bind()
                texture.filter_mode(True)
                self.shader.uniform_sampler('source',texture)
                self.shader.uniform_float('stepUV',(1/w,1/h))
                self.shader.uniform_float('supersampled',float(texture.width>w))
                self.batch.draw(self.shader)
        finally:
            gpu.state.viewport_set(*viewport)
        return self.output.texture_color

    def close_target(self):
        if self.output:
            self.output.free()
            self.output=None

