"""Read typed Odin fields from AnimeStudio UIAvatarShowSettings raw exports."""
import base64
import struct

class Reader:
    def __init__(self,b,start):self.b=b;self.p=start;self.types={};self.events=0
    def take(self,n):
        if n<0 or self.p+n>len(self.b):raise ValueError("Truncated Odin data")
        v=self.b[self.p:self.p+n];self.p+=n;return v
    def num(self,fmt):return struct.unpack('<'+fmt,self.take(struct.calcsize('<'+fmt)))[0]
    def string(self):
        wide=self.num('B');n=self.num('i')
        if wide not in (0,1) or not 0<=n<1000000:raise ValueError('Invalid Odin string')
        return self.take(n*(1+wide)).decode('utf-16le' if wide else 'latin1')
    def typ(self):
        tag=self.num('B')
        if tag==0x2e:return None
        if tag not in (0x2f,0x30):raise ValueError('Unknown Odin type')
        i=self.num('i')
        if tag==0x2f:self.types[i]=self.string()
        return self.types[i]
    def children(self,end):
        a=[]
        while self.b[self.p]!=end:a.append(self.entry())
        self.p+=1;return a
    def entry(self):
        pos=self.p;tag=self.num('B');self.events+=1
        named=tag in [1,3,9,11,13,50] or (15<=tag<=45 and tag%2==1)
        o={'offset':pos,'tag':tag,'name':self.string() if named else None}
        if tag in (1,2,3,4):
            o['type']=self.typ()
            if tag in (1,2):o['ref_id']=self.num('i')
            o['children']=self.children(5)
        elif tag==6:
            o['length']=self.num('q');o['children']=self.children(7)
            if len(o['children'])!=o['length']:raise ValueError('Invalid Odin array length')
        elif tag==8:
            n=self.num('i');size=self.num('i');o['length']=n;o['element_bytes']=size;o['raw_base64']=base64.b64encode(self.take(n*size)).decode()
        elif tag in (9,10,11,12):o['value']=self.num('i')
        elif tag in (13,14,35,36,41,42):o['value_hex']=self.take(16).hex()
        elif 15<=tag<=34:
            fmts={15:'b',17:'B',19:'h',21:'H',23:'i',25:'I',27:'q',29:'Q',31:'f',33:'d'}
            o['value']=self.num(fmts[tag if tag%2 else tag-1])
        elif tag in (37,38):o['value']=chr(self.num('H'))
        elif tag in (39,40,50,51):o['value']=self.string()
        elif tag in (43,44):o['value']=bool(self.num('B'))
        elif tag in (45,46):o['value']=None
        else:raise ValueError((pos,hex(tag)))
        o['end']=self.p;return o

def simplify(node):
    if 'children' not in node:
        if node['tag'] in (9,10,11,12):return {'reference_kind':node['tag'],'index':node['value']}
        return node.get('value',node.get('value_hex',node.get('raw_base64')))
    if node['tag']==6:return [simplify(x) for x in node['children']]
    d={'$type':node.get('type')};unnamed=[]
    for child in node['children']:
        if child['name'] is None:unnamed.append(simplify(child))
        else:
            if child['name'] in d:raise ValueError('Duplicate Odin field')
            d[child['name']]=simplify(child)
    if unnamed:d['$items']=unnamed
    return d

def expand_references(tree):
    refs={}
    def collect(n):
        if 'ref_id' in n:refs[n['ref_id']]=n
        for c in n.get('children',[]):collect(c)
    for n in tree:collect(n)
    def expand(n,stack=()):
        if n['tag'] in (9,10):
            rid=n['value']
            if rid not in refs:raise ValueError('Missing internal Odin reference')
            if rid in stack:return {'cyclic_reference':rid}
            return expand(refs[rid],stack+(rid,))
        copy=dict(n)
        if 'children' in n:
            # Preserve the original typed tree separately; this view resolves aliases.
            vals=[(c['name'],expand(c,stack)) for c in n['children']]
            if n['tag']==6:return [v for _,v in vals]
            result={'$type':n.get('type')};unnamed=[]
            for name,value in vals:
                if name is None:unnamed.append(value)
                else:result[name]=value
            if unnamed:result['$items']=unnamed
            return result
        return simplify(copy)
    return {n['name']:expand(n) for n in tree}

