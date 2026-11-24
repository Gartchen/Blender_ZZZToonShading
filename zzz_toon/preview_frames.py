"""Bounded RGBA uploads and a delete-on-close store for the current camera's foreground."""
import tempfile
import zlib

import gpu
import numpy as np
try:
    import zstandard
except ImportError:
    zstandard=None


class FrameUpload:
    def __init__(self):
        self.texture=None
        self.buffer=None
        self.shader=None

    def put(self,pixels):
        h,w,_=pixels.shape
        if self.texture is None or (w,h)!=(self.texture.width,self.texture.height):
            self.texture=gpu.types.GPUTexture((w,h),format='RGBA8')
            self.buffer=None
        if hasattr(self.texture,'update'):
            self.texture.update(gpu.types.Buffer('UBYTE',pixels.shape,pixels))
        else:
            if self.shader is None:
                info=gpu.types.GPUShaderCreateInfo()
                info.local_group_size(16,16)
                info.storage_buf(0,'uint','rgba[]',qualifiers={'READ'})
                info.image(0,'RGBA8','FLOAT_2D','dest',qualifiers={'WRITE'})
                info.compute_source('void main(){ivec2 p=ivec2(gl_GlobalInvocationID.xy),s=imageSize(dest);if(all(lessThan(p,s)))imageStore(dest,p,unpackUnorm4x8(rgba[p.y*s.x+p.x]));}')
                self.shader=gpu.shader.create_from_info(info)
            if self.buffer is None:
                self.buffer=gpu.types.GPUStorageBuf(pixels)
            else:
                self.buffer.update(pixels)
            self.shader.bind()
            self.shader.storage_block('rgba',self.buffer)
            self.shader.image('dest',self.texture)
            gpu.compute.dispatch(self.shader,(w+15)//16,(h+15)//16,1)
        return self.texture

    def close(self):
        self.buffer=self.texture=self.shader=None


def crop(pixels):
    # The viewport output is display-encoded. Ignore sub-quantization dither
    # on transparent black, but retain additive highlights with zero alpha.
    # Little-endian RGBA8: test all alpha bits and bits 1..7 of each RGB
    # channel in one pass over the pixel buffer.
    visible=pixels.view('<u4')[:,:,0] & np.uint32(0xfffefefe)
    xs=np.flatnonzero(visible.any(axis=0))
    ys=np.flatnonzero(visible.any(axis=1))
    if not len(xs):return (0,0,1,1)
    return (int(xs[0]),int(ys[0]),int(xs[-1]+1-xs[0]),int(ys[-1]+1-ys[0]))


class ViewFrames:
    def __init__(self):
        self.file=None
        self.entries={}
        self.bytes=0
        self.full=False
        self.encoder=zstandard.ZstdCompressor(level=1) if zstandard else None
        self.decoder=zstandard.ZstdDecompressor() if zstandard else None

    def add(self,index,pixels):
        if index in self.entries or self.full:
            return
        x,y,w,h=crop(pixels)
        raw=np.ascontiguousarray(pixels[y:y+h,x:x+w])
        data=self.encoder.compress(raw) if self.encoder else zlib.compress(raw,1)
        if self.bytes+len(data)>512*1024**2:
            self.full=True
            return
        if self.file is None:
            self.file=tempfile.TemporaryFile('w+b')
        self.file.seek(self.bytes)
        if self.file.write(data)!=len(data):
            raise OSError('Incomplete preview frame write')
        self.entries[index]=(self.bytes,len(data),(x,y,w,h))
        self.bytes+=len(data)

    def read(self,index):
        pos,size,rect=self.entries[index]
        self.file.seek(pos)
        _,_,w,h=rect
        data=self.file.read(size)
        raw=self.decoder.decompress(data,max_output_size=w*h*4) if self.decoder else zlib.decompress(data)
        return rect,np.frombuffer(raw,np.uint8).reshape(h,w,4)

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file=None
        self.entries.clear()
        self.bytes=0
        self.full=False

