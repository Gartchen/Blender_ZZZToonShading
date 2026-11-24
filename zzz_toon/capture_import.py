"""Read proven outfit-preview bindings from an external XXMI FrameAnalysis directory."""
import hashlib
import math
from pathlib import Path
import re
import struct

import numpy as np

from .outfit_import import light_profile
from . import native_bloom, capture_space


BODY_PS = "a24c103adbe204f1"  # NapAvatarUI PS1119
BODY_VS = "c280f6945b23a42a"  # VS44
DEFERRED_PS = "f1f227ccc44b3309"  # DeferredShadingForCharacter PS26
_FILE = re.compile(r"^(\d+)-(ib|vb1|vs-cb[0123]|ps-cb[0123]|ps-t[012356])=.+?-vs=([\da-f]{16})-ps=([\da-f]{16})\.(buf|txt|dds)$", re.I)
_GAUSS_VS = '770187c8bda685a7'
_BLURS = [(_GAUSS_VS, ps) for ps in ('d48dc300168dde5a','3a0dcde915eeb4c3','7edd98cc69fc523f')]
_BLOOM = ([('6e0f6cc2a68627a1','c71aaf77e66ac72f')] + [(_GAUSS_VS,'b3358d7b5c4bf4cf')]*2
          + [('a897e1ccee9ac064','26ab5a565bdb537a')]*3 + _BLURS*2 + [('7ce3d6948d7a44f8','a8395033c794d5d7')])
_UBER = ('ce43e302fadd1328','6443f79de027d780')


def _fingerprint(indices, uvs):
    order = np.argsort(indices, axis=1)
    indices = np.take_along_axis(indices, order, axis=1)
    uvs = np.take_along_axis(uvs, order[..., None], axis=1).astype("<f2")
    uvs[uvs == 0] = 0  # canonical signed zero
    digest = hashlib.sha256(indices.astype("<u4").tobytes())
    digest.update(uvs.tobytes())
    return digest.digest()


def _mesh_key(mesh):
    uv = mesh.uv_layers.get("UV0") or mesh.uv_layers.active
    if not uv or len(mesh.loops) != 3*len(mesh.polygons) or not len(mesh.loops):
        return None
    indices = np.empty(len(mesh.loops), dtype=np.int32)
    coords = np.empty(len(indices)*2, dtype=np.float32)
    mesh.loops.foreach_get("vertex_index", indices)
    uv.data.foreach_get("uv", coords)
    return _fingerprint(indices.reshape(-1,3), coords.reshape(-1,3,2))


def _header(path):
    with path.open(encoding="ascii") as f:
        return f.read(8192).split("vertex-data:")[0]


def _draw_triangles(files):
    ib = _header(files["ib.txt"])
    dtype = "<u2" if "DXGI_FORMAT_R16_UINT" in ib else "<u4" if "DXGI_FORMAT_R32_UINT" in ib else None
    if not dtype or "topology: trianglelist" not in ib:
        return None
    vb = _header(files["vb1.txt"])
    field = re.search(r"SemanticName: TEXCOORD\s+SemanticIndex: 0\s+Format: (R16G16_FLOAT|R32G32_FLOAT)\s+InputSlot: 1\s+AlignedByteOffset: (\d+)", vb)
    stride = re.search(r"^stride: (\d+)", vb, re.M)
    if not field or not stride:
        return None
    raw = np.fromfile(files["vb1.buf"], "B").reshape(-1, int(stride[1]))
    uvtype = np.dtype("<f2" if field[1]=="R16G16_FLOAT" else "<f4")
    offset = int(field[2])
    uv = raw[:,offset:offset+2*uvtype.itemsize].copy().view(uvtype)
    tri = np.fromfile(files["ib.buf"], dtype).reshape(-1,3)
    if not len(tri) or tri.max() >= len(uv) or uv.shape[1] != 2:
        return None
    first = re.search(r"^first index: (\d+)",ib,re.M)
    count = re.search(r"^index count: (\d+)",ib,re.M)
    if not first or not count or int(first[1])%3 or int(count[1]) < 3 or int(count[1])%3 or int(first[1])+int(count[1])>tri.size:
        return None
    return tri, uv, slice(int(first[1])//3,(int(first[1])+int(count[1]))//3)


def _draw_key(files):
    parsed = _draw_triangles(files)
    if parsed is None:
        return None
    tri, uv, _ = parsed
    return _fingerprint(tri, uv[tri])


def _floats(data, offset, count):
    values = list(struct.unpack_from(f"<{count}f", data, offset))
    if not all(map(math.isfinite, values)):
        raise ValueError("Non-finite shader input")
    return values


def _buffer(files, slot, size):
    data = files[slot+".buf"].read_bytes()
    if len(data) != size:
        raise ValueError(f"{slot} does not match the reflected layout")
    return data


def _body_inputs(files):
    # Byte offsets from PS1119 DXBC reflection; asset values never enter this table.
    cb3 = _buffer(files,"ps-cb3",2048)
    light = None
    if struct.unpack_from("<i",cb3,1632)[0] == 1:
        light = [_floats(cb3,offset,3) for offset in (1296,1312)]
        if any(abs(sum(x*x for x in v)-1)>1e-4 for v in light):
            raise ValueError("Invalid captured light vectors")
    cb0 = _buffer(files,"ps-cb0",3392)
    cb2 = _buffer(files,"ps-cb2",656)
    g = {}
    for prefix, offsets in (("post",(48,64,80,128,96,112)),("skin",(160,176,192,224,144,208))):
        for key, offset in zip(("shallow","shallow_fade","shadow","shadow_fade","front","sss"),offsets):
            g[prefix+"_"+key] = _floats(cb0,offset,4)
    g.update(avatar_light_alpha=_floats(cb0,3164,1)[0], entity_info_y=_floats(cb2,644,1)[0]>.5,
             rim_color=_floats(cb0,3204,3), rim_intensity=_floats(cb0,3216,1)[0], rim_style=_floats(cb0,3232,4))
    raw = files["ps-t1.buf"].read_bytes()
    selector = _floats(cb2,40,1)[0]
    index = int(selector)
    if index != selector or index < 0 or len(raw)%128 or (index+1)*128 > len(raw):
        raise ValueError("Invalid entity record index")
    for i in (7,8,13):
        g["entity_"+str(i)] = _floats(raw,index*128+i*4,1)[0]
    return light, g, _floats(cb0,32,3)


def _dimensions(path, packed=False):
    with path.open('rb') as stream:
        header = stream.read(148)
    if header[:4] != b'DDS ':
        raise ValueError("Missing DDS header")
    if packed and (header[84:88] != b'DX10' or struct.unpack_from('<I',header,128)[0] != 26):
        raise ValueError("Unexpected Bloom render target format")
    return list(reversed(struct.unpack_from('<II',header,12)))


def overlay_pixels(data):
    """Reflected linear BC1 texture. DDS row zero is sampled at Blender UV y=0."""
    height,width = struct.unpack_from('<II',data,12)
    if (data[:4] != b'DDS ' or data[84:88] != b'DXT1' or struct.unpack_from('<I',data,28)[0] > 1
            or not (0 < width <= 8192 and 0 < height <= 8192)):
        raise ValueError("Unsupported character overlay texture layout")
    cols,rows = (width+3)//4,(height+3)//4
    if len(data) != 128+rows*cols*8:
        raise ValueError("Incomplete character overlay texture")
    blocks = np.frombuffer(data,'<u2',offset=128).reshape(-1,4)
    endpoints = blocks[:,:2]
    r,g,b = endpoints>>11,(endpoints>>5)&63,endpoints&31
    colors = np.stack(((r<<3)|(r>>2),(g<<2)|(g>>4),(b<<3)|(b>>2)),axis=-1)
    a,b = colors[:,0],colors[:,1]
    opaque = endpoints[:,0,None] > endpoints[:,1,None]
    palette = np.stack((a,b,np.where(opaque,(2*a+b)//3,(a+b)//2),np.where(opaque,(a+2*b)//3,0)),axis=1)
    bits = np.frombuffer(data,'<u4',offset=128).reshape(-1,2)[:,1]
    index = ((bits[:,None]>>(2*np.arange(16,dtype='u4')))&3)
    rgb = palette[np.arange(len(bits))[:,None],index].reshape(rows,cols,4,4,3)
    return rgb.transpose(0,2,1,3,4).reshape(rows*4,cols*4,3)[:height,:width].astype('u1')


def _bloom_inputs(events, folder, after):
    selected = sorted((n, pair, files) for n,(pair,files) in events.items() if n > after)
    if [pair for _,pair,_ in selected] != _BLOOM + [_UBER]:
        raise ValueError("No unique supported blur and UberPost sequence")
    stages = [files for _,_,files in selected]
    bright, compose, uber = stages[0], stages[12], stages[13]
    def identity(path):
        return path.name.split('=',1)[1].split('-vs=',1)[0]
    if identity(bright['ps-t0.dds']) != identity(uber['ps-t0.dds']):
        raise ValueError("Bright pass and UberPost read different character targets")
    ref = _dimensions(bright['ps-t0.dds'])
    base = _dimensions(compose['ps-t0.dds'], packed=True)
    atlas = _dimensions(compose['ps-t1.dds'], packed=True)
    size = _dimensions(uber['ps-t3.dds'], packed=True)
    weights = _floats(_buffer(compose,'ps-cb1',48),0,4)
    packed = _floats(_buffer(bright,'ps-cb0',48),16,4)
    intensity = _floats(_buffer(uber,'ps-cb1',608),336,1)[0]
    rects = _buffer(compose,'ps-cb0',2928)
    identity_uv = [1.,1.,0.,0.]
    bright_vs = _buffer(bright,'vs-cb0',2928)
    if (_floats(bright_vs,2736,4)!=identity_uv or _floats(bright_vs,2752,4)!=identity_uv
            or _floats(bright_vs,2808,2)!=ref):
        raise ValueError("Unsupported bright-pass crop or reference size")
    rows, layers = [], []
    for index,kernel in enumerate((9,16,20)):
        rect = _floats(rects,2752+16*index,4)
        w,h,x,y = np.asarray(rect) * [*atlas,*atlas]
        if x != 0 or any(abs(v-round(v))>1e-4 for v in (w,h,y)):
            raise ValueError("Unsupported Bloom atlas rectangle")
        down = _buffer(stages[3+index],'vs-cb0',2928)
        target = [rect[0],rect[1],rect[0]-1,1-2*rect[3]-rect[1]]
        if (not np.allclose(_floats(down,2736,4),target,rtol=1e-6,atol=1e-6)
                or _floats(down,2752,4)!=identity_uv or _floats(down,2808,2)!=base):
            raise ValueError("Unsupported Bloom downsample transform")
        for level,step in ((6+index,[1/atlas[0],0]),(9+index,[0,1/atlas[1]])):
            cb = _buffer(stages[level],'ps-cb0',2784)
            if not np.allclose(_floats(cb,2768,2),step,rtol=1e-6,atol=1e-8):
                raise ValueError("Unsupported Bloom sampling step")
            if not np.allclose(_floats(cb,2752,4),rect,rtol=1e-6,atol=1e-8):
                raise ValueError("Inconsistent Bloom atlas source")
            if not np.allclose(_floats(cb,2736,4),target,rtol=1e-6,atol=1e-6):
                raise ValueError("Inconsistent Bloom atlas target")
        rows.append(round(y))
        layers.append(dict(size=[round(w),round(h)],kernel=kernel))
    for level,step in ((1,[1/base[0],0]),(2,[0,1/base[1]])):
        if not np.allclose(_floats(_buffer(stages[level],'ps-cb0',2784),2768,2),step,rtol=1e-6,atol=1e-8):
            raise ValueError("Unsupported base blur step")
    return native_bloom.validate(dict(schema="zzz-toon-native-bloom/1",enabled=True,source=str(folder),
        scope="Selected frame Bloom draws " + ", ".join(str(n) for n,_,_ in selected),
        resolution_policy="normalized-height",output_alpha="native",reference_resolution=ref,
        base_size=base,compose_size=size,threshold=packed[0],prefilter_gain=packed[1],intensity=intensity,
        weights=weights,layers=layers,intermediate_storage="r11g11b10_truncate",atlas=dict(size=atlas,rows_from_v0=rows)))


def _deferred(files):
    cb = _buffer(files,"ps-cb0",2960)
    width = _floats(cb,2892,1)[0]
    if _floats(cb,2944,1)[0] != 1:
        return width, None
    a,b,c,exposure = _floats(cb,2928,4)
    size = int(c)+1
    if not 2 <= size <= 128 or (a,b,c)!=(1/size**2,1/size,float(size-1)) or exposure<=0:
        raise ValueError("Unsupported character LUT dimensions or exposure")
    path = files["ps-t5.dds"]
    data = path.read_bytes()
    if (data[:4]!=b"DDS " or struct.unpack_from("<II",data,12)!=(size,size**2)
            or struct.unpack_from("<I",data,84)[0]!=113 or len(data)!=128+size**3*8
            or not np.isfinite(np.frombuffer(data,"<f2",offset=128)).all()):
        raise ValueError("Expected a linear RGBA16F character LUT")
    return width, dict(schema="zzz-toon-character-lut/1", enabled=True, size=size, exposure=exposure,
                       layout="linear-strip-dds-row0-at-uv0", texture=str(path), source_format="dds-rgba16f",
                       source=hashlib.sha256(data).hexdigest(), scope="Selected outfit frame PS26")


def read_frame(folder, objects, registration=None, *, eye_draws=None, face_draws=None, include_overlay=True, shared_draws=None, slot_evidence=None):
    if not folder.is_dir():
        raise ValueError(f"FrameAnalysis directory missing: {folder}")
    draws, deferred, bloom, features, dispatches = {}, {}, {}, {}, {}
    from .capture_face import FACE, CORE_FACE_PROGRAMS, CASTER
    for p in folder.iterdir():
        dispatch = capture_space.ENTITY_DISPATCH.fullmatch(p.name)
        if dispatch:
            dispatches[int(dispatch[1])] = p
            continue
        m = _FILE.fullmatch(p.name)
        if not m:
            continue
        event, slot, vs, ps, ext = m.groups()
        if face_draws is not None:
            if (vs, ps) in (FACE, CASTER) or (shared_draws is not None and (vs, ps) in CORE_FACE_PROGRAMS):
                _, face_files = face_draws.setdefault(event, ((vs, ps), {}))
                key = slot + '.' + ext
                if key in face_files:
                    raise ValueError(f"Ambiguous face resource {event} {key}")
                face_files[key] = p
        if ps == DEFERRED_PS:
            bucket = deferred
        elif ps == BODY_PS and vs == BODY_VS:
            bucket = draws
        elif (vs,ps) in capture_space.FEATURE_PASSES:
            _, files = features.setdefault(event, ((vs,ps), {}))
            files[slot+"."+ext] = p
            continue
        elif (vs,ps) in _BLOOM or (vs,ps) == _UBER:
            _, files = bloom.setdefault(int(event), ((vs,ps), {}))
            files[slot+"."+ext] = p
            continue
        else:
            continue
        files = bucket.setdefault(event,{})
        key = slot+"."+ext
        if key in files:
            raise ValueError(f"Ambiguous capture resource {event} {key}")
        files[key] = p
    names = {}
    meshes = {o.name:o.data for o in objects if o.type == "MESH"}
    for obj in objects:
        if obj.type == "MESH" and not obj.get("zzz_outline_helper"):
            names.setdefault(_mesh_key(obj.data),[]).append(obj.name)
    names.pop(None,None)
    matched, results, notes, overlays = {}, [], [], {}
    for event, files in sorted(draws.items()):
        try:
            key = _draw_key(files)
            if key not in names or len(names[key])!=1:
                continue
            results.append(_body_inputs(files))
            matched[event] = names[key][0]
            if slot_evidence is not None:
                # _draw_key validated every triangle and UV in source polygon order.
                mesh_name = matched[event]
                triangles = _draw_triangles(files)[2]
                polys = meshes[mesh_name].polygons
                indices = {polys[i].material_index for i in range(triangles.start, triangles.stop)}
                if len(indices) == 1:
                    slot = indices.pop()
                    slot_evidence.append(dict(draw=event, mesh=mesh_name, slot=slot,
                                              feature='PS1119', state='matched', ps=BODY_PS))
                    enabled = _floats(_buffer(files, 'ps-cb3', 2048), 1696, 1)[0] > .5
                    if not enabled or 'ps-t2.dds' in files:
                        slot_evidence.append(dict(draw=event, mesh=mesh_name, slot=slot,
                                                  feature='_UseOverlayTex', state='enabled' if enabled else 'inactive', ps=BODY_PS))
            if include_overlay and _floats(_buffer(files,'ps-cb3',2048),1696,1)[0] > .5:
                path = files['ps-t2.dds']
                data = path.read_bytes()
                overlay_pixels(data)
                overlays[hashlib.sha256(data).hexdigest()] = str(path)
        except (KeyError, ValueError, IndexError, struct.error, OSError) as exc:
            notes.append(f"Draw {event} unavailable: {exc}")
    overrides = {}
    if not results or any(r!=results[0] for r in results[1:]):
        notes.append("No unambiguous matching PS1119 lighting; check the outfit and capture files")
        return overrides, matched, notes
    if shared_draws is not None:
        shared_draws.update(features)
    if eye_draws is not None:
        eye_draws.update({n: files for n, (pair, files) in features.items()
                          if capture_space.FEATURE_PASSES[pair] == 'eye'})
    light, g, ambient = results[0]
    overrides["native_globals"] = dict(g)
    overrides["native_character_ambient"] = ambient
    try:
        feature_matches = capture_space.match_features(features, objects, _draw_triangles)
        overrides['capture_features'] = feature_matches
        environment = capture_space.environment(folder, objects, matched, draws, features, feature_matches,
                                                 dispatches, registration)
        overrides['native_environment'] = environment
    except (ValueError, OSError, KeyError, IndexError, struct.error) as exc:
        notes.append(f"Spatial environment unavailable: {exc}")
    if len(overlays)==1:
        digest,path = next(iter(overlays.items()))
        overrides['capture_overlay'] = dict(path=path,sha256=digest)
    elif overlays:
        notes.append("Matched draws use different character overlay textures")
    if light:
        overrides["native_light_profile"] = light_profile(*light, folder, "Matched outfit draw "+", ".join(matched))
    # Refuse to attribute a deferred result to this entity in multi-pass captures.
    if len(deferred)==1:
        try:
            width,lut = _deferred(next(iter(deferred.values())))
            overrides["native_globals"]["rim_width"] = width
            if lut:
                overrides["native_character_lut"] = lut
        except (KeyError, ValueError, OSError, struct.error) as exc:
            notes.append(f"Character LUT unavailable: {exc}")
        try:
            overrides["native_bloom"] = _bloom_inputs(bloom, folder, int(next(iter(deferred))))
        except (KeyError, ValueError, OSError, struct.error) as exc:
            notes.append(f"Captured Bloom unavailable: {exc}")
    else:
        notes.append("No unique PS26 deferred character pass")
    return overrides, matched, notes
