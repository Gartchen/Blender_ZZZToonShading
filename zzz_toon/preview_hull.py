"""Evaluate the same outline geometry as a separate object to avoid joining the shaded body twice."""
import bpy
from mathutils import Matrix
from . import feature_layers


def separate(session):
    hulls=[]
    invisible_in_eye_pass=[]
    for obj in list(session.scene.objects):
        mod=obj.modifiers.get('ZZZ_Outline')
        if not mod or mod.type!='NODES' or not mod.show_viewport or not mod.node_group:
            continue
        # A detached outline is a new object. It does not inherit the source
        # object's visibility (including unequipped parasol meshes).
        if obj.hide_render or not obj.visible_camera or not obj.visible_get(view_layer=session.win.view_layer):
            continue
        tree=mod.node_group
        src=next((n for n in tree.nodes if n.type=='GROUP_INPUT'),None)
        dst=next((n for n in tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output),None)
        if not src or not dst or not dst.inputs[0].is_linked:
            continue
        # Parameterized modifiers own their width, material and face selection.
        # A fresh modifier on a detached object would use the group defaults.
        if any(socket.is_linked for socket in list(src.outputs)[1:]):
            continue
        join=dst.inputs[0].links[0].from_node
        if join.bl_idname!='GeometryNodeJoinGeometry' or len(join.inputs[0].links)!=2:
            continue
        if not any(l.from_node==src for l in join.inputs[0].links):
            continue
        group=tree.copy()
        session.restore.callback(bpy.data.node_groups.remove,group)
        mesh=bpy.data.meshes.new('ZZZ Preview Outline')
        session.restore.callback(bpy.data.meshes.remove,mesh)
        hull=bpy.data.objects.new('ZZZ Preview Outline '+obj.name,mesh)
        # Face objects also belong to unlinked light-linking collections.
        # Preserve scene membership as well as the light/shadow assignments.
        collections=obj.users_collection if session.scene.zzz_toon.wardrobe_role in ('Remielle','Velina') else obj.users_collection[:1]
        for collection in collections:
            collection.objects.link(hull)
        hulls.append(hull)
        session.restore.callback(bpy.data.objects.remove,hull,do_unlink=True)
        hull.parent=obj
        hull.matrix_parent_inverse=Matrix.Identity(4)
        hull.matrix_basis=Matrix.Identity(4)
        source=group.nodes[src.name]
        join=group.nodes[join.name]
        branch=next(link.from_socket for link in join.inputs[0].links if link.from_node!=source)
        if (branch.node.bl_idname=='GeometryNodeSetMaterial' and not branch.node.inputs['Material'].is_linked
                and not branch.node.inputs['Selection'].is_linked and branch.node.inputs['Selection'].default_value):
            shell=branch.node.inputs['Material'].default_value
            wrapper=shell.node_tree.nodes.get(feature_layers.WRAPPER) if shell and shell.node_tree else None
            if wrapper and wrapper.inputs[2].is_linked and wrapper.inputs[2].links[0].from_node.type=='BSDF_TRANSPARENT':
                invisible_in_eye_pass.append(hull)
        group.links.new(branch,group.nodes[dst.name].inputs[0])
        ref=group.nodes.new('GeometryNodeObjectInfo')
        ref.transform_space='RELATIVE'
        ref.inputs['Object'].default_value=obj
        for link in list(source.outputs[0].links):
            group.links.new(ref.outputs['Geometry'],link.to_socket)
        session.keep(mod,'show_viewport',False)
        hull.modifiers.new('Preview Outline','NODES').node_group=group
    eye=session.scene.view_layers.get(feature_layers.LAYER)
    if eye and invisible_in_eye_pass and session.initial_loop and session.scene.zzz_toon.wardrobe_role in ('Remielle','Velina'):
        eye.update()
        for hull in invisible_in_eye_pass:
            if hull.name in eye.objects:
                hull.hide_set(True,view_layer=eye)
    def detach():
        for hull in hulls:
            for collection in list(hull.users_collection):
                collection.objects.unlink(hull)
        if hulls:
            session.scene.view_layers[0].update()
    session.restore.callback(detach)
