"""Native Outline PS12 colour math. Explicit linear inputs, no fitted gain.

The caller supplies the original (unexpanded) surface position, interpolated
baked outline normal after native view reflection, light direction and visibility.
No scene globals or single-capture defaults are installed by this module.
"""
from .native_lighting import Ops

def color(b,diffuse,metal,outline_rgb,outline_alpha,material_rgb,ndl,
          visibility,post_tint,light_color,ambient,vertex_ambient=(0.,0.,0.)):
    o=Ops(b)
    def split(v):
        n=b.node('ShaderNodeSeparateXYZ');b.link(v,n.inputs[0]);return list(n.outputs)
    def combine(v):
        n=b.node('ShaderNodeCombineXYZ')
        for i,x in enumerate(v):
            if isinstance(x,(int,float)):n.inputs[i].default_value=x
            else:b.link(x,n.inputs[i])
        return n.outputs[0]
    surface=o.v('MULTIPLY',diffuse,material_rgb)
    surface=o.scale(surface,o.sub(1.,metal))
    c=[o.sat(o.add(1.,o.mul(outline_alpha,o.sub(x,1.)))) for x in split(surface)]
    # Exact epsilon-bearing RGB->HSV sequence used by the native shader.
    r,g,blue=c;choose=o.sub(1.,o.m('LESS_THAN',g,blue))
    p=[o.mix(choose,a,z) for a,z in zip((blue,g,-1.,2/3),(g,blue,0.,-1/3))]
    choose=o.sub(1.,o.m('LESS_THAN',r,p[0]))
    q=[o.mix(choose,a,z) for a,z in zip((p[0],p[1],p[3],r),(r,p[1],p[2],p[0]))]
    delta=o.sub(q[0],o.m('MINIMUM',q[3],q[1]))
    hue=o.m('ABSOLUTE',o.add(q[2],o.div(o.sub(q[3],q[1]),o.add(o.mul(6.,delta),.0001))))
    saturation=o.div(delta,o.add(q[0],.0001))
    value=o.mul(q[0],o.sub(.8,o.mul(.2,o.add(1.,o.sub(q[0],saturation)))))
    value=o.mul(value,o.add(.5,o.mul(.5,o.mul(visibility,o.sat(o.mul(10.000001,o.sub(ndl,.28)))))))
    wave=[o.sat(o.sub(o.m('ABSOLUTE',o.sub(o.mul(6.,o.m('FRACT',o.add(hue,k))),3.)),1.)) for k in (1.,2/3,1/3)]
    rgb=combine([o.mul(value,o.add(1.,o.mul(saturation,o.sub(x,1.)))) for x in wave])
    rgb=o.v('MULTIPLY',o.v('MULTIPLY',rgb,outline_rgb),material_rgb)
    light=split(light_color)
    maximum=o.m('MAXIMUM',light[0],o.m('MAXIMUM',light[1],light[2]))
    light_color=o.scale(light_color,o.div(1.,o.m('MAXIMUM',1.,maximum)))
    light_color=o.v('ADD',light_color,vertex_ambient)
    return o.v('ADD',o.v('MULTIPLY',o.v('MULTIPLY',rgb,post_tint),light_color),o.v('MULTIPLY',rgb,ambient))
