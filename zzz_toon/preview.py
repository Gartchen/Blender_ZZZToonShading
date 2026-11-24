"""Portable outfit-preview defaults and asset-bound preparation for verified exports."""
import hashlib
import json
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import native_mesh

DATA=Path(__file__).with_name('preview_data')
RIG_KEY='zzz_builtin_preview'


def _load(path, require_files=True):
    def expand(value):
        if isinstance(value,dict):
            return {k:expand(v) for k,v in value.items()}
        if isinstance(value,list):
            return [expand(v) for v in value]
        if isinstance(value,str) and value.startswith('@'):
            target=(DATA/value[1:]).resolve()
            if not target.is_relative_to(DATA.resolve()) or (require_files and not target.is_file()):
                raise ValueError('Missing packaged preview input: '+value)
            return str(target)
        return value
    return expand(json.loads(path.read_text(encoding='utf8')))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def select(fbx,uv,mats,textures,frame,use_outfit=True):
    index=_load(DATA/'index.json', require_files=False)
    lut = index['common'].get('native_character_lut')
    if lut and not Path(lut['texture']).is_file():
        index['common'].pop('native_character_lut')
    result=dict(common=index['common'],outfit=None,status='Shared outfit-preview defaults')
    if not use_outfit or not fbx or not fbx.is_file():
        return result
    fingerprint=digest(fbx)
    for role,item in index['outfits'].items():
        if fingerprint!=item['fbx']:
            continue
        if ((digest(uv) if uv and uv.is_file() else None)!=item['uv'] or
                any(not folder or not (folder/name).is_file() or digest(folder/name)!=sha
                    for field,folder in (('materials',mats),('textures',textures))
                    for name,sha in item[field].items())):
            result['status']='Export differs from the verified outfit; using shared preview defaults'
            return result
        if frame and (not (frame/'log.txt').is_file() or digest(frame/'log.txt')!=item['capture_log']):
            result['status']='Different preview capture; using the selected capture and shared defaults'
            return result
        # The public data describes animated outfits. Static registered poses
        # require the original private mesh streams.
        result['status'] = 'Shared preview defaults. Build an animated scene in the ZZZ Toon panel.'

        return result
    return result


def camera(scene, config=None, meshes=()):
    if config:
        cam=bpy.data.objects.new('ZZZ Outfit Preview',bpy.data.cameras.new('Outfit preview projection'))
        scene.collection.objects.link(cam)
        scene.camera=cam
        cam.matrix_world=Matrix(config['camera']['world'])
        for key,value in config['camera']['optics'].items():
            setattr(cam.data,key,value)
        for key,value in config['render'].items():
            setattr(scene.render,key,value)
        scene.render.resolution_percentage=100
        scene.eevee.taa_render_samples=config['samples']
    elif not scene.camera or scene.camera.data.type!='PERSP':
        points=[o.matrix_world@Vector(p) for o in meshes if not o.hide_render for p in o.bound_box]
        if not points:
            return
        lo,hi=np.min(points,axis=0),np.max(points,axis=0)
        cam=bpy.data.objects.new('ZZZ Outfit Preview',bpy.data.cameras.new('Outfit preview framing'))
        scene.collection.objects.link(cam)
        scene.camera=cam
        center=(lo+hi)/2
        aspect=scene.render.resolution_x*scene.render.pixel_aspect_x/(scene.render.resolution_y*scene.render.pixel_aspect_y)
        cam.data.sensor_fit='HORIZONTAL'
        dist=max(hi[0]-lo[0],(hi[2]-lo[2])*aspect,.1)*1.1*cam.data.lens/cam.data.sensor_width
        cam.location=(center[0],lo[1]-dist,center[2])
        cam.rotation_euler=(np.pi/2,0,0)
    scene.render.use_border=False
    scene.render.film_transparent=True


def _capture_mesh(obj,path):
    mesh=obj.data
    with np.load(path) as data:
        loops=np.empty(len(mesh.loops),'i')
        mesh.loops.foreach_get('vertex_index',loops)
        if not np.array_equal(loops,data['loops']) or len(mesh.vertices)!=len(data['position']):
            raise ValueError('Preview triangle/UV mismatch: '+obj.name)
        if 'uv' in data:
            uv=np.empty(2*len(loops),'f')
            mesh.uv_layers['UV0'].data.foreach_get('uv',uv)
            if not np.array_equal(uv.reshape(-1,2),data['uv']):
                raise ValueError('Preview UV mismatch: '+obj.name)
        # Keep a real rest-mesh datablock and the armature for subsequent editing.
        obj['zzz_preview_rest_mesh']=mesh
        obj.data=mesh.copy()
        if obj.data.shape_keys:
            obj.shape_key_clear()
        for mod in list(obj.modifiers):
            if mod.type=='ARMATURE':
                obj.modifiers.remove(mod)
        obj.data.vertices.foreach_set('co',data['position'].ravel())
        obj.data.update()
        obj.data.normals_split_custom_set(data['normal'].tolist())


def prepare(scene,objects,choice,reference_pose,fresh):
    cfg=choice['config']
    stage,prep=cfg['stage'],cfg['prepare']
    rig,=[o for o in objects if o.type=='ARMATURE']
    by={o.name:o for o in objects if o.type=='MESH'}
    if set(stage['objects'])-by.keys():
        raise ValueError('Preview needs the original named outfit mesh set in this scene')
    meshes=[by[name] for name in stage['objects']]
    if fresh:
        for name,item in prep.get('replace_mesh',{}).items():
            native_mesh.replace(by[name],item['path'],[-1,1,1],bind_tolerance=item['tolerance'])
        eye=prep.get('eye_slot')
        if eye:
            polygons=by[eye['object']].data.polygons
            for index in eye['polygons']:
                polygons[index].material_index=eye['index']
        for name,obj in by.items():
            obj.hide_render=obj.hide_viewport=name not in stage['objects']
        camera(scene,stage if reference_pose else None,meshes)
        if reference_pose:
            rig.matrix_world=Matrix(stage['rig_world'])
            for name,basis in stage['pose'].items():
                rig.pose.bones[name].matrix_basis=Matrix(basis)
            bpy.context.view_layer.update()
            for obj in meshes:
                row=stage['objects'][obj.name]
                obj.matrix_world=Matrix(row['world'])
                if 'geometry' in row:
                    _capture_mesh(obj,row['geometry'])
                if obj.data.shape_keys:
                    for name,value in row['shape'].items():
                        obj.data.shape_keys.key_blocks[name].value=value
        rig[RIG_KEY]=choice['outfit']
        rig['zzz_preview_reference_pose']=reference_pose
        bpy.context.view_layer.update()
    ov=dict(cfg['inputs'])
    if prep.get('rest_outline') and not rig.get('zzz_preview_reference_pose'):
        ov['native_outline']=prep['rest_outline']
    if 'texture' in prep:
        ov['preview_texture_dir']=str(Path(prep['texture']).parent)
    sampling=set(ov.get('native_sampling',{}).get('meshes',[]))
    meshes.extend(obj for name,obj in by.items() if name in sampling and name not in stage['objects'])
    return [rig,*meshes],ov


def _light_name(obj):
    return 'ZZZ_HairShadowLight' if obj.name.startswith('ZZZ_HairShadowLight_') else obj.name


def light_state(scene):
    return {_light_name(obj):dict(world=obj.matrix_world.copy(),energy=obj.data.energy,color=list(obj.data.color))
            for obj in scene.objects if obj.type=='LIGHT' and obj.name.startswith('ZZZ_')}


def finish(scene,objects,stage,lights,explicit_key):
    by={o.name:o for o in objects}
    for name,row in stage['objects'].items():
        obj=by[name]
        if 'geometry' in row and obj.get('zzz_preview_rest_mesh'):
            # The reference already contains post-skin positions and normals.
            for m in list(obj.modifiers):
                if m.name=='ZZZ_SkinSmooth' and m.name not in row['modifiers']:
                    obj.modifiers.remove(m)
            for index,name_ in enumerate(row['modifiers']):
                old=obj.modifiers.find(name_)
                if old>=0:
                    obj.modifiers.move(old,index)
    if lights:
        for obj in scene.objects:
            if obj.type!='LIGHT':
                continue
            name=_light_name(obj)
            row=lights.get(name)
            if name=='ZZZ_KeyLight' and explicit_key:
                continue
            if row:
                obj.matrix_world=Matrix(row['world'])
                obj.data.color=row['color']
                obj.data.energy=row['energy']
    bpy.context.view_layer.update()
