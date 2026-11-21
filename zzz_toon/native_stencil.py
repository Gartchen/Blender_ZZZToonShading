"""NapStencilShadowCaster VS bcddceab76ee10ce, transcribed from DXBC.

Pure projection math. All runtime inputs must be provided; no character defaults.
Coverage/stencil state is a separate stage and is not inferred by this module.
"""
import numpy as np


def project(position, red, inputs):
    p=inputs;pos=np.asarray(position,dtype=np.float64);red=np.asarray(red,dtype=np.float64)
    if pos.ndim!=2 or pos.shape[1]!=3 or red.shape!=(len(pos),):raise ValueError('Invalid caster streams')
    M=np.asarray(p['unity_ObjectToWorld']).reshape(4,4).T
    VP=np.asarray(p['unity_MatrixVP']).reshape(4,4).T
    axis=M[:3,2];axis=axis/max(np.linalg.norm(axis),np.sqrt(np.finfo(np.float32).tiny))
    packed=np.asarray(p['_PackedParams0']);light=np.asarray(p['_AvatarMainLightPosition'][:3])
    if packed[3]>0:light=packed[:3]
    # DXBC itof reads an integer uniform, not the reinterpreted float value.
    if p['_OverrideHairShadowMainLight'][0]>.5:light=np.asarray(p['_OverrideHairShadowMainLightDir'][:3])
    world=pos@M[:3,:3].T+M[:3,3]
    view=np.asarray(p['_WorldSpaceCameraPos'])-world
    view/=np.maximum(np.linalg.norm(view,axis=1,keepdims=True),1e-30)
    world+=view*(red*p['_ZOffset'][0])[:,None]
    clip=np.c_[world,np.ones(len(world))]@VP.T
    moved=world+(red*np.dot(axis,light)*8)[:,None]*light
    delta=(np.c_[moved,np.ones(len(moved))]@VP.T)[:,:2]-clip[:,:2]
    shift=np.sign(delta)*(1-np.exp2(-abs(delta)*.100000001))*p['_Length'][0]*np.sign(np.dot(-axis,light))
    offset=np.array([p['_XOffsetNew'][0],p['_YOffset'][0]])
    offset+=packed[3]*(np.array([p['_XOffsetEnter'][0],p['_YOffsetEnter'][0]])-offset)
    if p['_StencilShadowBlendDebugMode'][0]<.5:
        clip[:,:2]+=shift+red[:,None]*offset+np.asarray(p['_ClipSpaceOffset'][:2])*clip[:,3:4]
    if not np.isfinite(clip).all():raise ValueError('Nonfinite projected caster')
    return clip
