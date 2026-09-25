"""Encode captured workflow folders as compact looping GIFs. Requires Pillow."""
import argparse
from pathlib import Path
from PIL import Image
p=argparse.ArgumentParser();p.add_argument('capture',type=Path);p.add_argument('--output',type=Path,default=Path('docs/media'));a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
for folder in sorted(a.capture.iterdir()):
 paths=sorted(folder.glob('*.png'))
 if not paths:continue
 frames=[Image.open(f).convert('RGB').resize((1080,720),Image.Resampling.LANCZOS) for f in paths]
 indices=sorted(set([0,len(frames)//4,len(frames)//2,len(frames)*3//4,len(frames)-1]))
 atlas=Image.new('RGB',(360*len(indices),240))
 for i,index in enumerate(indices):atlas.paste(frames[index].resize((360,240)),(360*i,0))
 palette=atlas.quantize(colors=224)
 encoded=[f.quantize(palette=palette,dither=Image.Dither.NONE) for f in frames]
 target=a.output/(folder.name+'.gif')
 encoded[0].save(target,save_all=True,append_images=encoded[1:],loop=0,duration=90,optimize=True,disposal=1)
 frames[len(frames)//2].save(a.output/(folder.name+'.png'))
 with Image.open(target) as check:
  duration=0
  for i in range(check.n_frames):check.seek(i);duration+=check.info.get('duration',0)
 print(target.name,len(frames),'frames',duration/1000,'s',round(target.stat().st_size/1024),'KB')
