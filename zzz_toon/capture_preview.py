"""Reconstruct a static outfit-preview frame from verified, post-skin draw buffers."""
import re

import bpy
import numpy as np

from . import capture_import as cap
from . import capture_space, outline_runtime, preview
from .outfit_import import BASIS

HULL = ('3dc9820c6068ed8b', 'a9f95d035488717a')
FACE_HULL = ('456d459c80544591', 'dcab6417cc0a8e70')
CASTER = ('bcddceab76ee10ce', 'c07b54c6554b2591')
PRIMARY = set(capture_space.FEATURE_PASSES) | {
    (cap.BODY_VS, p) for p in (cap.BODY_PS, '5cdfe58cc1acd0bd', 'cb6f69d885830458')
}
EVENT = re.compile(r'^(\d+)-ib=[0-9a-f]+-vs=([0-9a-f]{16})-ps=([0-9a-f]{16})\.txt$')
STAGE = 'zzz_raw_capture_preview'
C = np.asarray(BASIS)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def bindings(folder, event, pair):
    result = {}
    suffix = '-vs=%s-ps=%s.' % pair
    for path in folder.glob(event + '-*'):
        if suffix not in path.name:
            continue
        key = path.name.split('-', 1)[1].split('=', 1)[0] + path.suffix
        if key in ('ib.buf','ib.txt','vb0.buf','vb0.txt','vb1.buf','vb1.txt','vs-cb0.buf',
                   'vs-cb1.buf','vs-cb2.buf','ps-cb0.buf','ps-cb2.buf','ps-t0.dds','ps-t1.buf'):
            require(key not in result, 'Ambiguous draw binding ' + event + ' ' + key)
            result[key] = path
    return result


def check_field(header, name, index, fmt, slot, offset):
    pattern = (r'SemanticName: %s\s+SemanticIndex: %d\s+Format: %s\s+InputSlot: %d\s+'
               r'AlignedByteOffset: %d\s+InputSlotClass: per-vertex') % (name,index,fmt,slot,offset)
    require(re.search(pattern,header), 'Unsupported vertex field ' + name)


def match_mesh(objects, files, caster):
    buf = files['vb0.buf']
    require(buf.stat().st_size % 40 == 0, 'Unexpected post-skin vertex stride')
    count = buf.stat().st_size // 40
    h = cap._header(files['vb0.txt'])
    require(h.startswith('stride: 40\n'), 'Unsupported vertex layout')
    check_field(h,'POSITION',0,'R32G32B32_FLOAT',0,0)
    check_field(h,'NORMAL',0,'R32G32B32_FLOAT',0,12)
    if caster:
        ih = cap._header(files['ib.txt'])
        require('topology: trianglelist' in ih, 'Unsupported caster primitive')
        fmt = '<u2' if 'DXGI_FORMAT_R16_UINT' in ih else '<u4' if 'DXGI_FORMAT_R32_UINT' in ih else None
        require(fmt, 'Unsupported caster index type')
        tri = np.fromfile(files['ib.buf'],fmt).reshape(-1,3)
        uv = None
    else:
        parsed = cap._draw_triangles(files)
        require(parsed is not None, 'Unsupported indexed UV layout')
        tri,uv,_ = parsed
        require(len(uv)==count, 'UV and position stream lengths differ')
    candidates = []
    for obj in objects:
        me = obj.data
        if len(me.vertices)!=count or len(me.loops)!=tri.size:
            continue
        idx = np.empty(tri.size, 'i')
        me.loops.foreach_get('vertex_index',idx)
        if not np.array_equal(np.sort(idx.reshape(-1,3),axis=1), np.sort(tri,axis=1)):
            continue
        if caster:
            names=[m.name.split('.')[0] for m in me.materials if m]
            if not ('MAT_HairShadow_UI' in names or
                    (not names and obj.name.split('.')[0].lower().endswith('_hairshadow'))):
                continue
        else:
            layer = me.uv_layers.get('UV0')
            if not layer:
                continue
            coords = np.empty(idx.size*2,'f')
            layer.data.foreach_get('uv',coords)
            if not np.allclose(coords.reshape(-1,2),uv[idx],rtol=0,atol=2e-6):
                continue
        candidates.append(obj)
    require(len(candidates)==1,'No unique full triangle/UV match for ' + buf.name.split('-')[0])
    return candidates[0]


def identity(files):
    # The remainder of CB1 includes pass-specific previous-frame matrices.
    return (files['vb0.buf'].read_bytes(), files['vs-cb1.buf'].read_bytes()[:128],
            files['ib.buf'].read_bytes(), files['vb1.buf'].read_bytes())


def raw_mesh(obj, files, origin):
    v = np.fromfile(files['vb0.buf'],'<f4').reshape(-1,10).astype(float)
    matrices = np.fromfile(files['vs-cb1.buf'],'<f4')[:32].reshape(2,4,4).astype(float)
    m,inv = matrices
    require(np.isfinite(v[:,:6]).all() and np.allclose(m@inv,np.eye(4),atol=.002,rtol=0)
            and np.allclose(m[:,3],[0,0,0,1]), 'Invalid captured model transform')
    ob = np.asarray(obj.matrix_world)
    local = np.linalg.inv(ob[:3,:3])
    pos = ((v[:,:3]@m[:3,:3]+m[3,:3]-origin)@C.T-ob[:3,3])@local.T
    norm = v[:,3:6]@inv[:3,:3].T@C.T@ob[:3,:3]
    norm /= np.maximum(np.linalg.norm(norm,axis=1,keepdims=True),1e-20)
    return pos.astype('f'),norm.astype('f'),m[:3,:3]@C.T@local.T


def projection(files, size, origin):
    data = np.fromfile(files['vs-cb0.buf'],'<f4')
    require(len(data)==848, 'Unknown global camera layout')
    vp = data[508:524].astype(float).reshape(4,4).T
    a,b,d = vp[0,0],-vp[1,1],vp[3,3]
    require((vp[[0,1,2,2,3,3],[1,0,0,1,0,1]]==0).all() and vp[3,2]==1
            and a>0 and b>0, 'Unsupported outfit preview projection')
    camera = np.array([(vp[0,2]*d-vp[0,3])/a, (vp[1,3]-vp[1,2]*d)/b, -d])
    require(np.allclose(camera,data[220:223],rtol=0,atol=.00015),'Camera centre and VP disagree')
    w,h = size
    require(w>=16 and h>=16 and max(w,h)<=32768,'Invalid capture dimensions')
    ratio = (w/h)/(b/a)
    offset = data[84:86]
    loc = (camera-origin)@C.T
    return dict(camera=dict(world=[[1,0,0,loc[0]],[0,0,-1,loc[1]],[0,1,0,loc[2]],[0,0,0,1]],
                optics=dict(sensor_fit='VERTICAL',sensor_height=24.,lens=b*12,
                            shift_x=float(-(vp[0,2]+offset[0])*b/a/2),
                            shift_y=float((vp[1,2]+offset[1])/2))),
                render=dict(resolution_x=w,resolution_y=h,pixel_aspect_x=max(1,1/ratio),
                            pixel_aspect_y=max(1,ratio)),samples=32)


def hull_inputs(files, transform):
    # VS4 reconstructs the hull direction in the source tangent frame. Only
    # register this implementation for orthogonal transforms (including reflection).
    require(np.allclose(transform@transform.T,np.eye(3),atol=1e-4), 'Scaled outline frame')
    h = cap._header(files['vb1.txt'])
    check_field(h,'TANGENT',0,'R32G32B32A32_FLOAT',0,24)
    field = re.search(r'^stride: (\d+)',h,re.M)
    require(field,'Missing outline vertex stride')
    stride = int(field[1])
    layout = {20:('R8G8B8A8_UNORM',8),24:('R8G8B8A8_UNORM',8),
              48:('R32G32B32A32_FLOAT',24)}.get(stride)
    require(layout,'Unknown outline color/UV layout')
    check_field(h,'COLOR',0,layout[0],1,0)
    check_field(h,'TEXCOORD',1,'R32G32_FLOAT',1,layout[1])
    buf=np.fromfile(files['vb1.buf'],'B').reshape(-1,stride)
    rgba=buf[:,:4]/255. if layout[1]==8 else buf[:,:16].copy().view('<f4')
    xy=buf[:,layout[1]:layout[1]+8].copy().view('<f4')
    vertex=np.fromfile(files['vb0.buf'],'<f4').reshape(-1,10)
    require(np.isfinite(xy).all() and np.isfinite(rgba).all() and np.isfinite(vertex).all()
            and np.allclose(abs(vertex[:,9]),1,atol=1e-5), 'Invalid outline vertex data')
    return dict(rest_n=vertex[:,3:6]@transform, rest_t=vertex[:,6:9]@transform,
                packed=np.c_[xy,vertex[:,9]*np.sign(np.linalg.det(transform))],
                rg=np.c_[rgba[:,:2],np.zeros(len(rgba))],
                hair=((np.rint(rgba[:,2]*255).astype(int)>>5)&1).astype('f'))


def hull_profile(folder, files):
    vs=np.fromfile(files['vs-cb0.buf'],'<f4')
    ps=np.fromfile(files['ps-cb0.buf'],'<f4')
    instance=np.fromfile(files['ps-cb2.buf'],'<f4')
    lights=np.fromfile(files['vs-cb2.buf'],'<f4')
    entity=np.fromfile(files['ps-t1.buf'],'<f4').reshape(-1,32)
    require(len(vs)==len(ps)==848 and len(lights)==len(instance)==164, 'Unknown outline constant layout')
    index=int(instance[10])
    require(index==instance[10] and 0<=index<len(entity) and entity[index,7]==1 and lights[8]==0
            and ps[99]==0, 'Unsupported outline lighting or visibility branch')
    # PS12 samples red from the scene occlusion texture; prove it is constant.
    path=files['ps-t0.dds']
    require(path.stat().st_size<=1024*1024,'Nonconstant/large outline occlusion texture')
    data=path.read_bytes()
    w,h=np.frombuffer(data,'<u4',2,12)
    require(data[:4]==b'DDS ' and data[84:88]==b'DX10' and int.from_bytes(data[128:132],'little')==29
            and len(data)==148+4*int(w)*int(h) and w*h>0 and all(x==255 for x in data[148::4]),
            'Unsupported outline occlusion texture')
    return outline_runtime.validate(dict(schema='zzz-toon-outline/1',source=str(folder),scope='Selected static outfit frame',
            visibility=1.,post_tint=ps[72:75].tolist(),clip_offset=vs[84:86].tolist(),vertex_ambient=[0.,0.,0.]))


def prepare(folder, objects, auto, matched):
    """Preflight all posed geometry before changing the FBX or the scene."""
    meshes=[o for o in objects if o.type=='MESH']
    reg=auto['native_environment']['registration']
    origin=np.asarray(reg['unity_point'])-np.asarray(reg['blender_point'])@C
    require(matched,'No matched character draws')
    if 'compute_draw' in reg:
        start=int(reg['compute_draw'])
    else:
        producers=[int(m[1]) for p in folder.glob('*-cs-t5=*.buf')
                   if (m:=capture_space.ENTITY_DISPATCH.fullmatch(p.name)) and int(m[1])<int(min(matched))]
        require(producers,'No entity dispatch before the registered body')
        start=max(producers)
    events=[]
    for p in folder.glob('*-ib=*-vs=*-ps=*.txt'):
        m=EVENT.fullmatch(p.name)
        if m and int(m[1])>start:
            events.append((m[1],(m[2],m[3])))
    events.sort()
    ends=[e for e,pair in events if pair[1]==cap.DEFERRED_PS]
    require(len(ends)==1 and matched,'No unique character render pass')
    late_cloth=[e for e,p in events if e>ends[0] and p==('1f6ab42231416fdb','d72591ee44f3a191')]
    events=[(e,p) for e,p in events if e<ends[0]]
    boundaries=[e for e,p in events if p in (HULL,FACE_HULL)]
    casters=[e for e,p in events if p==CASTER]
    require(boundaries and len(casters)==1 and casters[0]>boundaries[0],'Incomplete character draw sequence')
    primary=[(e,p) for e,p in events if e<boundaries[0] or p==CASTER]
    poses,signatures,bindings_by_mesh={},{},{}
    # The deferred character depth target records the native projection domain,
    # including in captures with an incomplete post-processing sequence.
    depths=list(folder.glob(ends[0]+'-ps-t1=*-vs=c85e3fc3d2f75d34-ps='+cap.DEFERRED_PS+'.dds'))
    require(len(depths)<=1,'Ambiguous character depth target')
    if depths:
        with depths[0].open('rb') as stream:
            header=stream.read(148)
        require(len(header)==148 and header[:4]==b'DDS ' and header[84:88]==b'DX10' and
                int.from_bytes(header[128:132],'little')==21,'Unknown character depth target')
        size=[int.from_bytes(header[16:20],'little'),int.from_bytes(header[12:16],'little')]
    else:
        size=(auto.get('native_bloom') or {}).get('reference_resolution')
        require(size,'No measured render dimensions')
    camera=None
    for event,pair in primary:
        require(pair in PRIMARY or pair==CASTER,'Unverified character draw '+event)
        files=bindings(folder,event,pair)
        obj=match_mesh(meshes,files,pair==CASTER)
        sig=identity(files)
        if obj in signatures:
            require(sig==signatures[obj],'Conflicting draws for '+obj.name)
        else:
            signatures[obj]=sig
            poses[obj]=raw_mesh(obj,files,origin)
        bindings_by_mesh[event]=obj.name
        if event in matched:
            require(matched[event]==obj.name,'Body correspondence differs')
            proj=projection(files,size,origin)
            require(camera is None or proj==camera,'Conflicting body projections')
            camera=proj
    require(camera and set(matched)<=bindings_by_mesh.keys(),'Missing projected body draw')
    # Later opaque/feature draws must reference an index buffer accounted for
    # above. A whole new object after the first hull is outside this decoder.
    supported_ibs={sig[2] for sig in signatures.values()}
    for event in late_cloth:
        files=bindings(folder,event,('1f6ab42231416fdb','d72591ee44f3a191'))
        require(files['ib.buf'].read_bytes() in supported_ibs,'New transparent geometry at draw '+event)
    for event,pair in events:
        if event>=boundaries[0] and pair!=CASTER:
            file=next(folder.glob(event+'-ib=*.buf'),None)
            require(file and file.read_bytes() in supported_ibs,'Additional geometry at draw '+event)
    outlines,profile,warnings={},None,[]
    incomplete=set()
    for event,pair in events:
        if pair!=HULL:
            continue
        obj=None
        try:
            files=bindings(folder,event,pair)
            obj=match_mesh(meshes,files,False)
            require(obj in poses and identity(files)==signatures[obj], 'Outline geometry differs from body')
            current=hull_profile(folder,files)
            require(profile is None or profile==current,'Conflicting outline environments')
            if obj not in outlines:
                outlines[obj]=hull_inputs(files,poses[obj][2])
            profile=current
        except (KeyError,IndexError,OSError,ValueError) as err:
            warnings.append('Outline draw '+event+': '+str(err))
            if obj is None:
                return camera,poses,bindings_by_mesh,None,{},warnings
            incomplete.add(obj)
    for obj in incomplete:
        outlines.pop(obj,None)
    return camera,poses,bindings_by_mesh,profile if outlines else None,outlines,warnings


def install(scene,objects,prepared):
    camera,poses,draws,profile,outlines,warnings=prepared
    preview.camera(scene,camera)
    for obj in objects:
        if obj.type!='MESH':
            continue
        obj.hide_render=obj.hide_viewport=obj not in poses
        if obj not in poses:
            continue
        pos,n,_=poses[obj]
        obj['zzz_preview_rest_mesh']=obj.data
        obj.data=obj.data.copy()
        if obj.data.shape_keys:
            obj.shape_key_clear()
        for mod in list(obj.modifiers):
            if mod.type=='ARMATURE':
                obj.modifiers.remove(mod)
        obj.data.vertices.foreach_set('co',pos.ravel())
        obj.data.update()
        obj.data.normals_split_custom_set_from_vertices(n.tolist())
        for key,value in outlines.get(obj,{}).items():
            if key=='hair':
                outline_runtime.put(obj.data,'zzz_outline_hair',value,'FLOAT')
            else:
                outline_runtime.put(obj.data,'zzz_ol_'+key,value)
    return dict(draws=draws,objects=sorted(o.name for o in poses),outline_objects=sorted(o.name for o in outlines),
                outline=profile,warnings=warnings)

