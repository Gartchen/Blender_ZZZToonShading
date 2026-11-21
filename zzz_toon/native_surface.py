"""Opt-in original TBN for verified linear skinning and explicit direction frames."""
import json
import numpy as np
from . import core, outline_runtime
from .native_lighting import Ops

def validate(p):
 if p is None:return None
 if not isinstance(p,dict) or p.get('schema')!='zzz-toon-surface/1':raise ValueError('Unknown native surface schema')
 if not p.get('source') or not p.get('scope'):raise ValueError('Surface source and scope required')
 inputs=p.get('mesh_inputs')
 if not isinstance(inputs,dict) or not inputs:raise ValueError('Explicit verified surface mesh inputs required')
 for name,item in inputs.items():
  if not isinstance(name,str) or not name or not isinstance(item,dict) or not item.get('path') or not item.get('source'):raise ValueError('Invalid surface input')
  frame=item.get('direction_frame','object_normal')
  if frame not in ('object_normal','rigid_draw'):raise ValueError('Unknown surface direction frame: '+name)
  if frame=='rigid_draw' and not item.get('frame_source'):raise ValueError('Rigid draw requires explicit frame evidence: '+name)
 return p

def preflight(ob,frame='object_normal'):
 matrix=np.array(ob.matrix_world)[:3,:3]
 if not np.isfinite(matrix).all():raise ValueError('Nonfinite surface object frame: '+ob.name)
 sv=np.linalg.svd(matrix,compute_uv=False)
 if sv.min()<1e-8:raise ValueError('Singular surface object frame: '+ob.name)
 if frame=='rigid_draw':
  if np.linalg.det(matrix)<=0:raise ValueError('Rigid draw probes require a positive import frame: '+ob.name)
 elif sv.max()/sv.min()>1.0001:raise ValueError('Native surface requires verified uniform object frame: '+ob.name)
 if ob.data.shape_keys:raise ValueError('Morph TBN not implemented: '+ob.name)
 arms=[m for m in ob.modifiers if m.type=='ARMATURE' and m.show_viewport and m.show_render]
 if len(arms)!=1 or arms[0].use_deform_preserve_volume or arms[0].use_bone_envelopes or not arms[0].use_vertex_groups:raise ValueError('Native surface requires one linear vertex-group Armature: '+ob.name)
 if list(ob.modifiers)[0]!=arms[0]:raise ValueError('Native surface requires Armature first: '+ob.name)

def apply(objs,scene,p):
 """Public build hook. Omit the profile and rebuild to disable."""
 import bpy
 if validate(p) is None:return {}
 selected=[o for o in objs if o.type=='MESH' and o.name in p['mesh_inputs']]
 missing=set(p['mesh_inputs'])-{o.name for o in selected}
 if missing:raise ValueError('Surface meshes not selected: '+', '.join(sorted(missing)))
 mats={m for ob in selected for m in ob.data.materials if m and m.use_nodes and (m.get('zzz_surface_tbn_v34') or any(n.type=='NORMAL_MAP' and n.outputs[0].is_linked for n in m.node_tree.nodes))}
 for ob in selected:
  preflight(ob,p['mesh_inputs'][ob.name].get('direction_frame','object_normal'))
  with np.load(p['mesh_inputs'][ob.name]['path']) as a:
   if 'valid' not in a or a['valid'].shape!=(len(ob.data.vertices),) or not np.equal(a['valid'],True).all():raise ValueError('Unverified surface vertices: '+ob.name)
 for mat in mats:
  if any(ob.type=='MESH' and mat.name in ob.data.materials and ob not in selected for ob in bpy.data.objects):raise ValueError('Surface material also used by an unprepared mesh: '+mat.name)
  for nm in mat.node_tree.nodes:
   if nm.type=='NORMAL_MAP' and nm.outputs[0].is_linked and (not nm.inputs['Color'].is_linked or nm.inputs['Strength'].is_linked or abs(nm.inputs['Strength'].default_value-1)>1e-6):raise ValueError('Native surface requires unscaled normal reconstruction')
 report={'meshes':{},'materials':{}}
 for ob in selected:
  item=p['mesh_inputs'][ob.name]
  outline_runtime.load_inputs(ob,{'mesh_inputs':{ob.name.split('.')[0]:item}})
  if not ob.modifiers.get('ZZZ_NativeOutlineNormals'):outline_runtime.prepare_skin(ob)
  report['meshes'][ob.name]=geometry(ob,item.get('direction_frame','object_normal'))
  ob['zzz_native_surface_profile']=json.dumps({'source':p['source'],'scope':p['scope'],'input':item},sort_keys=True)
 for mat in mats:report['materials'][mat.name]=material(mat)
 return report

def geometry(ob,frame='object_normal'):
 import bpy
 md=ob.modifiers.get('ZZZ_NativeOutlineNormals');helper=bpy.data.objects.get('ZZZ_OutlineProbe_'+ob.name)
 if not md or not helper or not ob.get('zzz_outline_input_source'):raise ValueError('Verified native direction inputs required: '+ob.name)
 if ob.data.shape_keys:raise ValueError('Morph TBN not implemented')
 g=md.node_group
 if g.get('zzz_surface_tbn_v34'):
  if g.get('zzz_surface_direction_frame','object_normal')==frame:return False
  if g.users>1:
   g=g.copy();md.node_group=g
  # Follow the stored surface attributes. Existing files also have outline transforms.
  def transform_node(attribute):
   store=next(n for n in g.nodes if n.bl_idname=='GeometryNodeStoreNamedAttribute' and n.inputs['Name'].default_value==attribute)
   normalize=store.inputs['Value'].links[0].from_node
   return normalize.inputs[0].links[0].from_node
  normal_transform=transform_node('zzz_surface_n')
  world=transform_node('zzz_surface_t').inputs[1].links[0].from_socket
  inv=next(link.to_node for link in world.links if link.to_node.bl_idname=='FunctionNodeInvertMatrix')
  transpose=next(link.to_node for link in inv.outputs[0].links if link.to_node.bl_idname=='FunctionNodeTransposeMatrix')
  g.links.new(world if frame=='rigid_draw' else transpose.outputs[0],normal_transform.inputs[1])
  g['zzz_surface_direction_frame']=frame
  return True
 g=g.copy();md.node_group=g
 class Target:node_tree=g
 b=core.NB(Target());o=Ops(b);count=len(ob.data.vertices)
 oi=b.node('GeometryNodeObjectInfo');oi.transform_space='RELATIVE';oi.inputs['Object'].default_value=helper
 idx=b.node('GeometryNodeInputIndex');pos=b.node('GeometryNodeInputPosition')
 def sample(offset):
  s=b.node('GeometryNodeSampleIndex',data_type='FLOAT_VECTOR',domain='POINT');b.link(oi.outputs['Geometry'],s.inputs['Geometry']);b.link(pos.outputs[0],s.inputs['Value']);b.link(o.add(idx.outputs[0],offset),s.inputs['Index']);return s.outputs[0]
 own=b.node('GeometryNodeSelfObject');info=b.node('GeometryNodeObjectInfo');info.transform_space='ORIGINAL';b.link(own.outputs[0],info.inputs['Object']);world=info.outputs['Transform'];inv=b.node('FunctionNodeInvertMatrix');b.link(world,inv.inputs[0]);tr=b.node('FunctionNodeTransposeMatrix');b.link(inv.outputs[0],tr.inputs[0]);base=sample(0)
 def direction(v,m):
  n=b.node('FunctionNodeTransformDirection');b.link(v,n.inputs[0]);b.link(m,n.inputs[1]);return o.v('NORMALIZE',n.outputs[0])
 # For a rigid draw, N is encoded by the displacement between two skinned points.
 # Both differences must leave the FBX import frame as vectors before normalization.
 normal=direction(o.v('SUBTRACT',sample(count),base),world if frame=='rigid_draw' else tr.outputs[0]);tangent=direction(o.v('SUBTRACT',sample(2*count),base),world)
 packed=b.node('GeometryNodeInputNamedAttribute',data_type='FLOAT_VECTOR');packed.inputs['Name'].default_value='zzz_ol_packed';sep=b.node('ShaderNodeSeparateXYZ');b.link(packed.outputs[0],sep.inputs[0]);det=b.node('FunctionNodeMatrixDeterminant');b.link(world,det.inputs[0]);hand=o.mul(sep.outputs[2],o.m('SIGN',det.outputs[0]));bitangent=o.scale(o.v('CROSS_PRODUCT',normal,tangent),hand)
 go=next(n for n in g.nodes if n.type=='GROUP_OUTPUT');geo=go.inputs[0].links[0].from_socket
 for key,value in [('zzz_surface_n',normal),('zzz_surface_t',tangent),('zzz_surface_b',bitangent)]:
  s=b.node('GeometryNodeStoreNamedAttribute',data_type='FLOAT_VECTOR',domain='POINT');s.inputs['Name'].default_value=key;b.link(geo,s.inputs['Geometry']);b.link(value,s.inputs['Value']);geo=s.outputs[0]
 b.link(geo,go.inputs[0]);g['zzz_surface_tbn_v34']=True;g['zzz_surface_direction_frame']=frame;ob['zzz_surface_tbn_v34']='Original asset directions, current linear skinning, world-space vertex TBN; explicit validation only';return True

def material(mat):
 if mat.get('zzz_surface_tbn_v34'):return 0
 nodes=[n for n in mat.node_tree.nodes if n.type=='NORMAL_MAP' and n.outputs[0].is_linked];changed=0
 for nm in nodes:
  if not nm.inputs['Color'].is_linked or nm.inputs['Strength'].is_linked or abs(nm.inputs['Strength'].default_value-1)>1e-6:raise ValueError('Native TBN requires unscaled normal reconstruction')
  b=core.NB(mat);o=Ops(b);encoded=nm.inputs['Color'].links[0].from_socket
  if encoded.node.get('zzz_ps1119_normal_map'):
   # This path reconstructs T*x + B*y + signed N*z directly.
   encoded=encoded.node.inputs[4].links[0].from_socket
  dec=o.v('SUBTRACT',o.scale(encoded,2.),(1.,1.,1.));sep=b.node('ShaderNodeSeparateXYZ');b.link(dec,sep.inputs[0]);values=[]
  for i,key in enumerate(('zzz_surface_t','zzz_surface_b','zzz_surface_n')):
   n=b.node('ShaderNodeAttribute');n.attribute_name=key;val=sep.outputs[i]
   if i==2:
    geom=b.node('ShaderNodeNewGeometry');val=o.mul(val,o.sub(1.,o.mul(2.,geom.outputs['Backfacing'])))
   values.append(o.scale(n.outputs['Vector'],val))
  normal=o.v('NORMALIZE',o.v('ADD',o.v('ADD',values[0],values[1]),values[2]))
  for link in list(nm.outputs[0].links):b.link(normal,link.to_socket)
  changed+=1
 if changed:mat['zzz_surface_tbn_v34']=True
 return changed
