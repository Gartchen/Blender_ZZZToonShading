"""UI Face VS22 / PS261 opaque base pass, with explicit measured inputs.

Original DXBC uses integer B&16 / B&3; the HLSL decompiler printed these as zero.
Stencil receiver coverage is optional and supplied by an explicit caller.
Depth/stencil coverage and overlay/blush are deliberately not inferred.
"""
from .native_lighting import Ops
from . import native_environment


def validate(profile):
    if profile is None:return None
    if profile.get('schema')!='zzz-toon-native-face/1':raise ValueError('Unknown native face schema')
    if profile.get('receiver') is not None:
        from . import stencil_runtime
        stencil_runtime.validate(profile['receiver'])
    if not all(profile.get(k) for k in ('source','scope','bone','materials','mesh_inputs')):
        raise ValueError('Native face requires explicit provenance, head bone, materials and mesh inputs')
    for p in profile['materials'].values():
        if p['source']['shaders'].get('vs')!='bd0cf06981a8f054' or p['source']['shaders'].get('ps') not in ('202c3cc697d36317','3969ce67a7c8092e'):
            raise ValueError('Unsupported native face shader variant')
        if not p.get('blush_neutral'):raise ValueError('Native face requires verified neutral blush')
        native_environment.validate(p['environment'])
        if p['visibility']!=1. or p['pixel']['_CharacterUISelfShadow'][0]!=0 or p['pixel']['_IsStencilReceiverPass'][0]!=0:
            raise ValueError('Native face currently supports the captured unshadowed base pass only')
    return profile


def bind(arm,scene,profile):
    if validate(profile) is None:return None
    import bpy,json
    if arm is None or profile['bone'] not in arm.pose.bones:raise ValueError('Native face head bone missing')
    name='ZZZ_NativeFace_'+arm.name;ob=bpy.data.objects.get(name)
    if ob is None:ob=bpy.data.objects.new(name,None);scene.collection.objects.link(ob)
    ob.parent=arm;ob.parent_type='BONE';ob.parent_bone=profile['bone'];ob.matrix_parent_inverse.identity();ob.matrix_basis.identity()
    ob.hide_render=True;ob.empty_display_size=.01;ob['zzz_native_face_profile']=json.dumps(profile,sort_keys=True)
    return ob


def apply_inputs(ob,profile,slots):
    item=(profile or {}).get('mesh_inputs',{}).get(ob.name.split('.')[0])
    if item is None:return None
    import numpy as np
    me=ob.data
    with np.load(item['path']) as a:
        pos=np.array([v.co[:] for v in me.vertices]);loop=np.array([v.vertex_index for v in me.loops]);uv=np.array([v.uv[:] for v in me.uv_layers['UV0'].data])
        if pos.shape!=a['position'].shape or not np.allclose(pos,a['position'],atol=2e-6,rtol=0) or not np.array_equal(loop,a['loop_vertex']) or uv.shape!=a['uv0'].shape or not np.allclose(uv,a['uv0'],atol=2e-6,rtol=0):raise ValueError('Native face destination mesh/UV mismatch: '+ob.name)
        required=[i for p in me.polygons if p.material_index<len(slots) and slots[p.material_index] in profile['materials']
                  for i in p.loop_indices]
        if not len(required) or not a['valid'][required].all():raise ValueError('Incomplete native face input coverage: '+ob.name)
        data=[('zzz_face_uv3',np.c_[a['uv3'],np.zeros(len(loop))],'FLOAT_VECTOR','vector'),('zzz_face_flags',a['flags'],'FLOAT_VECTOR','vector'),('zzz_face_alpha',a['alpha'],'FLOAT','value')]
        for name,values,kind,field in data:
            if not np.isfinite(values).all():raise ValueError('Nonfinite native face inputs')
            old=me.attributes.get(name)
            if old:me.attributes.remove(old)
            at=me.attributes.new(name,kind,'CORNER');at.data.foreach_set(field,values.astype(np.float32).ravel())
    return {'verified_face_corners':len(required),'source':item.get('source')}


def rows(b, head):
    """Inverse of full head basis. Driver RNA is column-major."""
    o=Ops(b);cols=[]
    for col in range(3):
        n=b.node('ShaderNodeCombineXYZ')
        for row in range(3):
            d=n.inputs[row].driver_add('default_value').driver;d.type='AVERAGE'
            v=d.variables.new();v.name='basis';v.type='SINGLE_PROP';v.targets[0].id=head
            v.targets[0].data_path=f'matrix_world[{col}][{row}]'
        cols.append(n.outputs[0])
    cross=lambda a,c:o.v('CROSS_PRODUCT',a,c)
    inv=o.div(1.,o.v('DOT_PRODUCT',cols[0],cross(cols[1],cols[2])))
    return cols,[o.scale(cross(cols[(i+1)%3],cols[(i+2)%3]),inv) for i in range(3)]


def parts(b,v):
    n=b.node('ShaderNodeSeparateXYZ');b.link(v,n.inputs[0]);return list(n.outputs)[:3]


def vector(b,xyz):
    n=b.node('ShaderNodeCombineXYZ')
    for i,x in enumerate(xyz):
        if isinstance(x,(int,float)):n.inputs[i].default_value=x
        else:b.link(x,n.inputs[i])
    return n.outputs[0]


def nose_hsv(b,color):
    """The shader's RGB/HSV roundtrip, including its 1e-4 epsilons."""
    o=Ops(b);r,g,bl=parts(b,color);step=lambda x,y:o.sub(1.,o.m('LESS_THAN',x,y))
    k=step(g,bl);px=o.mix(k,bl,g);py=o.mix(k,g,bl);pz=o.mix(k,-1.,0.);pw=o.mix(k,.666666687,-.333333313)
    k=step(r,px);qx=o.mix(k,px,r);qz=o.mix(k,pw,pz);qw=o.mix(k,r,px)
    d=o.sub(qx,o.m('MINIMUM',qw,py));h=o.m('ABSOLUTE',o.add(qz,o.div(o.sub(qw,py),o.add(o.mul(d,6.),.0001))))
    s=o.div(d,o.add(qx,.0001));v=o.mul(qx,o.add(.8,o.mul(-.2,o.add(1.,o.sub(qx,s)))))
    rgb=[]
    for offset in (1.,.666666687,.333333343):
        x=o.sat(o.sub(o.m('ABSOLUTE',o.sub(o.mul(o.m('FRACT',o.add(h,offset)),6.),3.)),1.))
        rgb.append(o.mul(v,o.add(1.,o.mul(s,o.sub(x,1.)))))
    return vector(b,rgb)


def build(name,main_tex,light_tex,profile,head,core,stencil_coverage=None):
    import bpy
    p=profile['pixel'];vs=profile['vertex'];env=profile['environment'];e=profile['entity']
    scalar=lambda key:p[key][0]
    def pick(key,code):
        return o.cmix(o.m('GREATER_THAN',code,1.5),o.cmix(o.m('GREATER_THAN',code,.5),tuple(p[key][:3]),tuple(p[key+'2'][:3])),tuple(p[key+'3'][:3]))
    if scalar('_UseOverlayTex')!=0 or scalar('_CharacterUISelfShadow')!=0:
        raise ValueError('Native face base pass requires explicit overlay/self-shadow implementation')
    if any(abs(x)>1e-8 for x in profile['vertex_ambient']):raise ValueError('Nonzero face vertex ambient unsupported')
    mat=bpy.data.materials.get(name) or bpy.data.materials.new(name);mat.use_nodes=True;mat.node_tree.nodes.clear();b=core.NB(mat);o=Ops(b)
    geo=b.node('ShaderNodeNewGeometry');pos=geo.outputs['Position'];view=geo.outputs['Incoming'];normal=geo.outputs['Normal']
    cols,inv=rows(b,head)
    # Blender head local X is reflected relative to Unity's captured object X.
    inv_game=[o.scale(inv[0],-1.),inv[1],inv[2]]
    L=o.v('NORMALIZE',o.v('SUBTRACT',tuple(env['entity_position']),pos))
    center=tuple(profile['middle_world']);lv=o.v('NORMALIZE',o.v('SUBTRACT',tuple(env['entity_position']),center))
    axisx=o.v('NORMALIZE',cols[0]);axisy=o.v('NORMALIZE',cols[1])
    tilt=o.mul(.5,o.m('ABSOLUTE',o.v('DOT_PRODUCT',axisx,lv)))
    ax=parts(b,axisy);horizontal=vector(b,[ax[0],ax[1],0.])
    lv=o.v('NORMALIZE',o.v('ADD',lv,o.scale(horizontal,tilt)));lc=parts(b,lv)
    lv=o.v('NORMALIZE',vector(b,[lc[0],lc[1],.00000999999975]))
    ly=o.v('DOT_PRODUCT',inv_game[1],lv);lz=o.v('DOT_PRODUCT',inv_game[2],lv)
    # Exact atan2 polynomial emitted in VS22, rather than a fitted light angle.
    ay=o.m('ABSOLUTE',ly);az=o.m('ABSOLUTE',lz);t=o.div(o.m('MINIMUM',ay,az),o.m('MAXIMUM',ay,az));t2=o.mul(t,t)
    poly=o.add(o.mul(t2,.0208350997),-.0851330012)
    for a in (.180141002,-.330299497,.999866009):poly=o.add(o.mul(poly,t2),a)
    angle=o.mul(t,poly);angle=o.mix(o.m('LESS_THAN',ay,az),angle,o.sub(1.57079637,angle))
    angle=o.add(angle,o.mul(o.m('LESS_THAN',ly,0.),-3.14159298))
    flip_sign=o.mul(o.m('LESS_THAN',o.m('MINIMUM',o.mul(-1.,lz),ly),0.),o.sub(1.,o.m('LESS_THAN',o.m('MAXIMUM',o.mul(-1.,lz),ly),0.)))
    angle=o.mul(angle,o.sub(1.,o.mul(2.,flip_sign)));w=o.m('ABSOLUTE',o.mul(angle,.318309873));positive=o.m('GREATER_THAN',angle,0.)
    uv=b.node('ShaderNodeUVMap');uv.uv_map='UV0';u=parts(b,uv.outputs[0]);mirrored=vector(b,[o.mix(positive,u[0],o.sub(1.,u[0])),u[1],0.])
    a=b.node('ShaderNodeAttribute');a.attribute_name='zzz_face_uv3';uv3=parts(b,a.outputs['Vector']);s=o.mix(positive,uv3[0],uv3[1])
    flags=b.node('ShaderNodeAttribute');flags.attribute_name='zzz_face_flags';fv=parts(b,flags.outputs['Vector']);facebit=o.m('GREATER_THAN',fv[0],.5);code=fv[1]
    tex=b.tex(core.img(main_tex));b.link(uv.outputs[0],tex.inputs[0]);D=o.v('MULTIPLY',tex.outputs['Color'],tuple(p['_Color'][:3]));alpha=tex.outputs['Alpha']
    texL=b.tex(core.img(light_tex,True));texL.extension='EXTEND';b.link(uv.outputs[0],texL.inputs[0]);direct=parts(b,texL.outputs['Color'])
    texM=b.tex(core.img(light_tex,True));texM.extension='EXTEND';b.link(mirrored,texM.inputs[0]);mg=parts(b,texM.outputs['Color'])[1]
    s=o.mix(facebit,s,parts(b,texM.outputs['Color'])[0]);ao=o.mix(facebit,1.,texM.outputs['Alpha'])
    if p['_OverrideMainLight'][0]:L=o.v('NORMALIZE',o.cmix(ao,tuple(profile['override_body_world']),L))
    albedo_smooth=o.mix(o.m('GREATER_THAN',code,1.5),o.mix(o.m('GREATER_THAN',code,.5),scalar('_AlbedoSmoothness'),scalar('_AlbedoSmoothness2')),scalar('_AlbedoSmoothness3'))
    G=o.mix(o.mul(o.m('GREATER_THAN',mg,.400000006),o.m('LESS_THAN',mg,.949999988)),direct[1],mg)
    smooth=o.mix(o.sat(o.mul(o.sub(G,.5),2.5)),albedo_smooth,.0250000004)
    smooth=o.m('MAXIMUM',.00001,o.mul(smooth,o.mul(o.sub(1.,o.m('LESS_THAN',G,.400000006)),o.sub(1.,o.m('GREATER_THAN',G,.949999988)))))
    # Four face weights, then native entity redistribution (AO=1 cancels the
    # normal-driven body ramp in PS261 lines 442-454).
    # DXBC PS261: movc r0.xy, receiver, l(0,1), r0.xy. The coverage
    # provider owns depth/stencil validation; there is no implicit default mask.
    if stencil_coverage is not None:
        coverage=stencil_coverage(b)
        s=o.mix(coverage,s,0.);w=o.mix(coverage,w,1.)
    edge=o.add(.600000024,o.div(o.add(o.mul(s,1.20000005),-.600000024),o.add(o.mul(smooth,4.),1.)))
    t1=o.sub(edge,smooth);t2=o.sub(edge,o.mul(smooth,2.));t3=o.sub(t2,.125)
    bright=o.sat(o.div(o.sub(w,t1),smooth))
    a=o.m('MINIMUM',o.sat(o.div(o.sub(edge,w),smooth)),o.sat(o.div(o.sub(w,t2),smooth)))
    c=o.m('MINIMUM',o.sat(o.mul(o.sub(w,t3),8.)),o.sat(o.div(o.sub(t1,w),smooth)))
    d=o.sat(o.mul(o.sub(t2,w),8.))
    visibility=profile['visibility'];q=(1-max(0,min(1,1-2*visibility))*e[8])*e[13]
    mixq=q
    if p['_EntityInfo'][1]>.5:mixq=1+p['_AvatarMainLightColor'][3]*(q-1)
    lit=o.mul(d,mixq*e[7]);front=o.mul(c,mixq*e[7]);sss=o.mul(a,mixq*e[7])
    fade=o.add(o.mul(bright,q),o.mul(o.add(o.add(a,c),d),1-mixq*e[7]))
    shadow=o.mul(bright,(1-q)*e[13]);shadowfade=o.mul(bright,(1-q)*(1-e[13]))
    # Native nose line tint and view threshold. No UV sliding or nose gain.
    hsv=nose_hsv(b,D);tint=o.v('MULTIPLY',hsv,tuple(p['_OutlineColor3'][:3]));tint=o.cmix(alpha,tint,(1.,1.,1.))
    threshold=o.mix(o.sat(o.mul(o.add(o.v('DOT_PRODUCT',inv_game[0],view),.850000024),1.33333337)),scalar('_NoseLineLkDnDisp'),scalar('_NoseLineHoriDisp'))
    gate=o.sat(o.mul(o.sub(o.v('DOT_PRODUCT',inv_game[1],view),threshold),50.))
    mask=o.sub(1.,o.m('LESS_THAN',gate,alpha));surface=o.v('MULTIPLY',D,o.cmix(mask,(1.,1.,1.),tint))
    # Colour tints are native GPU constants, explicitly scoped in the profile.
    distance=b.node('ShaderNodeCameraData').outputs['View Distance'];dw=o.m('MINIMUM',1.,o.mul(distance,.437249988))
    def shadow_color(key):
        raw=o.v('ADD',pick(key,code),(.0000610351562,)*3);mean=o.v('DOT_PRODUCT',raw,(.333330005,)*3)
        norm=o.v('MINIMUM',o.scale(raw,o.div(1.,mean)),(1.,1.,1.));return o.cmix(dw,norm,raw)
    sh=shadow_color('_ShallowColor');deep=shadow_color('_ShadowColor')
    va=b.node('ShaderNodeAttribute');va.attribute_name='zzz_face_alpha';skin=o.mul(o.m('LESS_THAN',va.outputs['Fac'],.5),o.m('LESS_THAN',code,.25))
    def tint_color(kind):return o.cmix(skin,tuple(p['_Post'+kind+'Tint'][:3]),tuple(p['_Skin'+kind+'Tint'][:3]))
    ramp=o.v('ADD',o.scale(o.v('MULTIPLY',sh,tint_color('ShallowFade')),fade),o.scale(o.v('MULTIPLY',deep,tint_color('Shadow')),shadow))
    ramp=o.v('ADD',ramp,o.scale(o.v('MULTIPLY',deep,tint_color('ShadowFade')),shadowfade))
    light,att=native_environment.light(b,pos,env);light=o.scale(light,o.add(att,o.mul(e[8],o.sub(1.,att))))
    rgb=parts(b,light);mx=o.m('MAXIMUM',o.m('MAXIMUM',rgb[0],rgb[1]),rgb[2]);normalized=o.scale(light,o.div(1.,o.m('MAXIMUM',1.,mx)))
    ramp=o.v('ADD',o.v('MULTIPLY',normalized,ramp),o.v('MULTIPLY',light,o.v('ADD',o.v('ADD',o.scale(tint_color('Front'),front),o.scale(tint_color('Sss'),sss)),vector(b,[lit]*3))))
    # Face AO mixes the complete normal-driven ramp with the face weights.
    from . import native_lighting
    ny=parts(b,normal)[2];lyworld=parts(b,L)[2]
    normal_signal=o.sub(o.mul(o.add(1.,o.v('DOT_PRODUCT',normal,L)),o.sat(o.add(1.5,o.sub(ny,o.mul(.5,o.m('MINIMUM',1.,o.mul(3.,lyworld))))))),1.)
    globals_={'entity_info_y':p['_EntityInfo'][1]>.5,'entity_7':e[7],'entity_8':e[8],'entity_13':e[13],'avatar_light_alpha':p['_AvatarMainLightColor'][3]}
    for short,native in [('shadow_fade','ShadowFade'),('shadow','Shadow'),('shallow_fade','ShallowFade'),('shallow','Shallow'),('sss','Sss'),('front','Front')]:
        for prefix,nativeprefix in [('post','Post'),('skin','Skin')]:globals_[prefix+'_'+short]=p['_'+nativeprefix+native+'Tint'][:3]
    rawlight,rawatt=native_environment.light(b,pos,env)
    body=native_lighting.shadow(b,normal_signal,albedo_smooth,visibility,skin,sh,deep,1.,{'native_globals':globals_,'post_shadow':(1.,1.,1.)},rawlight,rawatt)
    ramp=o.cmix(ao,body,ramp)
    hl1=o.v('DOT_PRODUCT',inv_game[1],L);hl2=o.v('DOT_PRODUCT',inv_game[2],L);den=o.m('SQRT',o.add(o.mul(hl1,hl1),o.mul(hl2,hl2)));hl2=o.div(hl2,den)
    h=o.mul(o.m('GREATER_THAN',hl1,0.),o.m('MINIMUM',o.sat(o.mul(o.add(hl2,.800000012),3.33333325)),o.sat(o.mul(o.sub(hl2,.800000012),-5.00000048))))
    h=o.mul(h,o.sat(o.mul(o.sub(o.m('ABSOLUTE',o.v('DOT_PRODUCT',inv_game[2],view)),1.20000005),-.909090877)))
    rr=parts(b,ramp);maximum=o.m('MAXIMUM',o.m('MAXIMUM',rr[0],rr[1]),rr[2]);amp=o.sat(o.add(o.mul(maximum,visibility*.399999976),.600000024))
    ramp=o.v('ADD',ramp,o.scale(tuple(p['_ShallowColor'][:3]),o.mul(o.mul(h,direct[2]),amp)))
    # Native non-skin curve. In this variant geometry N equals interpolated N.
    ndl=o.v('DOT_PRODUCT',normal,L);yn=o.mul(.5,o.add(o.sat(ndl),o.add(o.mul(.5,ndl),.5)))
    power=o.add(1.4375,o.mul(o.v('DOT_PRODUCT',surface,(.289999992,.600000024,.109999999)),.287499994));power=o.mix(yn,power,1.)
    sr=parts(b,surface);largest=o.m('MAXIMUM',1.,o.m('MAXIMUM',o.m('MAXIMUM',sr[0],sr[1]),sr[2]));curved=vector(b,[o.pow(o.div(x,largest),power) for x in sr])
    surface=o.cmix(skin,o.cmix(o.add(.5,o.mul(.5,o.sat(ndl))),surface,curved),surface)
    color=o.v('ADD',o.v('MULTIPLY',o.scale(surface,.959999979),ramp),o.v('MULTIPLY',tuple(env['ambient_color']),surface))
    debug=profile.get('diagnostic')
    if debug=='signals':color=vector(b,[s,w,skin])
    if debug=='surface':color=surface
    if debug=='ramp':color=ramp
    em=b.node('ShaderNodeEmission');b.link(color,em.inputs['Color']);out=b.node('ShaderNodeOutputMaterial');b.link(em.outputs[0],out.inputs['Surface'])
    for key,value in [('zzz_lut_weight',1.),('zzz_view_z',b.node('ShaderNodeCameraData').outputs['View Z Depth']),('zzz_face_sdf',s),('zzz_face_angle',w)]:
        n=b.node('ShaderNodeOutputAOV');n.aov_name=key
        if isinstance(value,float):n.inputs['Value'].default_value=value
        else:b.link(value,n.inputs['Value'])
    mat['zzz_native_face_scope']='UI Face base pass' + (' with caller-supplied stencil coverage' if stencil_coverage is not None else '; missing stencil coverage explicitly excluded')
    return mat
