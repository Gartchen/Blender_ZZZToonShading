"""File inputs for the three animated wardrobe presets."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

import bpy
from bpy.props import StringProperty

from . import preview, wardrobe_language

DATA = Path(__file__).with_name('wardrobe_data')
ROLES = ('Remielle', 'Velina', 'Vivian')
PARTS = ('脸部与眼睛', '头发', '服装', '武器与羽翼', 'MatCap', '采样图集', '颜色与细纹', '滚动背景', '几何辅助数据')
SOURCE_NAMES = json.loads((DATA/'slots.json').read_text(encoding='utf8'))
CHANNEL = {'D':'底色', 'N':'法线与光照', 'M':'材质遮罩', 'A':'辅助通道'}


@dataclass(frozen=True)
class Slot:
    prop: str
    name: str
    title: str
    group: str
    default: Path | None = None
    fingerprint: str = ''
    usage: str = ''


def texture_label(name):
    stem = Path(name).stem
    lower = stem.lower()
    if 'matcap' in lower:
        return 'MatCap', stem.split('_', 2)[-1]
    if 'lightmap' in lower:
        return PARTS[0], '面部光照图'
    if stem.startswith('Eye_'):
        return PARTS[0], '眼睛颜色查找表'
    channel = CHANNEL[stem.rsplit('_', 1)[1]]
    if 'face' in lower:
        return PARTS[0], '脸部 · '+channel
    if 'hair' in lower:
        return PARTS[1], '头发 · '+channel
    if 'body' in lower:
        number = re.search(r'(\d+)_[DNMA]$', stem).group(1)
        return PARTS[2], '服装 '+number+' · '+channel
    return PARTS[3], ('羽翼' if 'wing' in lower else '武器')+' · '+channel


def geometry_files(cfg):
    prep,inputs=cfg['prepare'],cfg['inputs']
    outline=prep.get('rest_outline') or inputs['native_outline']
    for table,label in [(prep.get('replace_mesh',{}),'原始网格'),
            (outline.get('mesh_inputs',{}),'描边网格'),
            (inputs.get('native_surface',{}).get('mesh_inputs',{}),'表面顶点数据'),
            (inputs.get('native_face',{}).get('mesh_inputs',{}),'脸部顶点数据'),
            (inputs.get('native_face_layers',{}).get('eye_inputs',{}),'眼部顶点数据')]:
        for obj,entry in table.items():
            yield entry,'path',obj+' · '+label
            if label=='表面顶点数据' and entry.get('source'):
                yield entry,'source',obj+' · 辅助 UV 数据'
    receiver=inputs.get('native_face',{}).get('receiver',{})
    if receiver.get('red_path'):
        yield receiver,'red_path','面部投影遮挡数据'


def geometry_key(path):
    return 'geo_'+Path(path).name.lower().replace('.','_')


@lru_cache(maxsize=3)
def slots(role):
    index = json.loads((preview.DATA/'index.json').read_text(encoding='utf8'))['outfits'][role]
    cfg = json.loads((preview.DATA/role/'preview.json').read_text(encoding='utf8'))
    inputs = cfg['inputs']
    def packed(ref):
        assert ref.startswith('@')
        return preview.DATA/ref[1:]
    known = cfg['prepare'].get('texture')
    replacement = packed(known) if known else None
    result = []
    for name in SOURCE_NAMES[role]:
        default = replacement if replacement and replacement.name == name else None
        group, label = texture_label(name)
        usage=SOURCE_NAMES['matcaps'][role].get(name,{})
        if usage:
            label='MatCap '+label+' · '+usage['parts']
        digest = preview.digest(default) if default and default.is_file() else index['textures'][name]
        result.append(Slot('tex_'+Path(name).stem.lower(), name, label, group, default if default and default.is_file() else None, digest,usage.get('slots','')))
    if index['uv']:
        result.append(Slot('uv','uv','辅助 UV 模型 FBX','模型',DATA/role/'uv.fbx'))
    result.append(Slot('lut','lut','角色调色 LUT','颜色与细纹',packed(inputs['native_character_lut']['texture'])))
    if inputs.get('character_overlay'):
        result.append(Slot('overlay','overlay','表面细纹','颜色与细纹',packed(inputs['character_overlay'])))
    atlases = {}
    for material in inputs['native_sampling']['materials'].values():
        for name, item in material.items():
            path = packed(item['atlas'])
            if name in atlases and atlases[name] != path:
                raise ValueError('Multiple wardrobe mip layouts for '+name)
            atlases[name] = path
    for name, path in atlases.items():
        prop = 'mip_'+Path(name).stem.lower()
        result.append(Slot(prop,prop,texture_label(name)[1], '采样图集', path))
    for key, title in [('large','大字图案'),('small','小字图案'),('screen_shade','背景明暗底图')]:
        result.append(Slot(key,key,title,'滚动背景',DATA/(key+'.png')))
    geometry={}
    for entry,key,title in geometry_files(cfg):
        path=packed(entry[key])
        prop=geometry_key(path)
        if prop in geometry and geometry[prop].default!=path:
            raise ValueError('Geometry slot collision: '+prop)
        geometry.setdefault(prop,Slot(prop,prop,title,'几何辅助数据',path))
    result.extend(geometry.values())
    face=DATA/role/'face_outline.npz'
    if face.with_suffix('.json').is_file():
        result.append(Slot('face_outline','face_outline','脸部轮廓顶点数据','几何辅助数据',face))
    return tuple(result)


def asset_type(role):
    fields = {key:StringProperty(name=name,subtype='FILE_PATH',description=hint)
              for key,name,hint in [('model','角色模型 FBX','所选服装的带蒙皮与顶点色模型'),
                                    ('clip','预览待机动画','Unity .anim 或已解析的 AnimationClip JSON'),
                                    ('background','整张滚动背景','可选择一张平铺图替代三层页面素材')]}
    for item in slots(role):
        hint = item.default.name if item.default else item.name
        if item.usage:
            hint+=' · '+item.usage
        fields[item.prop] = StringProperty(name=item.title, subtype='FILE_PATH',description=hint)
    return type('ZZZ_'+role+'Assets',(bpy.types.PropertyGroup,),{'__module__':__name__,'__annotations__':fields})


CLASSES = tuple(asset_type(role) for role in ROLES)


def selected(settings,role):
    return getattr(settings,'assets_'+role.lower())


def input_role(settings):
    return settings.wardrobe_selection if settings.is_property_set('wardrobe_selection') else settings.wardrobe_role


def legacy(settings):
    """Move the old directory-based fields into a single role, once per scene."""
    if settings.get('wardrobe_slots_migrated') or not any(
            getattr(settings,k) for k in ('wardrobe_model','wardrobe_idle','wardrobe_background','wardrobe_textures')):
        return
    role = settings.wardrobe_role
    if not settings.is_property_set('wardrobe_selection'):
        settings.wardrobe_selection=role
    a = selected(settings,role)
    for key,old in [('model','wardrobe_model'),('clip','wardrobe_idle'),('background','wardrobe_background')]:
        if not getattr(a,key) and getattr(settings,old):
            setattr(a,key,getattr(settings,old))
    if settings.wardrobe_textures:
        for item in slots(role):
            if item.prop.startswith('tex_') and not item.default and not getattr(a,item.prop):
                setattr(a,item.prop,str(Path(bpy.path.abspath(settings.wardrobe_textures))/item.name))
    settings['wardrobe_slots_migrated'] = True


def request(settings):
    legacy(settings)
    role = input_role(settings)
    a = selected(settings,role)
    missing = []
    def path(raw,title):
        target = Path(bpy.path.abspath(raw)).resolve() if raw else None
        if not target or not target.is_file():
            missing.append(title)
            return ''
        return str(target)
    base, extra = {}, {}
    model = path(a.model,'角色模型 FBX')
    clip = path(a.clip,'预览待机动画')
    background = path(a.background,'整张滚动背景') if a.background else None
    for item in slots(role):
        value = getattr(a,item.prop)
        if item.prop.startswith('tex_'):
            base[item.name] = path(value or str(item.default or ''),item.title+' ('+item.name+')')
        elif value and not (background and item.group=='滚动背景'):
            extra[item.prop] = path(value,item.title)
    if missing:
        raise ValueError(wardrobe_language.text(settings,'请填写有效文件：'+'、'.join(missing)))
    if Path(model).suffix.lower()!='.fbx':
        raise ValueError(wardrobe_language.text(settings,'角色模型槽需要 FBX 文件'))
    if Path(clip).suffix.lower() not in ('.anim','.json'):
        raise ValueError(wardrobe_language.text(settings,'预览待机动画槽需要 Unity .anim 或 AnimationClip JSON'))
    return dict(language=settings.wardrobe_language,role=role,model=model,clip=clip,background=background,textures=base,extras=extra)


def save_inputs(settings,req):
    role=req['role']
    settings.wardrobe_language=req.get('language','ZH')
    settings.wardrobe_role=settings.wardrobe_selection=role
    a = selected(settings,role)
    for key in ('model','clip','background'):
        setattr(a,key,req.get(key) or '')
    for item in slots(role):
        if item.prop.startswith('tex_'):
            value=req['textures'][item.name]
            if item.default and Path(value)==item.default:
                value=''
        else:
            value=req.get('extras',{}).get(item.prop,'')
        setattr(a,item.prop,value)
    settings['wardrobe_slots_migrated']=True


def character_matches(role,textures,extras):
    for item in slots(role):
        if item.prop.startswith('tex_'):
            if preview.digest(Path(textures[item.name]))!=item.fingerprint:
                return False
        elif item.group!='滚动背景' and item.prop in extras:
            path=Path(extras[item.prop])
            if path!=item.default and (not item.default.is_file() or preview.digest(path)!=preview.digest(item.default)):
                return False
    return True


def load_image(params,name,noncolor):
    cache=params.setdefault('_wardrobe_images',{})
    key=(name,noncolor)
    if key not in cache:
        path=params['wardrobe_texture_files'][name]
        image=bpy.data.images.load(path,check_existing=False)
        if min(image.size)<=0:
            raise ValueError('图片无法解码：'+name+' — '+path)
        image.name=name
        image['zzz_wardrobe_slot']=name
        image.colorspace_settings.name='Non-Color' if noncolor else 'sRGB'
        image.alpha_mode='CHANNEL_PACKED'
        cache[key]=image
    return cache[key]


def make_atlas(source):
    """Generate a packed linear chain for an edited wardrobe source image."""
    import numpy as np
    w,h=source.size
    if w!=h or w<2 or w&(w-1):
        raise ValueError('此槽使用偏置 Mip 采样，需要边长为 2 的幂的方形贴图：'+source['zzz_wardrobe_slot'])
    pixels=np.zeros((2*h,w,4),np.float32)
    # Blender exposes byte image pixels as normalized stored bytes, even for sRGB.
    source.pixels.foreach_get(pixels[:h].reshape(-1))
    if not source.is_float and source.colorspace_settings.name=='sRGB':
        rgb=pixels[:h,:,:3]
        rgb[:]=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    size=h
    while size>1:
        low=2*h-2*size
        half=size//2
        pixels[low+size:low+size+half,:half]=pixels[low:low+size,:size].reshape(
            half,2,half,2,4).mean(axis=(1,3))
        size=half
    image=bpy.data.images.new('Mip · '+source['zzz_wardrobe_slot'],w,2*h,alpha=True,float_buffer=True,is_data=True)
    image.file_format='OPEN_EXR'
    image.colorspace_settings.name='Non-Color'
    image.alpha_mode='CHANNEL_PACKED'
    image.use_half_precision=False
    image.pixels.foreach_set(pixels.reshape(-1))
    image.update()
    image.pack()
    return image


def configure_inputs(role,cfg,req):
    """Apply manual bindings before material construction, including biased texture sampling."""
    extra=req.get('extras',{})
    textures=req['textures']
    inputs=cfg['inputs']
    for entry,key,_ in geometry_files(cfg):
        prop=geometry_key(entry[key])
        if prop in extra:
            entry[key]=extra[prop]
    inputs['wardrobe_texture_files']=textures
    inputs['native_character_lut']['texture']=extra.get('lut',inputs['native_character_lut']['texture'])
    if 'overlay' in extra:
        inputs['character_overlay']=extra['overlay']
    expected={s.name:s.fingerprint for s in slots(role) if s.prop.startswith('tex_')}
    same={}
    for material in inputs['native_sampling']['materials'].values():
        for name, item in material.items():
            atlas=extra.get('mip_'+Path(name).stem.lower())
            if atlas:
                item['atlas']=atlas
            else:
                if name not in same:
                    same[name]=preview.digest(Path(textures[name]))==expected[name]
                if not same[name] or not Path(item['atlas']).is_file():
                    item['derive_mips']=True


def draw(layout,settings):
    tr=lambda value: wardrobe_language.text(settings,value)
    role=input_role(settings)
    assets=selected(settings,role)
    layout.prop(assets,'model',translate=False,text=tr('模型 FBX'))
    for item in slots(role):
        if item.prop=='uv':
            layout.prop(assets,'uv',translate=False,text=tr('辅助 UV'),placeholder=tr('选择文件'))
    layout.prop(assets,'clip',translate=False,text=tr('待机动画'))
    for group in PARTS:
        items=[s for s in slots(role) if s.group==group]
        if not items:
            continue
        header, body=layout.panel('zzz_wardrobe_'+role+str(PARTS.index(group)),default_closed=True)
        required=[s for s in items if not s.default]
        count=sum(bool(getattr(assets,s.prop)) for s in required)
        header.label(translate=False,text=f'{tr(group)}  {count}/{len(required)}' if required else tr(group))
        if body is None:
            continue
        if group=='滚动背景':
            body.prop(assets,'background',translate=False,text=tr('整张滚动背景'),placeholder=tr('使用下方三层页面素材'))
            if assets.background:
                continue
        for item in items:
            row=body.column(align=True)
            row.label(translate=False,text=tr(item.title)+'  ·  '+(item.default.name if item.default else item.name))
            placeholder='自动匹配或生成' if group in ('采样图集','滚动背景') or item.prop=='lut' else ('使用内置文件' if item.default and item.default.is_file() else '选择文件')
            row.prop(assets,item.prop,text='',placeholder=tr(placeholder))
