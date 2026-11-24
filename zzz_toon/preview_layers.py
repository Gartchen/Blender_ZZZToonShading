"""Render the wardrobe's secondary EEVEE layers for the interactive compositor."""
import math
from contextlib import ExitStack

import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector

from . import feature_layers, transparent_layers

QUALITY_ROLES = {'Remielle', 'Velina'}


def _material_corners(mesh, slot_ids):
    """Bound the selected material faces on the currently evaluated mesh."""
    materials = np.empty(len(mesh.polygons), np.int32)
    corners = np.empty(len(mesh.loops), np.int32)
    # Blender can bulk-copy these integer attributes. The general RNA
    # polygon/corner properties may dispatch an accessor for each element.
    face_slots = mesh.attributes.get('material_index')
    if face_slots and face_slots.domain == 'FACE' and face_slots.data_type == 'INT':
        face_slots.data.foreach_get('value', materials)
    else:
        mesh.polygons.foreach_get('material_index', materials)
    corner_verts = mesh.attributes.get('.corner_vert')
    if corner_verts and corner_verts.domain == 'CORNER' and corner_verts.data_type == 'INT':
        corner_verts.data.foreach_get('value', corners)
    else:
        mesh.loops.foreach_get('vertex_index', corners)
    selected = np.isin(materials, slot_ids)
    if len(corners) == 3 * len(materials):
        # Every valid mesh face has at least three corners.
        vertices = corners.reshape(-1, 3)[selected].ravel()
    else:
        lengths = np.empty_like(materials)
        mesh.polygons.foreach_get('loop_total', lengths)
        vertices = corners[np.repeat(selected, lengths)]
    if not len(vertices):
        return ()
    coords = np.empty((len(mesh.vertices), 3), np.float32)
    mesh.vertices.foreach_get('co', coords.ravel())
    # Shared vertices contribute only once to the same min/max. Keep the
    # three coordinate arrays contiguous during the reductions.
    used = np.zeros(len(coords), dtype=bool)
    used[vertices] = True
    axes = coords[used].T.copy()
    lo, hi = axes.min(axis=1), axes.max(axis=1)
    return [(x, y, z) for x in (lo[0], hi[0])
                      for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


class LayerInput:
    def __init__(self, scene, tree, layer, source, coordinates, objects, density, restore):
        self.scene = scene
        self.tree = tree
        self.layer = layer
        self.objects = objects
        self.density = density
        self.target = None
        self.moving_size = None
        self.image = bpy.data.images.new('ZZZ Preview ' + layer.name, width=2, height=2,
                                         alpha=True, float_buffer=True)
        restore.callback(bpy.data.images.remove, self.image)
        self.image.alpha_mode = 'PREMUL'
        self.image.colorspace_settings.name = 'Non-Color'

        def node(kind):
            value = tree.nodes.new(kind)
            restore.callback(tree.nodes.remove, value)
            return value

        texture = node('CompositorNodeImage')
        texture.image = self.image
        self.offset = node('ShaderNodeVectorMath')
        self.offset.operation = 'SUBTRACT'
        tree.links.new(coordinates, self.offset.inputs[0])
        self.scale = node('ShaderNodeVectorMath')
        self.scale.operation = 'MULTIPLY'
        tree.links.new(self.offset.outputs[0], self.scale.inputs[0])
        mapped = node('CompositorNodeMapUV')
        self.mapped=mapped
        mapped.inputs['Extension X'].default_value = 'Clip'
        mapped.inputs['Extension Y'].default_value = 'Clip'
        tree.links.new(texture.outputs['Image'], mapped.inputs['Image'])
        tree.links.new(self.scale.outputs[0], mapped.inputs['UV'])
        separate = node('CompositorNodeSeparateColor')
        self.channels=separate
        tree.links.new(mapped.outputs[0], separate.inputs[0])
        for name, output in [('Image', mapped.outputs[0]), ('Alpha', separate.outputs['Alpha'])]:
            for link in list(source.outputs[name].links):
                restore.callback(tree.links.new, source.outputs[name], link.to_socket)
                tree.links.new(output, link.to_socket)
        self.rectangle = None
        self.mapped_rectangle = None
        restore.callback(self.close)

    def bounds(self, depsgraph, projection, view, w, h):
        # The colour passes cover only the projected actor, but other objects
        # remain in the draw and continue to occlude eyes and accessories.
        points = []
        for obj,slot_ids in self.objects:
            evaluated = obj.evaluated_get(depsgraph)
            matrix = projection @ view @ evaluated.matrix_world
            mesh=evaluated.data
            if not len(mesh.polygons):
                continue
            if slot_ids is None or len(slot_ids)==len(mesh.materials):
                corners=evaluated.bound_box
            else:
                # Evaluate the same deformed polygons as the pass. An object
                # can contain an entire costume with only a small sheer trim.
                corners = _material_corners(mesh, slot_ids)
            for corner in corners:
                xyzw = matrix @ Vector((*corner, 1.))
                if xyzw.w <= 0.001:
                    return 0, 0, w, h
                points.append(((.5 + .5*xyzw.x/xyzw.w)*w, (.5 + .5*xyzw.y/xyzw.w)*h))
        if not points:
            return 0, 0, w, h
        xs, ys = zip(*points)
        # Align to blocks to avoid reallocating the image on each idle pose.
        def interval(lo, hi, extent):
            lo = max(0, min(extent-1, math.floor((lo-4)/32)*32))
            hi = max(lo+1, min(extent, math.ceil((hi+4)/32)*32))
            return lo, hi-lo
        x, width = interval(min(xs), max(xs), w)
        y, height = interval(min(ys), max(ys), h)
        return x,y,width,height

    def render(self, ctx, space, view, projection, w, h, moving):
        rect = self.bounds(ctx.evaluated_depsgraph_get(), projection, view, w, h)
        x,y,cw,ch = rect
        if moving:
            # Retain the EEVEE viewport as the projected silhouette changes size.
            # Pad the camera crop by whole texels; the sampling density stays fixed.
            def pad(start, length, extent, previous):
                size = min(extent, max(math.ceil(length / 128) * 128, previous))
                return max(0, min(start - (size-length)//2, extent-size)), size
            prev_w,prev_h = self.moving_size or (0,0)
            x,cw = pad(x,cw,w,prev_w)
            y,ch = pad(y,ch,h,prev_h)
            self.moving_size=(cw,ch)
            rect=(x,y,cw,ch)
        else:
            self.moving_size=None
        iw,ih = max(2,round(cw*self.density)),max(2,round(ch*self.density))
        if self.target is None or (self.target.width,self.target.height)!=(iw,ih):
            self.close()
            self.target = gpu.types.GPUOffScreen(iw, ih, format='RGBA16F')
        self.rectangle=(rect,w,h)
        crop = Matrix.Identity(4)
        crop[0][0],crop[1][1] = w/cw,h/ch
        crop[0][3],crop[1][3] = (w-2*x-cw)/cw,(h-2*y-ch)/ch
        with ctx.temp_override(view_layer=self.layer):
            self.layer.update()
            ctx.evaluated_depsgraph_get()
            self.target.draw_view3d(self.scene,self.layer,space,ctx.region,view,crop@projection,
                                    do_color_management=False,draw_background=False)

    def publish(self):
        iw,ih=self.target.width,self.target.height
        if tuple(self.image.size)!=(iw,ih):
            self.image.scale(iw,ih)
        if self.mapped_rectangle!=self.rectangle:
            (x,y,cw,ch),w,h=self.rectangle
            self.offset.inputs[1].default_value=(x/w,y/h,-1.)
            self.scale.inputs[1].default_value=(w/cw,h/ch,1.)
            self.mapped_rectangle=self.rectangle
        pixels = np.asarray(self.target.texture_color.read(),dtype=np.float32).ravel()
        self.image.pixels.foreach_set(pixels)
        self.image.update()
        self.image.update_tag()

    def close(self):
        if self.target:
            self.target.free()
            self.target = None


class CharacterLayers:
    def __init__(self, owner):
        self.restore = ExitStack()
        owner.restore.callback(self.restore.close)
        self.aux_space = None
        tree = owner.scene.compositing_node_group
        layers = {n.layer:n for n in tree.nodes if n.type=='R_LAYERS'}
        main = layers.get(owner.win.view_layer.name)
        self.inputs = []
        self.cloth = None
        self.eye=None
        self.eye_transfer=None
        self.eye_restore=ExitStack()
        if not main or owner.scene.zzz_toon.wardrobe_role not in QUALITY_ROLES:
            return
        coord = tree.nodes.new('CompositorNodeImageCoordinates')
        self.restore.callback(tree.nodes.remove,coord)
        tree.links.new(main.outputs['Image'],coord.inputs['Image'])
        meshes = [o for o in owner.scene.objects if o.type=='MESH' and not o.hide_render]
        for name, density in [(feature_layers.LAYER,1.), (transparent_layers.LAYER,.5)]:
            node = layers.get(name)
            layer = owner.scene.view_layers.get(name)
            if not node or not layer or not layer.use:
                continue
            def emits(material):
                if not material or not material.node_tree:
                    return False
                nodes=material.node_tree.nodes
                if name==feature_layers.LAYER:
                    return any(n.type=='OUTPUT_AOV' and n.aov_name in ('zzz_eyes','zzz_brows') for n in nodes)
                wrapper=nodes.get(transparent_layers.WRAPPER)
                return wrapper and wrapper.get('original_input')==2
            bounds=[]
            for obj in meshes:
                slots=[i for i,slot in enumerate(obj.material_slots) if emits(slot.material)]
                if slots:
                    bounds.append((obj,slots if owner.initial_loop else None))
            self.inputs.append(LayerInput(owner.scene,tree,layer,node,coord.outputs['Normalized'],bounds,density,self.restore))
            if name==transparent_layers.LAYER:
                self.cloth=self.inputs[-1]
            else:
                self.eye=self.inputs[-1]
        self.tree = tree
        visible={w.screen for w in bpy.context.window_manager.windows}
        self.aux_space=next((a.spaces.active for screen in bpy.data.screens if screen not in visible
                             for a in screen.areas if a.type=='VIEW_3D' and a.spaces.active!=owner.playback.source),None)
        if self.aux_space:
            owner.keep(self.aux_space.shading,'type','RENDERED')
            owner.keep(self.aux_space.shading,'use_compositor','DISABLED')
            owner.keep(self.aux_space.overlay,'show_overlays',False)
            owner.keep(self.aux_space,'show_object_viewport_mesh',True)
            owner.keep(self.aux_space.region_3d,'view_perspective','CAMERA')
        if owner.initial_loop and self.eye:
            from .preview_features import EyeTransfer
            materials={slot.material for obj in meshes for slot in obj.material_slots
                       if slot.material and slot.material.node_tree}
            hair=[m for m in materials if any(n.type=='OUTPUT_AOV' and n.aov_name=='zzz_hair'
                  and (n.inputs['Value'].is_linked or n.inputs['Value'].default_value>0.)
                  for n in m.node_tree.nodes)]
            if hair:
                self.restore.callback(self.eye_restore.close)
                try:
                    self.eye_transfer=EyeTransfer(tree,owner.win.view_layer,main,self.eye,hair,self.eye_restore)
                except (AttributeError,IndexError,KeyError,TypeError,RuntimeError,ValueError):
                    self.eye_restore.close()

    def update(self,ctx,space,view,projection,w,h,moving=False,gpu_cloth=None):
        if not self.inputs:
            return
        space=self.aux_space or space
        compositor = space.shading.use_compositor
        try:
            if not self.aux_space:space.shading.use_compositor='DISABLED'
            for source in self.inputs:
                source.render(ctx,space,view,projection,w,h,moving)
            cpu_update=False
            for source in self.inputs:
                if source is gpu_cloth:
                    continue
                if source is self.eye and self.eye_transfer:
                    try:
                        self.eye_transfer.publish()
                        continue
                    except (RuntimeError,ValueError,TypeError,MemoryError):
                        self.eye_restore.close()
                        self.eye_transfer=None
                source.publish()
                cpu_update=True
        finally:
            if not self.aux_space:space.shading.use_compositor=compositor
        if cpu_update:
            # Publishing tags the changed Image ID. The GPU cloth route has
            # no compositor image to invalidate for that pass.
            if gpu_cloth is None:
                self.tree.update_tag()
            ctx.view_layer.update()

