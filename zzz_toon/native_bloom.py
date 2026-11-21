"""Opt-in dynamic NAP bloom from matched DXBC, without captured image inputs.

The shader kernels are native constants. Scene thresholds, gains, sizes and
weights require an explicit profile; no captured scene becomes a default.
A verified profile can supply a row atlas and R11G11B10 write precision between
passes. Working sizes preserve normalized screen-height radius
as render resolution/aspect changes; this adaptation is not recovered CPU code.
"""
import math

KERNELS = {'6': [[-4.09528494, 0.00570466183], [-2.22262812, 0.133484393], [-0.437803, 0.501891971], [1.32076705, 0.323496908], [3.14797401, 0.03487847], [5.0, 0.000543567527]], '9': [[-7.15882015, 0.000964867824], [-5.22749805, 0.0151298102], [-3.31476212, 0.100958303], [-1.41741204, 0.288899988], [0.472244591, 0.356403589], [2.36454797, 0.189770803], [4.26889801, 0.0434656292], [6.19080782, 0.00425362587], [8.0, 0.000153239904]], '16': [[-14.26509, 0.000146320002], [-12.2933798, 0.000947096676], [-10.3233604, 0.00464627193], [-8.35486317, 0.0172795802], [-6.38767719, 0.0487266295], [-4.42154217, 0.104202203], [-2.45616198, 0.169012904], [-0.491210788, 0.207937002], [1.47365403, 0.194056496], [3.43877792, 0.137373805], [5.40449619, 0.0737620592], [7.37112093, 0.0300378799], [9.33893299, 0.00927573629], [11.3081703, 0.00217165402], [13.2790203, 0.000385392312], [15.0, 3.87885193e-05]], '20': [[-18.3031006, 8.28053671e-05], [-16.3224392, 0.00039338661], [-14.3424101, 0.00156375498], [-12.3629599, 0.00520150177], [-10.3840103, 0.0144784302], [-8.40551472, 0.0337260291], [-6.42738485, 0.0657470524], [-4.44954205, 0.107267298], [-2.47190189, 0.146469697], [-0.494374692, 0.167387903], [1.48312998, 0.160102695], [3.46070194, 0.128165394], [5.43843317, 0.0858689398], [7.41640902, 0.0481489189], [9.3947134, 0.0225949194], [11.3734198, 0.0088734962], [13.3526201, 0.00291622407], [15.3323498, 0.000801990274], [17.3126907, 0.000184551303], [19.0, 2.50982393e-05]]}

def validate(p):
    if p is None:return None
    if not isinstance(p,dict) or p.get('schema')!='zzz-toon-native-bloom/1':raise ValueError('Unknown native Bloom profile')
    if not p.get('source') or not p.get('scope'):raise ValueError('Bloom provenance and scope are required')
    if type(p.get('enabled')) is not bool:raise ValueError('Bloom enabled must be explicit')
    for k in ('threshold','prefilter_gain','intensity'):
        v=p.get(k)
        if type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('Invalid Bloom '+k)
    if p.get('resolution_policy')!='normalized-height':raise ValueError('Unsupported Bloom resolution policy')
    if p.get('intermediate_storage', 'float') not in ('float', 'r11g11b10_truncate'):
        raise ValueError('Unknown Bloom intermediate storage')
    if p.get('output_alpha') not in ('native','opaque_black'):raise ValueError('Explicit Bloom output alpha policy required')
    for k in ('reference_resolution','base_size','compose_size'):
        if not isinstance(p.get(k),list) or len(p[k])!=2 or any(type(v) is not int or v<2 or v>16384 for v in p[k]):raise ValueError('Invalid Bloom '+k)
    layers=p.get('layers')
    if not isinstance(layers,list) or len(layers)!=3:raise ValueError('Three native Bloom layers required')
    for l in layers:
        if not isinstance(l,dict) or str(l.get('kernel')) not in KERNELS:raise ValueError('Unknown Bloom kernel')
        if not isinstance(l.get('size'),list) or len(l['size'])!=2 or any(type(v) is not int or not 2<=v<=2048 for v in l['size']):raise ValueError('Invalid Bloom layer size')
    if 'atlas' in p:
        atlas = p['atlas']
        if not isinstance(atlas, dict):
            raise ValueError('Invalid Bloom atlas')
        size, rows = atlas.get('size'), atlas.get('rows_from_v0')
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 2 <= v <= 16384 for v in size):
            raise ValueError('Invalid Bloom atlas size')
        if not isinstance(rows, list) or len(rows) != len(layers) or any(type(y) is not int for y in rows):
            raise ValueError('One integer row origin is required per Bloom atlas layer')
        intervals = []
        for y, layer in zip(rows, layers):
            w, h = layer['size']
            if y < 0 or y + h > size[1] or w > size[0]:
                raise ValueError('Bloom layer outside atlas')
            intervals.append((y, y + h))
        intervals.sort()
        if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
            raise ValueError('Overlapping Bloom atlas rows')
    if not isinstance(p.get('weights'),list) or len(p['weights'])!=4 or any(type(x) not in (float,int) or not math.isfinite(x) or x<0 for x in p['weights']):raise ValueError('Invalid Bloom compose weights')
    return p

def _packed_write_group():
    """Finite unsigned 5-bit-exponent floats, with 6/6/5 stored fraction bits."""
    import bpy
    from .core import NB
    from .native_lighting import Ops
    tree = bpy.data.node_groups.new('ZZZ Bloom R11G11B10 write', 'CompositorNodeTree')
    for direction in ('INPUT', 'OUTPUT'):
        tree.interface.new_socket(name='RGB', in_out=direction, socket_type='NodeSocketVector')
    class Target:
        node_tree = tree
    b = NB(Target())
    ops = Ops(b)
    src = b.node('NodeGroupInput')
    split = b.node('ShaderNodeSeparateXYZ')
    join = b.node('ShaderNodeCombineXYZ')
    b.link(src.outputs['RGB'], split.inputs[0])
    for channel, bits in enumerate((6, 6, 5)):
        # Clamp finite values to the representable range. Subnormals use the
        # spacing of the lowest normal bin. Rounding takes place once per RT write.
        maximum = (2 - 2 ** -bits) * 2 ** 15
        x = ops.m('MINIMUM', maximum, ops.m('MAXIMUM', 0., split.outputs[channel]))
        exponent = ops.m('FLOOR', ops.m('LOGARITHM', ops.m('MAXIMUM', x, 2 ** -14), 2.))
        scale = ops.pow(2., ops.sub(bits, exponent))
        stored = ops.div(ops.m('FLOOR', ops.mul(x, scale)), scale)
        b.link(stored, join.inputs[channel])
    b.link(join.outputs[0], b.node('NodeGroupOutput').inputs['RGB'])
    return tree

def build(scene,tree,src,profile,return_bloom=False):
    from .core import NB
    from .native_lighting import Ops
    p=validate(profile)
    if not p or not p['enabled']:return src
    class Target:node_tree=tree
    b=NB(Target());o=Ops(b);refw,refh=p['reference_resolution']
    storage = _packed_write_group() if p.get('intermediate_storage') == 'r11g11b10_truncate' else None
    def store(value):
        if storage is None:
            return value
        node = b.node('CompositorNodeGroup', node_tree=storage)
        b.link(value, node.inputs['RGB'])
        return node.outputs['RGB']
    def vec(x,y,z=1.):
        n=b.node('ShaderNodeCombineXYZ')
        for i,v in enumerate((x,y,z)):
            if isinstance(v,(int,float)):n.inputs[i].default_value=v
            else:b.link(v,n.inputs[i])
        return n.outputs[0]
    def split(a):
        n=b.node('ShaderNodeSeparateXYZ');b.link(a,n.inputs[0]);return n.outputs
    def width(value,label):
        n=b.node('ShaderNodeValue');n.label=label
        d=n.outputs[0].driver_add('default_value').driver;d.expression=f'max(2,round({value}*x/y*{refh}/{refw}))'
        for name,path in [('x','render.resolution_x'),('y','render.resolution_y')]:
            v=d.variables.new();v.name=name;v.type='SINGLE_PROP';v.targets[0].id_type='SCENE';v.targets[0].id=scene;v.targets[0].data_path=path
        return n.outputs[0]
    def coords(a):
        n=b.node('CompositorNodeImageCoordinates');b.link(a,n.inputs['Image']);return split(n.outputs['Normalized'])
    def grid(size,label):
        # A realized integer-sized domain keeps odd downsample heights exact.
        canvas=b.node('CompositorNodeBlankImage')
        canvas.label=label
        b.link(vec(width(size[0],label+' width'),size[1]),canvas.inputs['Size'])
        return coords(canvas.outputs['Image'])
    def dims(a):
        n=b.node('CompositorNodeImageInfo');b.link(a,n.inputs['Image']);return split(n.outputs['Dimensions'])
    def sample(a,u,v,interpolation='Bilinear',extension='Extend'):
        n=b.node('CompositorNodeMapUV');b.link(a,n.inputs['Image']);b.link(vec(u,v),n.inputs['UV']);n.inputs['Interpolation'].default_value=interpolation;n.inputs['Extension X'].default_value=extension;n.inputs['Extension Y'].default_value=extension;return n.outputs[0]
    def four(a,size,dx,dy,label):
        uv=grid(size,label);acc=None
        for sx,sy in [(1,1),(1,-1),(-1,-1),(-1,1)]:
            tap=sample(a,o.add(uv[0],o.mul(sx,dx)),o.add(uv[1],o.mul(sy,dy)))
            acc=tap if acc is None else o.v('ADD',acc,tap)
        return o.scale(acc,.25)
    def atlas_uv(u, v, rect):
        x_scale, y_scale, bottom = rect
        return o.mul(u, x_scale), o.add(o.mul(v, y_scale), bottom)
    def gaussian(a,k,axis,tile=None):
        if tile is None:
            uv = coords(a)
        else:
            domain, rect = tile
            uv = atlas_uv(*coords(domain)[:2], rect)
            x_scale, y_scale, bottom = rect
            low, high = (0., x_scale) if axis == 0 else (bottom, bottom + y_scale)
        size=dims(a);acc=None
        for offset,weight in KERNELS[str(k)]:
            # The preview target is vertically inverted for display. In this
            # graph compositor v=0 corresponds to the captured texture's v=0.
            # Older independent-image profiles retain their original tap order.
            delta = o.div(offset if axis == 0 or p.get('atlas') else -offset, size[axis])
            coordinate = o.add(uv[axis], delta)
            # DXBC clamps to the rectangle edge, before the atlas bilinear tap.
            coordinate = o.sat(coordinate) if tile is None else o.m('MINIMUM', high, o.m('MAXIMUM', low, coordinate))
            u, v = (uv[0], coordinate) if axis else (coordinate, uv[1])
            tap=o.scale(sample(a,u,v),weight);acc=tap if acc is None else o.v('ADD',acc,tap)
        return store(acc)
    # Use normalized reference-height texel offsets, independent of render %.
    dx=o.div(1.,width(refw,'Native reference source width'))
    bright=four(src,p['base_size'],dx,1./refh,'NAP bright downsample')
    bright=o.scale(o.v('MAXIMUM',o.v('SUBTRACT',bright,(p['threshold'],)*3),(0.,0.,0.)),p['prefilter_gain'])
    bright=store(bright)
    base=gaussian(gaussian(bright,6,0),6,1);size=dims(base)
    downsamples=[]
    for i,l in enumerate(p['layers']):
        a=four(base,l['size'],o.div(.959999979,size[0]),o.div(.25,size[1]),f'NAP level {i+1}')
        downsamples.append(store(a))
    u,v,_=grid(p['compose_size'],'NAP compose pixel grid')
    taps = [sample(base,u,v)]
    if p.get('atlas'):
        atlas = p['atlas']
        atlas_width, atlas_height = atlas['size']
        au, av, _ = grid(atlas['size'], 'NAP packed layer atlas')
        aw = width(atlas_width, 'NAP atlas width')
        rects = []
        for tile, y, layer in zip(downsamples, atlas['rows_from_v0'], p['layers']):
            h = layer['size'][1]
            rects.append((o.div(dims(tile)[0], aw), h / atlas_height, y / atlas_height))

        def pack(tiles):
            packed = None
            for tile, (sx, sy, bottom) in zip(tiles, rects):
                # Copy exact texels into disjoint rectangles; untouched texels are black.
                value = sample(tile,o.div(au,sx),o.div(o.sub(av,bottom),sy),'Nearest','Clip')
                packed = value if packed is None else o.v('ADD',packed,value)
            return packed

        packed = pack(downsamples)
        for axis in (0, 1):
            filtered = [gaussian(packed, layer['kernel'], axis, (tile, rect))
                        for layer, tile, rect in zip(p['layers'], downsamples, rects)]
            packed = pack(filtered)
        taps.extend(sample(packed, *atlas_uv(u,v,rect)) for rect in rects)
    else:
        for tile, layer in zip(downsamples, p['layers']):
            filtered = gaussian(gaussian(tile,layer['kernel'],0),layer['kernel'],1)
            taps.append(sample(filtered,u,v))
    bloom=None
    for value,w in zip(taps,p['weights']):
        tap=o.scale(value,w);bloom=tap if bloom is None else o.v('ADD',bloom,tap)
    # Resample generated bloom into the current source domain.
    uv=coords(src);bloom=sample(store(bloom),uv[0],uv[1])
    if return_bloom:return bloom
    channels=split(bloom);addition=[]
    for x in channels:
        mapped=o.mix(o.sub(1.,o.m('LESS_THAN',x,.300000012)),x,o.sub(o.mul(1.49380159,o.pow(o.m('ABSOLUTE',x),.333333343)),.699999988))
        addition.append(o.mul(mapped,p['intensity']))
    result=o.v('ADD',src,vec(*addition));rgb=split(result);result=vec(*(o.sat(x) for x in rgb))
    out=b.node('CompositorNodeSetAlpha');b.link(result,out.inputs['Image']);out.inputs['Type'].default_value='Replace Alpha'
    if p['output_alpha']=='opaque_black':out.inputs['Alpha'].default_value=1.
    else:
        sep=b.node('CompositorNodeSeparateColor');b.link(src,sep.inputs['Image'])
        b.link(o.sat(o.add(sep.outputs['Alpha'],o.mul(.333299994,o.add(o.add(addition[0],addition[1]),addition[2])))),out.inputs['Alpha'])
    out.label='Native dynamic NAP Bloom / explicit scene profile'
    return out.outputs[0]
