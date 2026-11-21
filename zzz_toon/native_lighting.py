"""Native NapAvatarUI fragment 156 math, with explicit unresolved runtime inputs.

Formula provenance: ref/native_v06/decoded/miHoYo_Character_NapAvatarUI.
Defaults below are neutral test fixtures, NOT recovered outfit-screen globals.
No character name, camera adjustment, exposure or fitted gain is used here.
"""
import json
import math
from . import light_profiles

DEFAULTS = dict(
    entity_info_y=False, entity_7=1., entity_8=1., entity_13=0.,
    avatar_light_alpha=1., light_attenuation=1., shadow_visibility=1.,
    rim_style=(1.,0.,1.,1.), rim_intensity=1., rim_color=(1.,1.,1.),
    vertex_ambient=(0.,0.,0.), stencil_receiver=False, rim_width=1.,
)

TINT_FIELDS = {prefix + '_' + band for prefix in ('skin', 'post')
               for band in ('shallow', 'shallow_fade', 'shadow', 'shadow_fade', 'front', 'sss')}


def load_globals(path):
    """Load one explicit GAME snapshot, or its native_globals member in an overrides file."""
    with open(path, encoding="utf-8-sig") as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError("Expected an object containing shader globals")
    values = document.get("native_globals", document)
    if not isinstance(values, dict) or not values:
        raise ValueError("Expected at least one named shader global")

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    for key, value in values.items():
        if key in TINT_FIELDS:
            valid = isinstance(value, list) and len(value) in (3, 4) and all(map(number, value))
        elif key not in DEFAULTS:
            raise ValueError(f"Unrecognized shader global '{key}'")
        elif isinstance(DEFAULTS[key], tuple):
            valid = isinstance(value, list) and len(value) == len(DEFAULTS[key]) and all(map(number, value))
        elif isinstance(DEFAULTS[key], bool):
            valid = isinstance(value, bool)
        else:
            valid = number(value)
        if not valid:
            raise ValueError(f"Invalid value for shader global '{key}'")
    return values


class Ops:
    def __init__(self,b): self.b=b
    def m(self,op,a,b=None): return self.b.math(op,a,b)
    def add(self,a,b): return self.m('ADD',a,b)
    def sub(self,a,b): return self.m('SUBTRACT',a,b)
    def mul(self,a,b): return self.m('MULTIPLY',a,b)
    def div(self,a,b): return self.m('DIVIDE',a,b)
    def pow(self,a,b): return self.m('POWER',a,b)
    def sat(self,a): return self.b.math('ADD',a,0.,clamp=True)
    def smooth(self,a):
        t=self.sat(a); return self.mul(self.mul(t,t),self.sub(3.,self.mul(2.,t)))
    def mix(self,f,a,b): return self.add(a,self.mul(f,self.sub(b,a)))
    def c(self,a): return self.b.rgb(a) if isinstance(a,tuple) else a
    def v(self,op,a,b=None):
        n=self.b.node('ShaderNodeVectorMath',operation=op)
        for i,v in enumerate((a,b)):
            if v is None:continue
            if isinstance(v,tuple):n.inputs[i].default_value=v[:3]
            else:self.b.link(v,n.inputs[i])
        return n.outputs['Value'] if op in ('DOT_PRODUCT','LENGTH') else n.outputs[0]
    def scale(self,c,s):
        n=self.b.node('ShaderNodeVectorMath',operation='SCALE')
        if isinstance(c,tuple):n.inputs[0].default_value=c[:3]
        else:self.b.link(c,n.inputs[0])
        if isinstance(s,(float,int)):n.inputs['Scale'].default_value=s
        else:self.b.link(s,n.inputs['Scale'])
        return n.outputs[0]
    def cmix(self,f,a,b):return self.b.mixc(f,a,b,clamp_factor=False)
    def cpow(self,c,p):
        sep=self.b.node('ShaderNodeSeparateXYZ');self.b.link(c,sep.inputs[0])
        out=self.b.node('ShaderNodeCombineXYZ')
        for i in range(3):self.b.link(self.pow(sep.outputs[i],p),out.inputs[i])
        return out.outputs[0]


def inputs(p):
    g=dict(DEFAULTS); g.update(p.get('native_globals',{}))
    for k,v in list(g.items()):
        if isinstance(v,list):g[k]=tuple(v)
    return g


def shadow(b,s,a,visibility,skin,shallow,shadow_color,vertex_alpha,p,light_rgb=None,distance_attenuation=None):
    """All seven raw weights, entity redistribution, six tints, light normalization.

    light_attenuation is native r9.x before entity mixing, not a guessed radius.
    Shadow map sampling/ambient-gradient inputs must be supplied by the engine.
    """
    o=Ops(b); g=inputs(p); a=o.m('MAXIMUM',a,1e-5)
    den=o.sub(2.,o.mul(3.,a)); three=o.mul(3.,s)
    q=o.div(o.add(3.,three),den)
    u=o.div(o.sub(o.add(1.,three),o.mul(1.5,a)),den)
    v=o.div(o.sub(o.sub(three,1.),o.mul(4.5,a)),den)
    t=o.add(o.div(o.add(s,.333299994),a),.5)
    m=o.add(o.div(o.sub(s,.333299994),a),.5); n=o.sub(m,1.)
    minimum=lambda x,y:o.sat(o.m('MINIMUM',x,y))
    deep=o.sat(o.sub(1.,q)); fade=minimum(o.sub(1.,t),q)
    sf=minimum(t,o.sub(1.,u)); sh=minimum(o.sub(1.,m),u)
    ss=minimum(o.sub(1.,n),m); front=minimum(n,o.sub(1.,v)); lit=o.sat(v)
    # r3.y originally held M.R, but is overwritten by screen/entity shadow
    # sampling at native line 174. Its live value here is visibility, NOT M.R.
    twice=o.mul(2.,visibility)
    x=minimum(twice,o.sub(2.,twice)); y=o.sat(o.sub(twice,1.)); z=o.sat(o.sub(1.,twice))
    if g['entity_info_y']:
        k=g['avatar_light_alpha']*g['entity_8']
        ty=o.mul(x,k);tz=o.add(y,o.mul(o.add(z,x),1-k))
        z=o.add(o.mul(z,k),o.mul(o.add(ty,tz),1-g['entity_7']))
        x=o.mul(ty,g['entity_7']);y=o.mul(tz,g['entity_7'])
    else:
        x=o.mul(x,g['entity_8']);z=o.mul(z,g['entity_8'])
    sf=o.add(sf,o.mul(z,o.sub(o.sub(o.sub(1.,deep),fade),sf)))
    sh=o.mul(sh,o.add(x,y));ss=o.add(o.mul(ss,o.add(x,y)),o.mul(o.add(front,lit),x))
    front=o.mul(front,y);lit=o.mul(lit,y)
    v7x=o.sub(1.,o.mul(g['entity_13'],vertex_alpha))
    df=o.mul(v7x,deep); fade=o.add(fade,o.mul(o.sub(1.,v7x),deep))
    tint=lambda name:o.cmix(skin,tuple(g.get('post_'+name,p.get('post_'+name,p['post_shadow']))),
                                 tuple(g.get('skin_'+name,g.get('post_'+name,p.get('post_'+name,p['post_shadow'])))))
    dark=o.v('ADD',o.scale(tint('shadow_fade'),df),o.scale(tint('shadow'),fade))
    dark=o.v('MULTIPLY',dark,shadow_color)
    mid=o.v('ADD',o.scale(tint('shallow_fade'),sf),o.scale(tint('shallow'),sh))
    dark=o.v('ADD',dark,o.v('MULTIPLY',mid,shallow))
    bright=o.v('ADD',o.scale(tint('sss'),ss),o.scale(tint('front'),front))
    bright=o.v('ADD',bright,o.scale((1.,1.,1.),lit))
    if light_rgb is not None:
        att=g['light_attenuation'] if distance_attenuation is None else distance_attenuation
        att=o.add(att,o.mul(g['entity_8'],o.sub(1.,att)))
        scaled=o.scale(light_rgb,att)
        sep=b.node('ShaderNodeSeparateXYZ');b.link(light_rgb,sep.inputs[0])
        maximum=o.m('MAXIMUM',o.m('MAXIMUM',sep.outputs[0],sep.outputs[1]),sep.outputs[2])
        norm=o.m('MINIMUM',1.,o.div(1.,o.add(maximum,1.17549435e-38)))
        return o.v('ADD',o.v('MULTIPLY',dark,o.scale(scaled,norm)),o.v('MULTIPLY',bright,scaled))
    light=tuple(p['game_light'][:3]); attenuation=g['light_attenuation']+g['entity_8']*(1-g['light_attenuation'])
    scaled=tuple(c*attenuation for c in light)
    norm=min(1.,1/max(max(light),1.17549435e-38))
    return o.v('ADD',o.v('MULTIPLY',dark,tuple(c*norm for c in scaled)),o.v('MULTIPLY',bright,scaled))


def rim(b,normal,view,light,distance,skin,hair,diffuse,f0,metal,ramp,sun,rim_color,p):
    """Native HDR rim before GBuffer encoding; all operations preserve HDR."""
    o=Ops(b);g=inputs(p);style=g['rim_style'];vis=g['shadow_visibility']
    q=o.pow(o.sat(o.mul(o.sub(1.,o.v('DOT_PRODUCT',view,light)),.5)),2.)
    ra=o.add(.5,o.mul(.5,q))
    sep=b.node('ShaderNodeSeparateXYZ');b.link(normal,sep.inputs[0])
    vertical=sep.outputs[2]
    profile=light_profiles.resolve(p.get('native_light_profile'))
    if profile and profile['up'] != (0.,0.,1.):
        vertical=o.v('DOT_PRODUCT',normal,profile['up'])
    z=o.add(.5,o.mul(.5,vertical));z=o.smooth(o.mix(skin,o.mul(z,z),z))
    z5=o.pow(z,5.);zf=o.mix(skin,o.add(.7,o.mul(.3,z5)),o.sub(1.,o.mul(.5,z5)))
    height=o.add(.1,o.mul(style[0],o.sub(o.mul(z,zf),.1)))
    lit=o.add(.05,o.mul(.95*vis,o.sat(o.v('DOT_PRODUCT',normal,light))))
    amplitude=o.mul(o.mul(ra,height),o.mul(lit,min(1,max(0,.6+vis+.4*g['entity_8']))))
    d=o.m('MINIMUM',1.,o.mul(distance,1/12))
    lower=o.add(o.mix(skin,.65,.75),o.mul(-.45,d))
    fresnel=o.smooth(o.div(o.sub(o.sub(1.,o.v('DOT_PRODUCT',view,normal)),lower),o.mix(skin,.3,.2)))
    grey=o.v('DOT_PRODUCT',sun,(.3,.6,.1))
    chosen=o.cmix(skin,o.scale((1.,1.,1.),grey),sun)
    lum=o.mix(skin,grey,o.v('DOT_PRODUCT',sun,(.33,.33,.33)))
    sun8=o.cpow(chosen,8.)
    tint=o.scale(sun8,o.div(lum,o.m('MAXIMUM',.01,o.v('DOT_PRODUCT',sun8,(.7,.7,.7)))))
    tint=o.cmix(vis,ramp,tint);tint=o.cmix(o.pow(q,20.),tint,ramp);tint=o.cmix(style[2],ramp,tint)
    lum=o.v('DOT_PRODUCT',diffuse,(.33,.33,.33))
    k=o.mul(.1,o.sub(1.,o.mul(.2*style[3],o.mul(lum,lum))))
    direction=o.cpow(o.v('MINIMUM',o.v('MAXIMUM',diffuse,(0.,0.,0.)),(1.,1.,1.)),.2)
    direction=o.scale(direction,o.pow(o.m('MAXIMUM',2**-14,o.v('DOT_PRODUCT',direction,direction)),-.5))
    energy=o.scale(o.cmix(metal,o.scale(direction,k),f0),48*g['rim_intensity'])
    result=o.v('MULTIPLY',o.v('MULTIPLY',tint,energy),rim_color)
    fade=o.sub(1.,o.mul(.7,o.sat(o.sub(o.mul(.2,distance),1.))))
    result=o.scale(result,o.mul(o.mul(amplitude,fresnel),fade))
    total=o.v('DOT_PRODUCT',result,(1.,1.,1.))
    result=o.scale(result,o.add(1.,o.mul(.05,o.mul(total,total))))
    result=o.scale(result,o.mix(hair,1.,.2))
    result=o.v('MULTIPLY',result,g['rim_color'])
    result=o.v('MULTIPLY',result,tuple(1+2*x for x in g['vertex_ambient']))
    return o.scale(result,0. if g['stencil_receiver'] else p['game_rim_gain'])


def encode_rim(b,rim):
    o=Ops(b)
    return o.v('MINIMUM',o.scale(o.cpow(o.v('MAXIMUM',rim,(0.,0.,0.)),.5),.2),(1.,1.,1.))
