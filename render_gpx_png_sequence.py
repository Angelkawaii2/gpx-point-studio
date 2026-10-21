#!/usr/bin/env python3
"""GPX point-by-point PNG renderer and local web application."""
from __future__ import annotations
import argparse
from bisect import bisect_right
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import hashlib
import re
from statistics import median
import os
from pathlib import Path
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.tile-cache'
STYLES = {'default': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'dark': 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', 'light': 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'}
tracks, jobs = {}, {}
ENV_FILE = ROOT / '.env'
config_lock = threading.Lock()
cache_lock = threading.Lock()
tile_locks = [threading.Lock() for _ in range(64)]
cache_counters = dict(disk_hits=0, downloads=0, errors=0, active=0, last_error='')
_inventory = dict(time=0, files=0, bytes=0, by_style={})
_KEY_DEFAULT = object()
# CARTO can return these authentication placeholders with HTTP 200.
AUTH_TILE_HASHES = {
    'd36df23c63879d10e90e52f7ccd86148998030867958151e8e8d026a7338b47b',
    '4adce5fc1b6b53c4ac03387defd4120b6ebdcd188be696649ae851b2e4f53db8',
}


class TileAuthorizationError(RuntimeError):
    pass


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


def tile_url(style, z, x, y, api_key=_KEY_DEFAULT):
    api_key = MAP_API_KEY if api_key is _KEY_DEFAULT else api_key
    url = STYLES[style].format(z=z, x=x, y=y)
    if style != 'default' and api_key:
        url += '?' + urlencode({'key': api_key})
    return url

def parse_gpx(data, browser_timezone=None):
    fallback_zone = ZoneInfo(browser_timezone) if browser_timezone else None
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
            ts, recorded_offset = None, None
            if stamp:
                parsed = datetime.fromisoformat(stamp.strip().replace('Z', '+00:00'))
                if parsed.tzinfo is not None:
                    recorded_offset = int(parsed.utcoffset().total_seconds()/60)
                elif fallback_zone is not None:
                    parsed = parsed.replace(tzinfo=fallback_zone)
                ts = parsed.timestamp()
            points.append(dict(lat=lat, lon=lon, time=stamp, timestamp=ts, segment=segment,
                               recorded_offset_minutes=recorded_offset))
        segment += 1
    if not points:
        raise ValueError('GPX 中没有轨迹点')
    # Untimed tracks use point indices; original order and duplicate points are preserved.
    timed = all(p['timestamp'] is not None for p in points)
    timed = timed and all(b['timestamp'] >= a['timestamp'] for a,b in zip(points,points[1:]))
    for i, p in enumerate(points):
        p['offset'] = p['timestamp'] - points[0]['timestamp'] if timed else i
    gaps, threshold = detect_gaps(points, timed)
    return dict(points=points, timed=timed, gaps=gaps, gap_threshold=threshold)


def detect_gaps(points, timed=True):
    intervals = [b['offset']-a['offset'] for a,b in zip(points,points[1:])
                 if b['offset']>a['offset'] and a['segment']==b['segment']]
    threshold = max(60.0,2*median(intervals)) if timed and intervals else None
    gaps = []
    for i,(a,b) in enumerate(zip(points,points[1:]),1):
        seconds = b['offset']-a['offset']
        missing = seconds>0 and (a['segment']!=b['segment'] or
                                  (threshold is not None and seconds>=threshold))
        b['gap_before'] = missing
        if missing:
            gaps.append(dict(start=a['offset'],end=b['offset'],seconds=seconds,
                             from_index=i-1,to_index=i))
    return gaps,threshold

def project(lat, lon, zoom):
    lat = max(-85.05112878, min(85.05112878, lat))
    s = 256 * 2 ** zoom
    return ((lon + 180) / 360 * s, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * s)

def cache_scope(style, api_key):
    # Separate credentials and the new cache format from legacy watermarked tiles.
    return 'public' if style == 'default' else hashlib.sha256(api_key.encode()).hexdigest()[:20]


def cache_status():
    with cache_lock:
        if time.monotonic() - _inventory['time'] > 3:
            files, size, by_style = 0, 0, {}
            for path in (CACHE / 'v2').glob('*/*/*/*/*.png'):
                try:
                    amount = path.stat().st_size
                except FileNotFoundError:
                    continue
                style = path.relative_to(CACHE / 'v2').parts[0]
                entry = by_style.setdefault(style, dict(files=0, bytes=0))
                entry['files'] += 1; entry['bytes'] += amount
                files += 1; size += amount
            _inventory.update(time=time.monotonic(), files=files, bytes=size, by_style=by_style)
        info = _cached_tile.cache_info()
        return dict(**cache_counters, files=_inventory['files'], bytes=_inventory['bytes'],
                    by_style=_inventory['by_style'], memory_hits=info.hits,
                    memory_tiles=info.currsize, key_configured=bool(MAP_API_KEY))


def tile(style, z, x, y, api_key=_KEY_DEFAULT):
    from PIL import Image
    api_key = MAP_API_KEY if api_key is _KEY_DEFAULT else api_key
    n = 2 ** z
    if not 0 <= y < n:
        return Image.new('RGB', (256,256), '#d9e1e5')
    if style != 'default' and not api_key:
        raise ValueError('CARTO Dark / Light 需要 API key，请在右侧设置后重试')
    x %= n
    identity = (str(CACHE), style, cache_scope(style, api_key), z, x, y, api_key)
    # Concurrent preview/export requests for the same tile download only once.
    with tile_locks[hash(identity) % len(tile_locks)]:
        return _cached_tile(*identity)


def clear_tile_memory():
    _cached_tile.cache_clear()


tile.cache_clear = clear_tile_memory


@lru_cache(maxsize=512)
def _cached_tile(cache_dir, style, scope, z, x, y, api_key):
    from PIL import Image
    path = Path(cache_dir) / 'v2' / style / scope / str(z) / str(x) / f'{y}.png'
    if path.exists():
        try:
            with Image.open(path) as im:
                result = im.convert('RGB')
                if result.size != (256, 256): raise ValueError('invalid tile')
            with cache_lock: cache_counters['disk_hits'] += 1
            return result
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    url = tile_url(style,z,x,y,api_key)
    with cache_lock: cache_counters['active'] += 1
    try:
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={'User-Agent':'GPXPointStudio/1.0 (local GPX PNG renderer)'}), timeout=25) as response:
                    data = response.read()
                with Image.open(BytesIO(data)) as im:
                    result = im.convert('RGB')
                    if result.size != (256,256): raise ValueError('invalid tile dimensions')
                if hashlib.sha256(result.tobytes()).hexdigest() in AUTH_TILE_HASHES:
                    raise TileAuthorizationError('地图服务返回 API KEY REQUIRED 水印，请检查 key 或访问限制')
                path.parent.mkdir(parents=True,exist_ok=True)
                temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                try:
                    temp.write_bytes(data)
                    temp.replace(path)
                finally: temp.unlink(missing_ok=True)
                with cache_lock:
                    cache_counters['downloads'] += 1
                    cache_counters['last_error'] = ''
                    _inventory['time'] = 0
                return result
            except Exception as exc:
                if isinstance(exc, TileAuthorizationError):
                    message = str(exc)
                    break
                if isinstance(exc, HTTPError) and exc.code in (401,403):
                    message = 'CARTO API key 未通过验证或访问受限（HTTP %s）' % exc.code
                    break
                message = '地图瓦片请求失败，请检查网络或 API key'
                if attempt < 2: time.sleep(attempt + 1)
        with cache_lock:
            cache_counters['errors'] += 1
            cache_counters['last_error'] = message
        raise RuntimeError(message) from None
    finally:
        with cache_lock: cache_counters['active'] -= 1


def drawing_settings(value):
    result = {}
    for name, default, low, high in [('line_width',4,0,100), ('point_diameter',18,0,200), ('point_outline_width',3,0,30)]:
        number = float(value.get(name, default))
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'{name} 必须在 {low}–{high} 之间')
        result[name] = round(number)
    for name, default in [('line_color','#ff694b'), ('point_color','#29b5ff'), ('point_outline_color','#ffffff')]:
        color = value.get(name, default)
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):
            raise ValueError(f'{name} 颜色格式须为 #RRGGBB')
        result[name] = color
    result['show_line'] = bool(value.get('show_line',True))
    result['show_point'] = bool(value.get('show_point',True))
    return result

def settings(value):
    z, w, h = int(value.get('zoom',14)), int(value.get('width',600)), int(value.get('height',600))
    style = value.get('style','default')
    if not 1 <= z <= 19 or not 128 <= w <= 4096 or not 128 <= h <= 4096 or style not in STYLES:
        raise ValueError('缩放等级须为 1–19，宽高须为 128–4096，地图样式无效')
    drawing_settings(value)
    return z,w,h,style

def interpolate_position(points, offset, offsets=None, fit_gaps=False):
    offsets = offsets if offsets is not None else [p['offset'] for p in points]
    offset = max(offsets[0], min(offsets[-1], float(offset)))
    i = max(0, bisect_right(offsets, offset) - 1)
    a = points[i]
    if i == len(points)-1:
        return dict(a)
    b = points[i+1]
    gap = b['offset'] - a['offset']
    is_gap = b.get('gap_before',False) or a['segment']!=b['segment']
    ratio = (offset-a['offset'])/gap if gap>0 and (not is_gap or fit_gaps) else 0
    if a['lat']==b['lat'] and a['lon']==b['lon']:
        return dict(a,offset=offset)
    delta_lon = (b['lon']-a['lon']+180)%360-180
    return dict(a,lat=a['lat']+(b['lat']-a['lat'])*ratio,
                lon=(a['lon']+delta_lon*ratio+180)%360-180,offset=offset)


def export_plan(track, config, start, end):
    speed = float(config.get('export_speed',10))
    fps_value = float(config.get('fps',30))
    if not math.isfinite(speed) or not 1<=speed<=100 or fps_value not in (25,30,50,60):
        raise ValueError('导出倍率必须为 1–100，帧率须为 25、30、50、60')
    duration = track['points'][end]['offset']-track['points'][start]['offset']
    count = max(1,math.ceil(duration/speed*int(fps_value)))
    return dict(export_speed=speed,fps=int(fps_value),source_duration=duration,
                estimated_duration=duration/speed,frame_count=count,sequence_duration=count/int(fps_value))


def frame_offset(plan, first_offset, frame):
    count = plan['frame_count']
    return first_offset + (plan['source_duration']*frame/(count-1) if count>1 else 0)


def render(points, index, config, api_key=_KEY_DEFAULT, position=None):
    from PIL import Image, ImageDraw
    z,w,h,style = settings(config)
    paint = drawing_settings(config)
    position = position or points[index]
    cx,cy = project(position['lat'],position['lon'],z)
    left,top = round(cx-w/2), round(cy-h/2)
    im = Image.new('RGB',(w,h))
    for ty in range(top//256,(top+h-1)//256+1):
        for tx in range(left//256,(left+w-1)//256+1):
            im.paste(tile(style,z,tx,ty,api_key),(tx*256-left,ty*256-top))
    draw = ImageDraw.Draw(im)
    coords = []
    previous = None
    world = 256*2**z
    for p in points:
        x,y = project(p['lat'],p['lon'],z)
        x = cx + (x-cx+world/2)%world-world/2
        if (previous != p['segment'] or p.get('gap_before',False)) and not config.get('fit_gaps',False):
            if len(coords)>1 and paint['show_line'] and paint['line_width']: draw.line(coords,fill=paint['line_color'],width=paint['line_width'])
            coords=[]
        coords.append((x-left,y-top)); previous=p['segment']
    if len(coords)>1 and paint['show_line'] and paint['line_width']: draw.line(coords,fill=paint['line_color'],width=paint['line_width'])
    x,y=cx-left,cy-top
    diameter = paint['point_diameter']
    if paint['show_point'] and diameter:
        r = diameter / 2
        draw.ellipse((x-r,y-r,x+r-1,y+r-1),fill=paint['point_color'],outline=paint['point_outline_color'],width=min(paint['point_outline_width'],max(1,diameter//2)))
    credit = '© OpenStreetMap contributors' + (' © CARTO' if style != 'default' else '')
    box = draw.textbbox((0,0),credit)
    draw.rectangle((0,h-20,box[2]+12,h),fill='white')
    draw.text((6,h-16),credit,fill='black')
    out=BytesIO(); im.save(out,format='PNG'); return out.getvalue()

def render_sequence(points, positions, config, api_key=_KEY_DEFAULT):
    """Keep one encoded frame; a fixed sequence has fixed route/style settings."""
    previous_coordinates, previous_png = None, None
    for position in positions:
        coordinates = (position['lat'],position['lon'])
        reused = previous_png is not None and coordinates == previous_coordinates
        if not reused:
            previous_png = render(points,0,config,api_key,position)
            previous_coordinates = coordinates
        yield previous_png,reused


def export_job(job, track, config, start, end):
    try:
        api_key = MAP_API_KEY
        plan = export_plan(track,config,start,end)
        points = track['points']
        offsets = [p['offset'] for p in points]
        folder=ROOT/'.exports'; folder.mkdir(exist_ok=True)
        path=folder/f'{job}.zip'
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            jobs[job].update(rendered=0,reused=0)
            positions = (interpolate_position(points,frame_offset(plan,points[start]['offset'],frame),offsets,
                         fit_gaps=bool(config.get('fit_gaps',False))) for frame in range(plan['frame_count']))
            for frame,(png,reused) in enumerate(render_sequence(points,positions,config,api_key)):
                archive.writestr(f'frame_{frame+1:06d}.png',png)
                jobs[job]['reused' if reused else 'rendered'] += 1
                jobs[job]['done']=frame+1
            jobs[job]['phase']='packing'
            archive.writestr('manifest.json',json.dumps(dict(settings=config,start=start,end=end,
                timing=plan,rendering=dict(rendered=jobs[job]['rendered'],reused=jobs[job]['reused']),timed=track['timed'],sampling='uniform source-time interpolation including endpoints',
                points=points[start:end+1]),ensure_ascii=False))
        jobs[job].update(status='complete',phase='complete',path=str(path))
    except Exception as exc:
        jobs[job].update(status='error',error=str(exc))


class Handler(BaseHTTPRequestHandler):
    def send(self,data,status=200,content='application/json'):
        if content=='application/json': data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Cache-Control','no-store'); self.send_header('Content-Type',content); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        try:
            path=self.path.split('?')[0]
            if path == '/api/cache':
                self.send(cache_status()); return
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
                job=jobs[parts[2]]
                elapsed=time.monotonic()-job['started']
                result={k:v for k,v in job.items() if k not in ('path','started')}
                result.update(elapsed=elapsed,eta=(elapsed/job['done']*(job['total']-job['done'])) if job['done'] else None)
                self.send(result); return
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
                track=parse_gpx(raw,self.headers.get('X-Browser-Time-Zone')); key=uuid.uuid4().hex; tracks[key]=track
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
                position=None
                if 'offset' in data:
                    offset=float(data['offset'])
                    if not math.isfinite(offset): raise ValueError('时间无效')
                    position=interpolate_position(track['points'],offset,fit_gaps=bool(data.get('fit_gaps',False)))
                self.send(render(track['points'],index,data,position=position),content='image/png'); return
            if self.path=='/api/export':
                start,end=int(data.get('start',0)),int(data.get('end',len(track['points'])-1))
                if not 0<=start<=end<len(track['points']): raise ValueError('导出范围无效')
                if any(j['status']=='running' for j in jobs.values()): raise ValueError('已有导出任务正在运行')
                plan=export_plan(track,data,start,end)
                key=uuid.uuid4().hex; jobs[key]=dict(status='running',phase='rendering',done=0,total=plan['frame_count'],started=time.monotonic(),timing=plan)
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
            for i,(png,reused) in enumerate(render_sequence(track['points'],track['points'],config)):
                (args.output_dir/f'frame_{i+1:06d}.png').write_bytes(png); print(f"{i+1}/{len(track['points'])}" + (' (reused)' if reused else ''))
        except Exception as exc: parser.exit(1,f'{exc}\n')
    else:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        print(f'GPX Point Studio: http://127.0.0.1:{args.port}',flush=True)
        if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{args.port}')
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()

if __name__=='__main__': main()
