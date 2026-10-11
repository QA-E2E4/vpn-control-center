"""Raster fallback of the code-native shield icon, using standard PNG encoding."""
from pathlib import Path
import struct
import zlib

def inside(x,y,points):
    ok=False
    j=len(points)-1
    for i,(a,b) in enumerate(points):
        c,d=points[j]
        if (b>y)!=(d>y) and x<(c-a)*(y-b)/(d-b)+a:
            ok=not ok
        j=i
    return ok

def build(path):
    shield=[(32,8),(53,17),(53,34),(49,43),(41,51),(32,57),(23,51),(15,43),(11,34),(11,17)]
    mark=[(24,20),(44,20),(41,27),(31,27),(29,33),(39,33),(36,40),(27,40),(24,50),(16,50)]
    pixels=bytearray()
    for y in range(64):
        pixels.append(0)
        for x in range(64):
            color=(180,237,134,255) if inside(x+.5,y+.5,shield) and not inside(x+.5,y+.5,mark) else (16,21,30,255)
            pixels.extend(color)
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',64,64,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))

if __name__=='__main__':
    build(Path(__file__).parent/'static/favicon.png')
