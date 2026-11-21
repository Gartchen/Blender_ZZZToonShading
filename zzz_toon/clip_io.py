"""Read the baked AnimationClip YAML emitted by AnimeStudio, or its parsed JSON form."""
import json
import hashlib
import math
import re
from pathlib import Path

SECTIONS={'m_RotationCurves':'rot','m_PositionCurves':'pos','m_ScaleCurves':'scl','m_FloatCurves':'float'}
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
VECTOR=re.compile(r'[xyzw]: ('+NUMBER+r')')


def curve_signature(clip):
    tracks={k:clip[k] for k in SECTIONS.values()}
    return hashlib.sha256(json.dumps(tracks,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load(path):
    path=Path(path)
    with path.open(encoding='utf-8-sig') as stream:
        if path.suffix.lower()=='.json':
            data=json.load(stream)
        elif path.suffix.lower()=='.anim':
            data={key:{} for key in SECTIONS.values()}
            section=None
            for line in stream:
                s=line.strip()
                if line.startswith('  m_'):
                    field,_,value=s.partition(':')
                    section=SECTIONS.get(field)
                    if field=='m_SampleRate':
                        data['rate']=float(value)
                    elif field=='m_Name':
                        data['name']=value.strip()
                    elif field in ('m_EulerCurves','m_CompressedRotationCurves') and value.strip()!='[]':
                        raise ValueError('Export baked quaternion curves from AnimeStudio')
                elif line.startswith('    m_StopTime:'):
                    data['stop']=float(s.split(':',1)[1])
                elif section:
                    if s.startswith('- curve:'):
                        keys=[]
                        attr=''
                    elif s.startswith('time:'):
                        time=float(s.split(':',1)[1])
                    elif s.startswith('value:'):
                        values=([float(s.split(':',1)[1])] if section=='float' else list(map(float,VECTOR.findall(s))))
                        keys.append([time,*values])
                    elif s.startswith('attribute:'):
                        attr=s.split(':',1)[1].strip()
                    elif s.startswith('path:'):
                        name=s.split(':',1)[1].strip()
                        data[section][name+'|'+attr if section=='float' else name]=keys
        else:
            raise ValueError('Choose an AnimeStudio .anim or a parsed AnimationClip .json')
    if not isinstance(data,dict) or not data.get('rot'):
        raise ValueError('Animation contains no baked skeletal rotations')
    for key,width in [('rot',5),('pos',4),('scl',4),('float',2)]:
        curves=data.setdefault(key,{})
        for name,keys in curves.items():
            if (not isinstance(name,str) or any(len(row)!=width or not all(map(math.isfinite,row)) for row in keys)
                    or any(a[0]>b[0] for a,b in zip(keys,keys[1:]))):
                raise ValueError('Invalid animation curve: '+str(name))
    stop=data.get('stop') or max(rows[-1][0] for rows in data['rot'].values() if rows)
    if not math.isfinite(stop) or stop<=0:
        raise ValueError('Expected a positive animation duration')
    data['stop']=stop
    return data

