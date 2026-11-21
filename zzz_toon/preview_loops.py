"""Certificates for shipping an already rendered first view of an exact outfit/idle."""
import json
from pathlib import Path
import zipfile

import bpy
from . import preview,clip_io,wardrobe_slots

DATA=Path(__file__).with_name('fullscreen_data')


def _receipts():
    return Path(bpy.utils.user_resource('DATAFILES',path='ZZZ Outfit Previews/loop-keys',create=True))


def _metadata(role):
    index=DATA/'index.json'
    if not index.is_file():
        return None
    return json.loads(index.read_text(encoding='utf8')).get(role)


def _matches_motion(scene,expected):
    version=expected.get('dynamics_revision')
    if version is None:
        return True
    data=json.loads(scene.get('zzz_wardrobe_dynamics','{}'))
    return data.get('revision')==version and data.get('clip_calibration') is True


def _matches_profile(scene,expected):
    revision=expected.get('render_revision')
    return (revision is None or scene.get('zzz_wardrobe_render_revision')==revision) and _matches_motion(scene,expected)


def certify(scene,request,blend):
    """Record only pristine worker outputs matching the shipped reference inputs."""
    role=request['role']
    expected=_metadata(role)
    if not expected or not _matches_profile(scene,expected):
        return
    source=Path(request['clip'])
    if preview.digest(source)!=expected['clip']:
        if clip_io.curve_signature(clip_io.load(source))!=expected['curves']:
            return
    spec=preview._load(preview.DATA/'index.json')['outfits'][role]
    if preview.digest(Path(request['model']))!=spec['fbx']:
        return
    if isinstance(request['textures'],dict) and not wardrobe_slots.character_matches(
            role,request['textures'],request.get('extras',{})):
        return
    signature=preview.digest(blend)
    (_receipts()/(signature+'.json')).write_text(json.dumps({'role':role,'profile':expected['scene']}))


def verified_initial(scene):
    """An edited/unknown scene always uses the live camera and its own loop."""
    if not bpy.data.filepath or bpy.data.is_dirty:
        return None
    role=scene.zzz_toon.wardrobe_role
    expected=_metadata(role)
    if not expected or not _matches_profile(scene,expected):
        return None
    try:
        signature=preview.digest(Path(bpy.data.filepath))
        if signature!=expected['scene']:
            file=_receipts()/(signature+'.json')
            if not file.is_file() or json.loads(file.read_text())!={'role':role,'profile':expected['scene']}:
                return None
        spec=preview._load(preview.DATA/'index.json')['outfits'][role]
        settings=scene.zzz_toon
        if settings.get('wardrobe_slots_migrated'):
            assets=wardrobe_slots.selected(settings,role)
            textures,extra={},{}
            for slot in wardrobe_slots.slots(role):
                value=getattr(assets,slot.prop)
                if slot.prop.startswith('tex_'):
                    textures[slot.name]=bpy.path.abspath(value) if value else str(slot.default or '')
                elif value:
                    extra[slot.prop]=bpy.path.abspath(value)
            if not wardrobe_slots.character_matches(role,textures,extra):
                return None
        else:
            tex=Path(bpy.path.abspath(settings.wardrobe_textures))
            if any(preview.digest(tex/name)!=sha for name,sha in spec['textures'].items()):
                return None
        return DATA/(role+'.loop')
    except (OSError,ValueError):
        return None


class RecordedLoop:
    def __init__(self,path,frames):
        self.archive=zipfile.ZipFile(path)
        try:
            info=json.loads(self.archive.read('info.json'))
            self.decoder=None
            if info.get('codec')=='zstd':
                try:
                    import zstandard
                except ImportError as error:
                    raise ValueError('Zstandard decoder unavailable') from error
                self.decoder=zstandard.ZstdDecompressor()
            elif info.get('codec') is not None:
                raise ValueError('Unsupported preview compression')
            self.canvas=tuple(info['canvas'])
            self.crop=tuple(info['crop'])
            x,y,self.width,self.height=self.crop
            w,h=self.canvas
            if (info['format']!='character-srgba8/1' or frames!=info['frames'] or
                    not (0<=x<x+self.width<=w<=8192 and 0<=y<y+self.height<=h<=8192)):
                raise ValueError('Different loop format')
        except Exception:
            self.archive.close()
            raise

    def read(self,i):
        import numpy as np
        data=self.archive.read(str(i))
        if self.decoder:
            try:
                data=self.decoder.decompress(data,max_output_size=self.height*self.width*4)
            except Exception as error:
                raise ValueError('Invalid preview frame') from error
        return np.frombuffer(data,dtype=np.uint8).reshape(self.height,self.width,4)

    def close(self):
        self.archive.close()
