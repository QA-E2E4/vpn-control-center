from pathlib import Path
from io import BytesIO
import struct
import cairosvg
from PIL import Image
root=Path(__file__).resolve().parent/'icons'
root.mkdir(exist_ok=True)
colors={'main':('#B7EF88','#13231A'),'offline':('#94A3B8','#17202D'),'connecting':('#FFD166','#332708')}
for name,(bg,fg) in colors.items():
 svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="1" y="1" width="30" height="30" rx="8" fill="{bg}"/><path d="M12 7h14l-1.2 4.5h-9l-1 4H22l-1.2 4.5h-7.2L12 26H6.8Z" fill="{fg}"/></svg>'
 (root/(name+'.svg')).write_text(svg)
 png=cairosvg.svg2png(bytestring=svg.encode(),output_width=256,output_height=256)
 im=Image.open(BytesIO(png)).convert('RGBA')
 sizes=[16,20,24,32,40,48,64,128,256]
 frames=[]
 for size in sizes:
  tile=im.resize((size,size),Image.Resampling.LANCZOS)
  rgba=tile.tobytes();xor=bytearray();mask=bytearray();stride=((size+31)//32)*4
  for y in range(size-1,-1,-1):
   row=bytearray(stride)
   for x in range(size):
    red,green,blue,alpha=rgba[(y*size+x)*4:(y*size+x)*4+4]
    xor.extend((blue,green,red,alpha))
    if alpha==0:row[x//8]|=1<<(7-x%8)
   mask.extend(row)
  header=struct.pack('<IiiHHIIiiII',40,size,size*2,1,32,0,len(xor)+len(mask),0,0,0,0)
  frames.append(header+xor+mask)
 offset=6+16*len(sizes);entries=bytearray()
 for size,frame in zip(sizes,frames):
  entries.extend(struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(frame),offset));offset+=len(frame)
 (root/(name+'.ico')).write_bytes(struct.pack('<HHH',0,1,len(sizes))+entries+b''.join(frames))
 if name=='main':
  (root/'favicon.png').write_bytes(cairosvg.svg2png(bytestring=svg.encode(),output_width=64,output_height=64))
# A contact sheet for visual review of the actual rendered icons.
canvas=Image.new('RGB',(420,180),'#10151E')
for i,name in enumerate(colors):
 im=Image.open(root/(name+'.ico')).convert('RGBA').resize((72,72),Image.Resampling.LANCZOS)
 canvas.paste(im,(35+i*140,30),im)
 small=Image.open(root/(name+'.ico'));small.size=(16,16)
 small=small.convert('RGBA');canvas.paste(small,(62+i*140,130),small)
canvas.save(root/'preview.png')
