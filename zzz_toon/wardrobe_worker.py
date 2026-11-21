"""Background Blender entry for generating an independent, editable scene."""
import importlib
import json
from pathlib import Path
import sys

import bpy


def main():
    p=Path(sys.argv[-1])
    args=json.loads(p.read_text(encoding='utf8'))
    dest=Path(args['output'])
    staging=dest.with_name('.'+dest.stem+'.blend')
    try:
        package=Path(__file__).resolve().parent
        sys.path.insert(0,str(package.parent))
        addon=importlib.import_module(package.name)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon.register()
        scene=bpy.context.scene
        addon.wardrobe.build(scene,args['request'])
        scene.render.image_settings.media_type='VIDEO'
        scene.render.image_settings.file_format='FFMPEG'
        scene.render.ffmpeg.format='MPEG4'
        scene.render.ffmpeg.codec='H264'
        scene.render.ffmpeg.constant_rate_factor='HIGH'
        scene.render.filepath='//'+dest.stem+'.mp4'
        # FBX import uses a temporary copy for some embedded images. Actual material
        # inputs reference the supplied texture folder or the installed outfit pack.
        bpy.context.preferences.filepaths.save_version=0
        if bpy.ops.wm.save_as_mainfile(filepath=str(staging),compress=True)!={'FINISHED'}:
            raise ValueError('Failed to save preview scene')
        staging.replace(dest)
        # Prewarmed playback is optional, including on read-only installations.
        try:
            addon.fullscreen.preview_loops.certify(scene,args['request'],dest)
        except (OSError,ValueError) as error:
            print('Using live idle playback:',error)
    except Exception as err:
        p.with_suffix('.error').write_text(str(err),encoding='utf8')
        raise
    finally:
        staging.unlink(missing_ok=True)


if __name__=='__main__':
    main()

