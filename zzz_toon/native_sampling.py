"""Opt-in authored mip sampling for a perspective render camera.

D3D reference isotropic footprint (orthogonal Jacobian), explicit shader bias,
authored linear mip atlas, bilinear texel filtering and explicit mip filter.
No character constants or fixed LOD fallback. Hardware-specific LOD precision,
quad sharing and TAA derivatives are not promised pixel-identical.
"""
import bpy,json,math
from pathlib import Path
import numpy as np
from . import core, surface_uv
from .native_lighting import Ops

def has_uv(ob,name):
 if name!=surface_uv.ATTRIBUTE:return name in ob.data.uv_layers
 field=ob.data.attributes.get(name)
 return field is not None and field.domain=='CORNER' and field.data_type=='FLOAT_VECTOR'

def geometry(ob,uv_names):
 if ob.modifiers.get('ZZZ_SampleFootprint'):return
 group=bpy.data.node_groups.new('ZZZ_SampleFootprint_'+ob.name,'GeometryNodeTree');group.is_modifier=True
 group.interface.new_socket('Geometry',in_out='INPUT',socket_type='NodeSocketGeometry');group.interface.new_socket('Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
 class Target:node_tree=group
 b=core.NB(Target());o=Ops(b);gi=b.node('NodeGroupInput');go=b.node('NodeGroupOutput');geo=gi.outputs[0]
 idx=b.node('GeometryNodeInputIndex');corners=b.node('GeometryNodeCornersOfFace');b.link(idx.outputs[0],corners.inputs['Face Index']);indices=[corners.outputs['Corner Index']]
 for k in [1,2]:
  off=b.node('GeometryNodeOffsetCornerInFace');b.link(indices[0],off.inputs['Corner Index']);off.inputs['Offset'].default_value=k;indices.append(off.outputs[0])
 own=b.node('GeometryNodeSelfObject');world=b.node('GeometryNodeObjectInfo');world.transform_space='ORIGINAL';b.link(own.outputs[0],world.inputs['Object']);pos=b.node('GeometryNodeInputPosition');point=b.node('FunctionNodeTransformPoint');b.link(pos.outputs[0],point.inputs[0]);b.link(world.outputs['Transform'],point.inputs[1])
 def sample(value,index):
  n=b.node('GeometryNodeSampleIndex',data_type='FLOAT_VECTOR',domain='CORNER');b.link(geo,n.inputs['Geometry']);b.link(value,n.inputs['Value']);b.link(index,n.inputs['Index']);return n.outputs[0]
 p=[sample(point.outputs[0],i) for i in indices];e1=o.v('SUBTRACT',p[1],p[0]);e2=o.v('SUBTRACT',p[2],p[0]);n=o.v('CROSS_PRODUCT',e1,e2);inv=o.div(1.,o.m('MAXIMUM',o.v('DOT_PRODUCT',n,n),1e-24));g1=o.scale(o.v('CROSS_PRODUCT',e2,n),inv);g2=o.scale(o.v('CROSS_PRODUCT',n,e1),inv);fields={'zzz_mip_plane':o.v('NORMALIZE',n)}
 for uvname in uv_names:
  if not has_uv(ob,uvname):continue
  uv=b.node('GeometryNodeInputNamedAttribute',data_type='FLOAT_VECTOR');uv.inputs['Name'].default_value=uvname;values=[sample(uv.outputs['Attribute'],i) for i in indices];parts=[]
  for j in [1,2]:
   sep=b.node('ShaderNodeSeparateXYZ');b.link(o.v('SUBTRACT',values[j],values[0]),sep.inputs[0]);parts.append(sep)
  for axis,label in enumerate(['u','v']):fields['zzz_mip_'+uvname+'_'+label]=o.v('ADD',o.scale(g1,parts[0].outputs[axis]),o.scale(g2,parts[1].outputs[axis]))
 for name,value in fields.items():
  n=b.node('GeometryNodeStoreNamedAttribute',data_type='FLOAT_VECTOR',domain='FACE');n.inputs['Name'].default_value=name;b.link(geo,n.inputs['Geometry']);b.link(value,n.inputs['Value']);geo=n.outputs['Geometry']
 b.link(geo,go.inputs[0]);md=ob.modifiers.new('ZZZ_SampleFootprint','NODES');md.node_group=group
 # Only material footprint attributes; preserve all original positions / modifier order.
 outline=next((i for i,m in enumerate(ob.modifiers) if m.name=='ZZZ_Outline'),None)
 if outline is not None:ob.modifiers.move(len(ob.modifiers)-1,outline)

def uniforms(scene):
 name=scene.get('zzz_sampling_uniform_group','ZZZ_MipCamera_'+scene.name);g=bpy.data.node_groups.get(name)
 if g is None:
  g=bpy.data.node_groups.new(name,'ShaderNodeTree');g.interface.new_socket('PixelStep',in_out='OUTPUT',socket_type='NodeSocketVector');g.interface.new_socket('Resolution',in_out='OUTPUT',socket_type='NodeSocketVector')
  out=g.nodes.new('NodeGroupOutput')
  for key in ['PixelStep','Resolution']:
   n=g.nodes.new('ShaderNodeCombineXYZ');n.name=key;g.links.new(n.outputs[0],out.inputs[key])
 scene['zzz_sampling_uniform_group']=g.name
 return g

def write_projection(group, width, height, projection):
    step = (2 / (width * projection[0][0]), 2 / (height * projection[1][1]), 0.)
    for name, values in (('PixelStep', step), ('Resolution', (width, height, 0.))):
        for socket, value in zip(group.nodes[name].inputs, values):
            socket.default_value = value
    return {'width': width, 'height': height, 'step': list(step[:2])}


def update(scene):
 if not scene.camera or scene.camera.data.type!='PERSP':raise ValueError('Native sampling requires perspective render camera')
 w=int(scene.render.resolution_x*scene.render.resolution_percentage/100);h=int(scene.render.resolution_y*scene.render.resolution_percentage/100)
 if scene.render.use_border:raise ValueError('Native sampling requires full-frame render')
 matrix=scene.camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=w,y=h,scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
 return write_projection(uniforms(scene),w,h,matrix)

def footprint(b,scene,uvname):
 o=Ops(b);geo=b.node('ShaderNodeNewGeometry')
 def transform(value,kind='VECTOR',src='WORLD',dst='CAMERA'):
  n=b.node('ShaderNodeVectorTransform',vector_type=kind,convert_from=src,convert_to=dst);b.link(value,n.inputs['Vector']);return n.outputs['Vector']
 def attribute(name):
  n=b.node('ShaderNodeAttribute');n.attribute_name=name;return n.outputs['Vector']
 p=transform(geo.outputs['Position'],'POINT');p.node.label='V37 camera position';sp=b.node('ShaderNodeSeparateXYZ');b.link(p,sp.inputs[0]);depth=sp.outputs[2];ray=o.scale(p,o.div(1.,depth));plane=transform(attribute('zzz_mip_plane'));plane.node.label='V37 plane';num=o.v('DOT_PRODUCT',plane,p);gu=attribute('zzz_mip_'+uvname+'_u');gv=attribute('zzz_mip_'+uvname+'_v')
 un=b.node('ShaderNodeGroup');un.node_tree=uniforms(scene);st=b.node('ShaderNodeSeparateXYZ');b.link(un.outputs['PixelStep'],st.inputs[0]);tc=b.node('ShaderNodeTexCoord');pixel=o.v('MULTIPLY',tc.outputs['Window'],un.outputs['Resolution']);ps=b.node('ShaderNodeSeparateXYZ');b.link(pixel,ps.inputs[0]);deltas=[]
 for axis in range(2):
  # Finite difference to the same 2x2 raster quad's opposite lane.
  sign=o.sub(1.,o.mul(2.,o.m('FLOORED_MODULO',o.m('FLOOR',ps.outputs[axis]),2.)))
  v=b.node('ShaderNodeCombineXYZ');b.link(o.mul(sign,st.outputs[axis]),v.inputs[axis]);r=o.v('ADD',ray,v.outputs[0]);q=o.scale(r,o.div(num,o.v('DOT_PRODUCT',plane,r)));delta=transform(o.v('SUBTRACT',q,p),src='CAMERA',dst='WORLD');delta.node.label='V37 delta '+str(axis);deltas.append((o.v('DOT_PRODUCT',gu,delta),o.v('DOT_PRODUCT',gv,delta)))
 return deltas

def sample(b,uv,atlas,w,h,mips,derivatives,bias,mip_filter):
 o=Ops(b);d=[(o.mul(du,w),o.mul(dv,h)) for du,dv in derivatives]
 xx=o.add(o.mul(d[0][0],d[0][0]),o.mul(d[0][1],d[0][1]));yy=o.add(o.mul(d[1][0],d[1][0]),o.mul(d[1][1],d[1][1]));xy=o.add(o.mul(d[0][0],d[1][0]),o.mul(d[0][1],d[1][1]));delta=o.sub(xx,yy)
 # D3D reference orthogonal Jacobian: largest singular value of the footprint.
 major=o.mul(.5,o.add(o.add(xx,yy),o.m('SQRT',o.add(o.mul(delta,delta),o.mul(4.,o.mul(xy,xy))))))
 lod=o.add(o.mul(.5,o.m('LOGARITHM',o.m('MAXIMUM',major,1e-20),2.)),bias);lod=o.m('MINIMUM',o.m('MAXIMUM',lod,0.),float(mips-1))
 low=o.m('FLOOR',o.add(lod,.5)) if mip_filter=='nearest' else o.m('FLOOR',lod)
 uvsep=b.node('ShaderNodeSeparateXYZ');b.link(uv,uvsep.inputs[0])
 def level(k):
  divisor=o.m('POWER',2.,k);mw=o.div(w,divisor);mh=o.div(h,divisor);oy=o.sub(2.*h,o.mul(2.,mh));coords=[o.sub(o.mul(uvsep.outputs[i],sz),.5) for i,sz in enumerate([mw,mh])];ix=[o.m('FLOOR',v) for v in coords];f=[o.sub(coords[i],ix[i]) for i in range(2)];taps=[]
  for y in range(2):
   row=[]
   for x in range(2):
    v=b.node('ShaderNodeCombineXYZ');b.link(o.div(o.add(o.m('FLOORED_MODULO',o.add(ix[0],x),mw),.5),w),v.inputs[0]);b.link(o.div(o.add(o.add(o.m('FLOORED_MODULO',o.add(ix[1],y),mh),oy),.5),2.*h),v.inputs[1]);t=b.node('ShaderNodeTexImage');t.image=atlas;t.interpolation='Closest';t.extension='EXTEND';t.label='Native mip atlas tap';b.link(v.outputs[0],t.inputs['Vector']);row.append(t)
   taps.append(row)
  rgb=o.cmix(f[1],o.cmix(f[0],taps[0][0].outputs['Color'],taps[0][1].outputs['Color']),o.cmix(f[0],taps[1][0].outputs['Color'],taps[1][1].outputs['Color']));alpha=o.mix(f[1],o.mix(f[0],taps[0][0].outputs['Alpha'],taps[0][1].outputs['Alpha']),o.mix(f[0],taps[1][0].outputs['Alpha'],taps[1][1].outputs['Alpha']));return rgb,alpha
 color,alpha=level(low)
 if mip_filter=='linear':
  c2,a2=level(o.m('MINIMUM',o.add(low,1.),mips-1));t=o.sub(lod,low);color=o.cmix(t,color,c2);alpha=o.mix(t,alpha,a2)
 return color,alpha,lod

def validate(p):
 if p is None:return None
 if not isinstance(p,dict) or p.get('schema')!='zzz-toon-sampling/1':raise ValueError('Unknown sampling schema')
 if not p.get('source') or not p.get('scope') or not p.get('sampler_provenance'):raise ValueError('Sampling source, scope and sampler provenance required')
 if not isinstance(p.get('meshes'),list) or not p['meshes'] or not all(isinstance(x,str) and x for x in p['meshes']):raise ValueError('Explicit sampling meshes required')
 if not isinstance(p.get('materials'),dict) or not p['materials']:raise ValueError('Explicit sampling materials required')
 for name,textures in p['materials'].items():
  if not isinstance(name,str) or not isinstance(textures,dict) or not textures:raise ValueError('Invalid sampling material')
  for image,item in textures.items():
   if not isinstance(item,dict):raise ValueError('Invalid sampling texture')
   w=item.get('width');h=item.get('height');count=item.get('mip_count')
   if type(w)!=int or w<2 or w&(w-1) or w!=h or count!=w.bit_length():raise ValueError('Sampling requires a complete square power-of-two mip chain')
   if not item.get('atlas') or not item.get('source') or not item.get('uv'):raise ValueError('Atlas, UV and source required')
   if item.get('mip_filter') not in ('nearest','linear') or item.get('address')!='repeat' or item.get('anisotropy')!=1:raise ValueError('Only explicit isotropic repeat samplers are supported')
   if type(item.get('saturate')) is not bool:raise ValueError('Explicit post-sample saturate flag required')
   if any(type(item.get(k)) not in (int,float) or not math.isfinite(item[k]) or not -16<=item[k]<=15.99 for k in ('shader_bias','sampler_bias')):raise ValueError('Explicit finite shader and sampler biases required')
   if 'fixed_lod' in item:raise ValueError('Fixed LOD is not a native biased sampler')
 return p

def _clear_material(mat):
 raw=mat.get('zzz_sampling_links')
 if not raw:return
 tree=mat.node_tree
 for n in list(tree.nodes):
  if n.get('zzz_sampling_generated'):tree.nodes.remove(n)
 for source,output,target,index in json.loads(raw):
  a=tree.nodes.get(source);b=tree.nodes.get(target)
  if a and b and output in a.outputs and index<len(b.inputs):tree.links.new(a.outputs[output],b.inputs[index])
 for key in ('zzz_sampling_links','zzz_sampling_profile'):
  if key in mat:del mat[key]

def clear(objs,scene=None):
 """Restore original image links and remove only this module's geometry fields."""
 mats={m for ob in objs if ob.type=='MESH' for m in ob.data.materials if m and m.use_nodes}
 for mat in mats:_clear_material(mat)
 for ob in objs:
  if ob.type!='MESH':continue
  md=ob.modifiers.get('ZZZ_SampleFootprint')
  if md:
   g=md.node_group;ob.modifiers.remove(md)
   if g and g.users==0:bpy.data.node_groups.remove(g)
  if 'zzz_sampling_profile' in ob:del ob['zzz_sampling_profile']
 if scene and not any(o.get('zzz_sampling_profile') for o in scene.objects):
  for k in ('zzz_sampling_profile','zzz_sampling_error'):
   if k in scene:del scene[k]

def apply(objs,scene,p):
 """Apply to named meshes/materials, with reversible links and no scene tuning.

Input atlas is width W, height 2W; mip k occupies bottom-origin rectangle
(0, 2W-2*(W>>k), W>>k, W>>k). All stored RGB must already be linear.
Render-camera scope only: arbitrary viewport projection is not supported.
"""
 if validate(p) is None:return {}
 if scene.render.engine!='BLENDER_EEVEE':raise ValueError('Native sampling is validated for EEVEE only')
 if not scene.camera or scene.camera.data.type!='PERSP' or scene.render.use_border:raise ValueError('Native sampling requires full-frame perspective camera rendering')
 selected=[o for o in objs if o.type=='MESH' and o.name in p['meshes']]
 if {o.name for o in selected}!=set(p['meshes']):raise ValueError('Sampling meshes not selected')
 mats={m.name:m for ob in selected for m in ob.data.materials if m and m.use_nodes}
 if set(p['materials'])-set(mats):raise ValueError('Sampling materials not found on selected meshes')
 plan=[];uvs_by_object={ob:set() for ob in selected};derived={}
 for name,items in p['materials'].items():
  mat=mats[name]
  if any(ob.type=='MESH' and mat.name in ob.data.materials and ob not in selected for ob in bpy.data.objects):raise ValueError('Sampling material also used by unprepared mesh: '+name)
  for image,item in items.items():
   candidates=[
    n for n in mat.node_tree.nodes
    if n.type=='TEX_IMAGE' and n.image and not n.get('zzz_sampling_generated')
    and (n.image.get('zzz_wardrobe_slot')==image if 'zzz_wardrobe_slot' in n.image else
         n.image.name==image or (n.image.source=='FILE' and bpy.path.basename(n.image.filepath)==image))
   ]
   if len(candidates)!=1:raise ValueError('Sampling requires one exact source image node: '+name+'/'+image)
   tex=candidates[0];uvname=item['uv'];needed={uvname}
   if tex.projection!='FLAT':raise ValueError('Only flat image projection is supported')
   if tex.inputs['Vector'].is_linked:
    n=tex.inputs['Vector'].links[0].from_node
    if n.get('zzz_two_sided_uv') and n.type=='MIX' and n.data_type=='VECTOR':
     if uvname!='UV0':raise ValueError('PS1119 two-sided sampling requires UV0 as its primary UV')
     needed.add(n.get('zzz_back_uv','UV3'))
    elif n.type!='UVMAP' or n.uv_map!=uvname:raise ValueError('Unverified warped UV sampling')
   elif any(ob.data.uv_layers.active is None or ob.data.uv_layers.active.name!=uvname for ob in selected if mat.name in ob.data.materials):raise ValueError('Unlinked image UV must match the active UV map')
   if item.get('derive_mips'):
    from .wardrobe_slots import make_atlas
    if tex.image not in derived:derived[tex.image]=make_atlas(tex.image)
    atlas=derived[tex.image]
    w,h=tex.image.size
    item=dict(item,width=w,height=h,mip_count=w.bit_length())
   else:
    atlas=bpy.data.images.load(bpy.path.abspath(item['atlas']),check_existing=True)
   if tuple(atlas.size)!=(item['width'],2*item['height']) or not atlas.is_float:raise ValueError('Invalid authored linear mip atlas')
   plan.append((mat,tex,item,atlas))
   for ob in selected:
    if mat.name not in ob.data.materials:continue
    if len(needed)>1 and n.get('zzz_partial_uv3') and not ob.get('zzz_ps1119_has_uv3'):
     if not ob.data.uv_layers.active or ob.data.uv_layers.active.name!=uvname:
      raise ValueError('Sampling profile must match fallback UV on '+ob.name)
     uvs_by_object[ob].add(uvname)
    else:uvs_by_object[ob].update(needed)
 for ob in selected:
  if any(not has_uv(ob,name) for name in uvs_by_object[ob]):raise ValueError('Missing sampling UV map: '+ob.name)
  if any(len(face.vertices)!=3 for face in ob.data.polygons):raise ValueError('Sampling requires triangulated input meshes')
 # Validate first; then remove old nodes and rebuild a bounded set.
 clear(selected,scene)
 for ob in selected:geometry(ob,sorted(uvs_by_object[ob]));ob['zzz_sampling_profile']=p['source']
 reports={};oldnodes={};links={};derivs={}
 for mat,tex,item,atlas in plan:
  if mat.name not in oldnodes:oldnodes[mat.name]=set(mat.node_tree.nodes);links[mat.name]=[];reports[mat.name]=[]
  b=core.NB(mat);key=(mat.name,item['uv'])
  if key not in derivs:derivs[key]=footprint(b,scene,item['uv'])
  if tex.inputs['Vector'].is_linked:uv=tex.inputs['Vector'].links[0].from_socket
  else:n=b.node('ShaderNodeUVMap');n.uv_map=item['uv'];uv=n.outputs[0]
  destinations=[(s.name,l.to_socket) for s in tex.outputs for l in s.links]
  for output,target in destinations:links[mat.name].append([tex.name,output,target.node.name,list(target.node.inputs).index(target)])
  atlas.colorspace_settings.name='Non-Color';atlas.alpha_mode='CHANNEL_PACKED';atlas.use_half_precision=False
  gradients=derivs[key]
  if uv.node.get('zzz_two_sided_uv'):
   route_key=(mat.name,uv.node.name,'two-sided')
   if route_key not in derivs:
    front,back=gradients,footprint(b,scene,uv.node.get('zzz_back_uv','UV3'))
    side=uv.node.inputs[0].links[0].from_socket
    derivs[route_key]=[[Ops(b).mix(side,a,c) for a,c in zip(aa,cc)] for aa,cc in zip(front,back)]
   gradients=derivs[route_key]
  rgb,alpha,lod=sample(b,uv,atlas,item['width'],item['height'],item['mip_count'],gradients,bias=item['shader_bias']+item['sampler_bias'],mip_filter=item['mip_filter'])
  lod.node['zzz_sampling_lod_image']=tex.image.name
  if item['saturate']:
   op=Ops(b);rgb=op.v('MINIMUM',op.v('MAXIMUM',rgb,(0.,0.,0.)),(1.,1.,1.));alpha=op.sat(alpha)
  for name,target in destinations:b.link(rgb if name=='Color' else alpha,target)
  reports[mat.name].append({'image':tex.image.name,'uv':item['uv'],'mips':item['mip_count'],'bias':item['shader_bias']+item['sampler_bias'],'mip_filter':item['mip_filter']})
 for name,nodes in oldnodes.items():
  mat=mats[name]
  for n in set(mat.node_tree.nodes)-nodes:n['zzz_sampling_generated']=True
  mat['zzz_sampling_links']=json.dumps(links[name]);mat['zzz_sampling_profile']=json.dumps(p['materials'][name],sort_keys=True)
 scene['zzz_sampling_profile']=json.dumps({'source':p['source'],'scope':p['scope'],'sampler_provenance':p['sampler_provenance']},sort_keys=True)
 update(scene);register();return reports

from bpy.app.handlers import persistent

@persistent
def _update(scene,*args):
 if not scene or not scene.get('zzz_sampling_profile'):return
 from .fullscreen import active_for
 if active_for(scene):return
 try:
  update(scene)
  if 'zzz_sampling_error' in scene:del scene['zzz_sampling_error']
 except Exception as e:
  scene['zzz_sampling_error']=str(e)
  raise

@persistent
def _load(*args):
 for scene in bpy.data.scenes:_update(scene)

def register():
 for collection,fn in [(bpy.app.handlers.render_pre,_update),(bpy.app.handlers.frame_change_post,_update),(bpy.app.handlers.load_post,_load)]:
  if fn not in collection:collection.append(fn)

def unregister():
 for collection,fn in [(bpy.app.handlers.render_pre,_update),(bpy.app.handlers.frame_change_post,_update),(bpy.app.handlers.load_post,_load)]:
  if fn in collection:collection.remove(fn)
