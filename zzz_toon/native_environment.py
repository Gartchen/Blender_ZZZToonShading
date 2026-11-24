"""Explicit frame/scene inputs for NapAvatarUI PS1119, lines 176-264 and 631.

All positions and gradient planes use registered Blender world coordinates.
No game capture or character-specific value is used as a default.
"""
import math

def validate(profile):
    if profile is None:return None
    if not isinstance(profile,dict) or profile.get('schema')!='zzz-toon-environment/1':
        raise ValueError('Unknown environment schema')
    if not profile.get('source') or not profile.get('scope'):
        raise ValueError('Environment requires source and scope')
    for name,size in [('main_color',3),('entity_color',3),('entity_position',3),('gradient_direction',3),('gradient_color',4),('ambient_color',3)]:
        v=profile.get(name)
        if not isinstance(v,(tuple,list)) or len(v)!=size or any(not isinstance(x,(float,int)) or not math.isfinite(x) for x in v):
            raise ValueError('Invalid environment '+name)
    for name in ('entity_range','gradient_offset'):
        v=profile.get(name)
        if not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('Invalid environment '+name)
    if not isinstance(profile.get('gradient_enabled'),bool):raise ValueError('gradient_enabled must be explicit')
    return profile

def light(b,position,profile):
    """Return native light RGB and distance attenuation before entity mixing."""
    from .native_lighting import Ops
    p=validate(profile);o=Ops(b)
    delta=o.v('SUBTRACT',tuple(p['entity_position']),position)
    dsq=o.m('MAXIMUM',o.v('DOT_PRODUCT',delta,delta),1.17549435e-38)
    t=o.m('MAXIMUM',0.,o.sub(1.,o.div(dsq,max(p['entity_range']**2,1.17549435e-38))))
    color=o.cmix(t,tuple(p['main_color']),tuple(p['entity_color']))
    attenuation=o.add(1.,o.mul(t,o.sub(t,1.)))
    if p['gradient_enabled']:
        weight=o.sat(o.sub(o.v('DOT_PRODUCT',tuple(p['gradient_direction']),position),p['gradient_offset']))
        g=tuple(p['gradient_color'][:3]);alpha=p['gradient_color'][3]
        product=o.v('MULTIPLY',g,color)
        screen=o.v('SUBTRACT',o.v('ADD',g,color),product)
        target=o.v('ADD',product,o.scale(screen,alpha))
        color=o.cmix(weight,color,target)
    color.node.label = ('Studio world-position light colour' if profile.get('scope') == 'Asset-only wardrobe preview'
                        else 'Native captured world-position light colour')
    return color,attenuation

def ambient(b,surface,diffuse,profile,vertex_ambient):
    return ambient_rgb(b,surface,diffuse,validate(profile)['ambient_color'],vertex_ambient)

def ambient_rgb(b,surface,diffuse,color,vertex_ambient):
    from .native_lighting import Ops
    o=Ops(b)
    # r1 is the curved unlit surface, NOT diffuse*(1-metal) and NOT shadowed colour.
    return o.v('ADD',o.v('MULTIPLY',tuple(color),surface),o.v('MULTIPLY',tuple(vertex_ambient),diffuse))
