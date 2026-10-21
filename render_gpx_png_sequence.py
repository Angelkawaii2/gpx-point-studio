#!/usr/bin/env python3
"""GPX point-by-point PNG renderer and local web application."""
from __future__ import annotations
import argparse
from datetime import datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from urllib.request import Request, urlopen
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.tile-cache'
STYLES = {'default': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'dark': 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', 'light': 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'}
tracks, jobs = {}, {}
ENV_FILE = ROOT / '.env'
config_lock = threading.Lock()


def load_map_key():
    value = ''
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip().startswith('API_KEY_CARTO='):
                value = line.strip().split('=', 1)[1].strip().strip('\"\'')
    return os.environ.get('API_KEY_CARTO', value)


MAP_API_KEY = load_map_key()


def save_map_key(value):
    global MAP_API_KEY
    if not isinstance(value, str) or len(value) > 512 or any(c in value for c in '\r\n\x00'):
        raise ValueError('API key 格式无效')
    value = value.strip()
    with config_lock:
        lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
        lines = [line for line in lines if not line.strip().startswith('API_KEY_CARTO=')]
        lines.append('API_KEY_CARTO=' + value)
        temp = ENV_FILE.with_name('.env.' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write('\n'.join(lines) + '\n')
            temp.replace(ENV_FILE)
        finally:
            temp.unlink(missing_ok=True)
        MAP_API_KEY = value
        # A webpage update replaces an inherited value for this process as well.
        if 'API_KEY_CARTO' in os.environ:
            os.environ['API_KEY_CARTO'] = value
        tile.cache_clear()


def tile_url(style, z, x, y):
    url = STYLES[style].format(z=z, x=x, y=y)
    if style != 'default' and MAP_API_KEY:
        url += '?' + urlencode({'key': MAP_API_KEY})
    return url

def parse_gpx(data):
    root = ET.fromstring(data)
    points = []
    segment = 0
    for group in root.iter():
        if group.tag.split('}')[-1] not in ('trkseg', 'rte'):
            continue
        for node in group:
            if node.tag.split('}')[-1] not in ('trkpt', 'rtept'):
                continue
            lat, lon = float(node.attrib['lat']), float(node.attrib['lon'])
            if not math.isfinite(lat + lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError('GPX 包含无效经纬度')
            stamp = next((c.text for c in node if c.tag.split('}')[-1] == 'time'), None)
            ts = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() if stamp else None
            points.append(dict(lat=lat, lon=lon, time=stamp, timestamp=ts, segment=segment))
        segment += 1
    if not points:
        raise ValueError('GPX 中没有轨迹点')
    # Untimed tracks use point indices; original order and duplicate points are preserved.
    timed = all(p['timestamp'] is not None for p in points)
    timed = timed and all(b['timestamp'] >= a['timestamp'] for a,b in zip(points,points[1:]))
    for i, p in enumerate(points):
        p['offset'] = p['timestamp'] - points[0]['timestamp'] if timed else i
    return dict(points=points, timed=timed)

def project(lat, lon, zoom):
    lat = max(-85.05112878, min(85.05112878, lat))
    s = 256 * 2 ** zoom
    return ((lon + 180) / 360 * s, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * s)

@lru_cache(maxsize=512)
def tile(style, z, x, y):
    from PIL import Image
    n = 2 ** z
    if not 0 <= y < n:
        return Image.new('RGB', (256,256), '#d9e1e5')
    x %= n
    path = CACHE / style / str(z) / str(x) / f'{y}.png'
    if path.exists():
        with Image.open(path) as im: return im.convert('RGB')
    url = tile_url(style,z,x,y)
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'GPXPointStudio/1.0 (local GPX PNG renderer)'}), timeout=25) as response:
                data = response.read()
            with Image.open(BytesIO(data)) as im: result = im.convert('RGB')
            path.parent.mkdir(parents=True,exist_ok=True)
            temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            temp.write_bytes(data)
            temp.replace(path)
            return result
        except Exception:
            if attempt == 2:
                raise RuntimeError('地图瓦片请求失败，请检查网络、地图服务和 API key') from None
            time.sleep(attempt + 1)

def settings(value):
    z, w, h = int(value.get('zoom',14)), int(value.get('width',600)), int(value.get('height',600))
    style = value.get('style','default')
    if not 1 <= z <= 19 or not 128 <= w <= 4096 or not 128 <= h <= 4096 or style not in STYLES:
        raise ValueError('缩放等级须为 1–19，宽高须为 128–4096，地图样式无效')
    return z,w,h,style

def render(points, index, config):
    from PIL import Image, ImageDraw
    z,w,h,style = settings(config)
    cx,cy = project(points[index]['lat'],points[index]['lon'],z)
    left,top = round(cx-w/2), round(cy-h/2)
    im = Image.new('RGB',(w,h))
    for ty in range(top//256,(top+h-1)//256+1):
        for tx in range(left//256,(left+w-1)//256+1):
            im.paste(tile(style,z,tx,ty),(tx*256-left,ty*256-top))
    draw = ImageDraw.Draw(im)
    coords = []
    previous = None
    world = 256*2**z
    for p in points:
        x,y = project(p['lat'],p['lon'],z)
        x = cx + (x-cx+world/2)%world-world/2
        if previous != p['segment']:
            if len(coords)>1: draw.line(coords,fill='#ff694b',width=4)
            coords=[]
        coords.append((x-left,y-top)); previous=p['segment']
    if len(coords)>1: draw.line(coords,fill='#ff694b',width=4)
    x,y=cx-left,cy-top
    draw.ellipse((x-9,y-9,x+9,y+9),fill='#29b5ff',outline='white',width=3)
    credit = '© OpenStreetMap contributors' + (' © CARTO' if style != 'default' else '')
    box = draw.textbbox((0,0),credit)
    draw.rectangle((0,h-20,box[2]+12,h),fill='white')
    draw.text((6,h-16),credit,fill='black')
    out=BytesIO(); im.save(out,format='PNG'); return out.getvalue()

def export_job(job, track, config, start, end):
    try:
        folder=ROOT/'.exports'; folder.mkdir(exist_ok=True)
        path=folder/f'{job}.zip'
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for number,index in enumerate(range(start,end+1),1):
                archive.writestr(f'frame_{number:06d}.png',render(track['points'],index,config))
                jobs[job]['done']=number
            archive.writestr('manifest.json',json.dumps(dict(settings=config,start=start,end=end,points=track['points'][start:end+1]),ensure_ascii=False))
        jobs[job].update(status='complete',path=str(path))
    except Exception as exc:
        jobs[job].update(status='error',error=str(exc))

class Handler(BaseHTTPRequestHandler):
    def send(self,data,status=200,content='application/json'):
        if content=='application/json': data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',content); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        try:
            path=self.path.split('?')[0]
            if path == '/api/config':
                self.send({'carto_key_configured': bool(MAP_API_KEY)}); return
            if path in ('/','/app.js','/style.css'):
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path]
                self.send((ROOT/'web'/name).read_bytes(),content={'/':'text/html; charset=utf-8','/app.js':'text/javascript','/style.css':'text/css'}[path]); return
            parts=path.strip('/').split('/')
            if len(parts)==6 and parts[:2]==['api','tiles']:
                style=parts[2]; z,x,y=int(parts[3]),int(parts[4]),int(parts[5].removesuffix('.png'))
                if style not in STYLES or not 1<=z<=19 or not 0<=y<2**z:
                    raise ValueError('地图瓦片参数无效')
                image=BytesIO(); tile(style,z,x,y).save(image,format='PNG')
                self.send(image.getvalue(),content='image/png'); return
            if len(parts)==3 and parts[:2]==['api','jobs']:
                job=jobs[parts[2]]; self.send({k:v for k,v in job.items() if k!='path'}); return
            if len(parts)==3 and parts[:2]==['api','download']:
                job=jobs[parts[2]]
                if job['status']!='complete': raise ValueError('导出尚未完成')
                file=Path(job['path'])
                self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition','attachment; filename="gpx-png-sequence.zip"'); self.send_header('Content-Length',str(file.stat().st_size)); self.end_headers()
                with file.open('rb') as f:
                    while chunk:=f.read(1024*1024): self.wfile.write(chunk)
                return
            self.send({'error':'Not found'},404)
        except (KeyError,ValueError,RuntimeError) as exc: self.send({'error':str(exc)},400)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length',0))
            raw=self.rfile.read(length)
            if self.path=='/api/import':
                track=parse_gpx(raw); key=uuid.uuid4().hex; tracks[key]=track
                self.send(dict(id=key,**track)); return
            data=json.loads(raw)
            if self.path == '/api/config':
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                    raise ValueError('配置请求必须使用 JSON')
                if self.headers.get('Origin') not in (None, 'http://' + self.headers.get('Host','')):
                    raise ValueError('仅允许从本机网页修改配置')
                save_map_key(data['carto_api_key'])
                self.send({'carto_key_configured': bool(MAP_API_KEY)}); return
            track=tracks[data['id']]; settings(data)
            if self.path=='/api/preview':
                index=int(data['index'])
                if not 0<=index<len(track['points']): raise ValueError('点位超出范围')
                self.send(render(track['points'],index,data),content='image/png'); return
            if self.path=='/api/export':
                start,end=int(data.get('start',0)),int(data.get('end',len(track['points'])-1))
                if not 0<=start<=end<len(track['points']): raise ValueError('导出范围无效')
                if any(j['status']=='running' for j in jobs.values()): raise ValueError('已有导出任务正在运行')
                key=uuid.uuid4().hex; jobs[key]=dict(status='running',done=0,total=end-start+1)
                threading.Thread(target=export_job,args=(key,track,data,start,end),daemon=True).start()
                self.send(dict(id=key)); return
            self.send({'error':'Not found'},404)
        except Exception as exc: self.send({'error':str(exc)},400)

def main():
    parser=argparse.ArgumentParser(description='默认启动网页；指定 GPX 和输出目录则每个原始点输出一张 PNG。')
    parser.add_argument('gpx',type=Path,nargs='?'); parser.add_argument('output_dir',type=Path,nargs='?')
    parser.add_argument('--zoom',type=int,default=14); parser.add_argument('--size',default='600x600')
    parser.add_argument('--map-style',choices=STYLES,default='default'); parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if args.gpx:
        if not args.output_dir: parser.error('请指定输出目录')
        try:
            w,h=map(int,args.size.lower().split('x')); config=dict(zoom=args.zoom,width=w,height=h,style=args.map_style); settings(config)
            track=parse_gpx(args.gpx.read_bytes())
            args.output_dir.mkdir(parents=True,exist_ok=True)
            if any(args.output_dir.iterdir()): raise ValueError('输出目录必须为空')
            for i in range(len(track['points'])):
                (args.output_dir/f'frame_{i+1:06d}.png').write_bytes(render(track['points'],i,config)); print(f"{i+1}/{len(track['points'])}")
        except Exception as exc: parser.exit(1,f'{exc}\n')
    else:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        print(f'GPX Point Studio: http://127.0.0.1:{args.port}',flush=True)
        if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{args.port}')
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()

if __name__=='__main__': main()
