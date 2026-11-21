"""Composite screen-space scrolling art behind the existing premultiplied character and Bloom."""
import math
from pathlib import Path
import bpy


def attach(scene,folder,page,role,custom=None,art=None):
    art=art or {}
    tree=scene.compositing_node_group
    output=next(n for n in tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output)
    char=output.inputs[0].links[0].from_socket
    g=bpy.data.node_groups.new('Outfit · scrolling background','CompositorNodeTree')
    g.interface.new_socket(name='Character and Bloom',in_out='INPUT',socket_type='NodeSocketColor')
    g.interface.new_socket(name='Preview',in_out='OUTPUT',socket_type='NodeSocketColor')
    def node(t,label=''):
        n=g.nodes.new(t)
        n.label=label
        return n
    def set_(socket,value):
        if isinstance(value,bpy.types.NodeSocket):
            g.links.new(value,socket)
        else:
            socket.default_value=value
    def math_(op,a,b):
        n=node('ShaderNodeMath')
        n.operation=op
        set_(n.inputs[0],a)
        set_(n.inputs[1],b)
        return n.outputs[0]
    def uv(x,y):
        n=node('ShaderNodeCombineXYZ')
        set_(n.inputs['X'],x)
        set_(n.inputs['Y'],y)
        n.inputs['Z'].default_value=1
        return n.outputs[0]
    images={}
    def artwork(path,linear_mask):
        key=(path,linear_mask)
        if key not in images:
            im=bpy.data.images.load(str(path),check_existing=False)
            if min(im.size)<=0:
                raise ValueError('Cannot decode background image '+str(path))
            if linear_mask:
                im.colorspace_settings.name='Non-Color'
            im.pack()
            images[key]=im
        return images[key]
    def image(path,coords,repeat=False,linear_mask=False,label=None):
        n=node('CompositorNodeImage',label or path.stem)
        n.image=artwork(path,linear_mask)
        mapped=node('CompositorNodeMapUV')
        if repeat:
            mapped.inputs['Extension X'].default_value='Repeat'
            mapped.inputs['Extension Y'].default_value='Repeat'
        set_(mapped.inputs['Image'],n.outputs['Image'])
        set_(mapped.inputs['UV'],coords)
        return mapped.outputs[0]
    foreground=node('NodeGroupInput').outputs[0]
    coord=node('CompositorNodeImageCoordinates','Screen coordinates')
    set_(coord.inputs[0],foreground)
    normalized=coord.outputs['Normalized']
    sep=node('ShaderNodeSeparateXYZ')
    set_(sep.inputs[0],normalized)
    time=node('ShaderNodeValue','Timeline seconds')
    f=time.outputs[0].driver_add('default_value').driver
    for name,path in [('fps','render.fps'),('base','render.fps_base'),('start','frame_start')]:
        var=f.variables.new()
        var.name=name
        var.targets[0].id_type='SCENE'
        var.targets[0].id=scene
        var.targets[0].data_path=path
    f.expression='(frame-start)*base/fps'
    t=time.outputs[0]
    c,s=math.cos(math.pi/12),math.sin(math.pi/12)
    motion=page['motion'].get(role,page['motion']['Remielle'])
    if custom:
        im=artwork(custom,False)
        w,h=im.size
        if min(w,h)<=0:
            raise ValueError('Cannot decode '+str(custom))
        fit=max(1920/w,1080/h)
        speed=motion['top']['logical_pixels_per_second']
        coords=[]
        for i,(span,size,velocity) in enumerate(((1920,w,-speed*c),(1080,h,-speed*s))):
            x=math_('MULTIPLY',math_('SUBTRACT',sep.outputs[i],.5),span/(size*fit))
            coords.append(math_('ADD',math_('ADD',x,.5),math_('MULTIPLY',t,velocity/(size*fit))))
        bg=image(custom,uv(*coords),repeat=True)
    else:
        x=math_('MULTIPLY',math_('SUBTRACT',sep.outputs[0],.5),1920)
        y=math_('MULTIPLY',math_('SUBTRACT',sep.outputs[1],.5),1080)
        along=math_('ADD',math_('MULTIPLY',x,c),math_('MULTIPLY',y,s))
        across=math_('SUBTRACT',math_('MULTIPLY',y,c),math_('MULTIPLY',x,s))
        masks=[]
        for name,row in page['rows'].items():
            m=motion[name]
            phase=math_('ADD',m['phase_at_zero'],math_('MULTIPLY',t,m['logical_pixels_per_second']))
            distance=math_('FLOORED_MODULO',math_('SUBTRACT',along,phase),row['period'])
            u=math_('DIVIDE',distance,row['width'])
            v=math_('DIVIDE',math_('SUBTRACT',across,row['low']),row['height'])
            key='small' if name=='middle' else 'large'
            file=Path(art.get(key,folder/(key+'.png')))
            # DDS row zero in the captured UI quad corresponds to the lower
            # edge; PNG image sampling in Blender uses bottom-origin UVs.
            masks.append(image(file,uv(u,math_('SUBTRACT',1,v)),linear_mask=True,label=key))
        mask=math_('ADD',math_('ADD',masks[0],masks[1]),masks[2])
        shade=Path(art.get('screen_shade',folder/'screen_shade.png'))
        bg=math_('MULTIPLY',mask,image(shade,uv(sep.outputs[0],sep.outputs[1]),label='screen_shade'))
    separate=node('CompositorNodeSeparateColor')
    set_(separate.inputs[0],foreground)
    lower=node('ShaderNodeVectorMath')
    lower.operation='SCALE'
    set_(lower.inputs[0],bg)
    set_(lower.inputs['Scale'],math_('SUBTRACT',1,separate.outputs['Alpha']))
    final=node('ShaderNodeVectorMath','Preserve character and halo RGB')
    final.operation='ADD'
    set_(final.inputs[0],foreground)
    set_(final.inputs[1],lower.outputs[0])
    opaque=node('CompositorNodeSetAlpha')
    opaque.inputs['Type'].default_value='Replace Alpha'
    opaque.inputs['Alpha'].default_value=1
    set_(opaque.inputs[0],final.outputs[0])
    result=node('NodeGroupOutput')
    set_(result.inputs[0],opaque.outputs[0])
    # Lay the generated graph out by its actual dependencies.
    depth={}
    def column(n):
        if n not in depth:
            depth[n]=max((column(link.from_node)+1 for i in n.inputs for link in i.links),default=0)
        return depth[n]
    counts={}
    for n in g.nodes:
        level=column(n)
        n.location=(level*185,-counts.get(level,0)*210)
        counts[level]=counts.get(level,0)+1
    wrap=tree.nodes.new('CompositorNodeGroup')
    wrap.node_tree=g
    wrap.label='滚动背景与角色'
    wrap.location=(output.location.x,output.location.y)
    output.location.x+=220
    tree.links.new(char,wrap.inputs[0])
    tree.links.new(wrap.outputs[0],output.inputs[0])

