import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from PIL import Image
import render_gpx_png_sequence as renderer


class RendererTests(unittest.TestCase):
    def test_point_street_labels_do_not_use_line_length(self):
        from map_labels import draw_labels,label_options
        base=Image.new('RGB',(256,256),'#202020')
        options=label_options(dict(show_places=False,show_pois=False))
        for geometry in [dict(type='Point',coordinates=[128,128]),dict(type='MultiPoint',coordinates=[[128,128],[160,160]])]:
            point=dict(properties=dict(name='Point street',rank=1),geometry=geometry)
            def render(features):
                return draw_labels(base,{'transportation_name':dict(extent=256,features=features)},14,0,0,options).tobytes()
            expected=render([point])
            self.assertNotEqual(expected,base.tobytes())
            short=dict(properties=dict(name='Too long for segment',rank=0),geometry=dict(type='LineString',coordinates=[[1,1],[2,1]]))
            self.assertEqual(render([short,point]),expected)

    def test_export_defaults_and_cancellation_before_rendering(self):
        import threading
        self.assertEqual(renderer.settings({})[:3],(15,900,900))
        paint=renderer.drawing_settings({})
        self.assertEqual((paint['line_width'],paint['point_diameter'],paint['point_outline_width']),(10,40,6))
        event=threading.Event();event.set()
        state=dict(_cancel=event,workers=1,download_workers=1,done=0)
        with self.assertRaises(renderer.ExportCancelled):
            list(renderer.pipelined_frames([],[dict(lat=0,lon=0)],{},'test-key',state,sink=lambda *args:None))

    def test_download_error_includes_status_but_redacts_secrets(self):
        from urllib.error import HTTPError,URLError
        error=HTTPError('https://example.com',429,'Too Many Requests',{},None)
        self.assertIn('HTTP 429',renderer.download_error_details(error));error.close()
        with patch.object(renderer,'MAP_API_KEY','secret-key'):
            message=renderer.download_error_details(URLError('timeout https://example.com?key=secret-key&token=another-secret'))
        self.assertIn('timeout',message)
        self.assertNotIn('secret-key',message)
        self.assertNotIn('another-secret',message)

    def test_skip_failed_frames_preserves_original_numbering(self):
        state=dict(workers=1,download_workers=1,done=0);written={}
        def pool(**kwargs):
            kwargs.pop('mp_context',None);return ThreadPoolExecutor(**kwargs)
        def download(style,z,x,y,config,key):
            if x==1:raise RuntimeError('network unreachable')
        def sink(indices,png):
            for i in indices:written[i]=png
            state['_skip'].set()
        with patch.object(renderer,'viewport_tiles',side_effect=lambda p,c:{(p['lon'],0)}),patch.object(renderer,'map_tile',side_effect=download),patch.object(renderer,'render_worker',return_value=b'png'),patch.object(renderer,'ProcessPoolExecutor',side_effect=pool),patch.object(renderer,'retry_delay',return_value=0):
            list(renderer.pipelined_frames([],[dict(lat=0,lon=1),dict(lat=0,lon=2)],dict(style='default'),'test-key',state,sink=sink))
        self.assertEqual(written,{2:b'png'})
        self.assertEqual(state['skipped_frames'],[1])
        self.assertEqual(state['done'],1)

    def test_ten_failures_wait_for_manual_retry_without_losing_frames(self):
        import threading,time
        attempts={};written={};order=[];state=dict(workers=2,download_workers=2,done=0)
        def download(style,z,x,y,config,key):
            attempts[x]=attempts.get(x,0)+1
            if x==1 and attempts[x]<=10:raise RuntimeError('offline')
        def pool(**kwargs):
            kwargs.pop('mp_context',None);return ThreadPoolExecutor(**kwargs)
        def sink(indices,png):
            order.extend(indices)
            for index in indices:written[index]=png
        errors=[]
        def run():
            try:list(renderer.pipelined_frames([],[dict(lat=0,lon=1),dict(lat=0,lon=2)],dict(style='default'),'test-key',state,sink=sink))
            except Exception as exc:errors.append(exc)
        with patch.object(renderer,'retry_delay',return_value=0),patch.object(renderer,'viewport_tiles',side_effect=lambda p,c:{(p['lon'],0)}),patch.object(renderer,'map_tile',side_effect=download),patch.object(renderer,'render_worker',side_effect=lambda p:str(p['lon']).encode()),patch.object(renderer,'ProcessPoolExecutor',side_effect=pool):
            thread=threading.Thread(target=run);thread.start()
            deadline=time.monotonic()+5
            while state.get('phase')!='awaiting_retry' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(state.get('phase'),'awaiting_retry')
            self.assertEqual(attempts[1],10)
            self.assertEqual(written,{2:b'2'})
            self.assertEqual(state['done'],1)
            state['_retry'].set();thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(written,{1:b'1',2:b'2'})
        self.assertEqual(order,[2,1])

    def test_render_retry_keeps_original_frame_numbers(self):
        attempts={};written={};order=[]
        positions=[dict(lat=0,lon=1),dict(lat=0,lon=2),dict(lat=0,lon=1)]
        state=dict(workers=2,download_workers=2,done=0)
        def draw(position):
            key=position['lon'];attempts[key]=attempts.get(key,0)+1
            if key==1 and attempts[key]==1:raise RuntimeError('render temporarily failed')
            return str(key).encode()
        def pool(**kwargs):
            kwargs.pop('mp_context',None);return ThreadPoolExecutor(**kwargs)
        def sink(indices,png):
            order.extend(indices)
            for index in indices:written[index]=png
        with patch.object(renderer,'viewport_tiles',side_effect=lambda p,c:{(p['lon'],0)}),patch.object(renderer,'map_tile'),patch.object(renderer,'render_worker',side_effect=draw),patch.object(renderer,'ProcessPoolExecutor',side_effect=pool):
            list(renderer.pipelined_frames([],positions,dict(style='default'),'test-key',state,sink=sink))
        self.assertEqual(written,{1:b'1',2:b'2',3:b'1'})
        self.assertEqual(order[0],2)
        self.assertEqual(attempts[1],2)
        self.assertEqual(state['done'],3)
        self.assertTrue(any(entry['message']=='图片 {frame} 重试渲染成功' for entry in state['logs']))

    def test_export_mode_requires_loopback_host_and_peer(self):
        for host in ['localhost:8765','127.0.0.1:8765','127.0.0.2','[::1]:8765','app.localhost','0.0.0.0:8765']:
            self.assertTrue(renderer.is_local_access(host,'127.0.0.1'),host)
        for host,peer in [('example.com','127.0.0.1'),('localhost','192.168.1.2'),('localhost.evil.example','127.0.0.1'),('192.168.1.2','127.0.0.1')]:
            self.assertFalse(renderer.is_local_access(host,peer))

    def test_streaming_zip_uses_unseekable_http_writer(self):
        import zipfile
        output=BytesIO()
        with zipfile.ZipFile(renderer.StreamingZipWriter(output),'w',allowZip64=True) as archive:
            archive.writestr('frame_000001.png',b'png')
        with zipfile.ZipFile(BytesIO(output.getvalue())) as archive:
            self.assertEqual(archive.read('frame_000001.png'),b'png')

    def test_clean_map_frames_and_separate_attribution_image(self):
        config=dict(style='default',width=256,height=256,zoom=1,show_line=False,show_point=False)
        with patch.object(renderer,'map_tile',return_value=Image.new('RGB',(256,256),'#123456')):
            png=renderer.render([dict(lat=0,lon=0,segment=0)],0,config)
        frame=Image.open(BytesIO(png))
        self.assertEqual(frame.getpixel((6,250)),(18,52,86))
        credit=Image.open(BytesIO(renderer.attribution_png(config)))
        self.assertEqual(credit.size,frame.size)
        self.assertGreater(len(credit.getcolors(100000)),1)

    def test_export_log_retains_recent_structured_messages(self):
        state={}
        for i in range(505):renderer.export_log(state,'retry {count}',count=i)
        self.assertEqual(len(state['logs']),500)
        self.assertEqual(state['logs'][0]['values']['count'],5)
        self.assertEqual(state['logs'][-1]['values']['count'],504)

    def test_pipeline_renders_ready_frames_before_retrying_failed_tiles(self):
        import threading
        rendered=threading.Event();attempts={}
        positions=[dict(lat=0,lon=1),dict(lat=0,lon=2),dict(lat=0,lon=2)]
        state=dict(workers=2,download_workers=2,done=0)
        def download(style,z,x,y,config,key):
            attempts[x]=attempts.get(x,0)+1
            if x==1 and attempts[x]==1:raise RuntimeError('temporary failure')
            if x==1:self.assertTrue(rendered.wait(3))
        def draw(position):
            if position['lon']==2:rendered.set()
            return b'png'
        def pool(**kwargs):
            kwargs.pop('mp_context',None)
            return ThreadPoolExecutor(**kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(renderer,'viewport_tiles',side_effect=lambda p,c:{(p['lon'],0)}), patch.object(renderer,'map_tile',side_effect=download), patch.object(renderer,'render_worker',side_effect=draw), patch.object(renderer,'ProcessPoolExecutor',side_effect=pool):
            paths=list(renderer.pipelined_frames([],positions,dict(style='default'),'test-key',state,Path(tmp)))
            self.assertEqual([p.read_bytes() for p in paths],[b'png']*3)
        self.assertEqual(state['done'],3)
        self.assertEqual(state['rendered'],2)
        self.assertEqual(state['reused'],1)
        self.assertEqual(attempts[1],2)
        self.assertEqual(state['tile_done'],2)
        self.assertTrue(any(entry['message']=='瓦片 {z}/{x}/{y} 补充下载成功' for entry in state['logs']))

    def test_clear_tile_cache_removes_disk_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(renderer,'CACHE',Path(tmp)/'tiles'), patch.dict(renderer.jobs,{},clear=True), patch.dict(renderer.cache_counters,dict(active=0)):
            renderer.CACHE.mkdir();(renderer.CACHE/'tile.png').write_bytes(b'cached')
            renderer.clear_map_cache()
            self.assertFalse(renderer.CACHE.exists())
            self.assertEqual(renderer.labelled_map_tile.cache_info().currsize,0)
            with patch.dict(renderer.jobs,{'busy':dict(status='running')}):
                with self.assertRaises(ValueError):renderer.clear_map_cache()

    def test_poi_tile_cache_isolates_all_label_parameters(self):
        import json
        with patch.object(renderer,'labelled_map_tile',return_value=None) as tile:
            configs=[dict(poi_excluded=[]),dict(poi_excluded=['transit']),dict(poi_excluded=[],poi_font_size=20),dict(poi_excluded=[],show_pois=False),dict(poi_excluded=[],label_language='en')]
            identities=[]
            for config in configs:
                renderer.map_tile('dark',14,1,1,({'show_pois':True} | config),'test-key')
                identities.append(tile.call_args.args)
            self.assertEqual(len(set(identities)),len(configs))
            self.assertEqual(json.loads(identities[1][-2])['poi_excluded'],['transit'])
            renderer.map_tile('dark',14,1,1,dict(poi_excluded=['parks','transit']),'test-key')
            first=tile.call_args.args
            renderer.map_tile('dark',14,1,1,dict(poi_excluded=['transit','parks']),'test-key')
            self.assertEqual(first,tile.call_args.args)

    def test_poi_preview_cache_isolates_categories(self):
        renderer._preview_png.cache_clear()
        with patch.dict(renderer.tracks,{'poi-cache-test':dict(points=[dict(lat=0,lon=0)])}):
            with patch.object(renderer,'render',return_value=b'png') as draw:
                for excluded in ([],['transit'],[]):
                    renderer.preview_png('poi-cache-test',dict(poi_excluded=excluded),dict(lat=0,lon=0))
                self.assertEqual(draw.call_count,2)

    def test_poi_filter_classification_and_rendering(self):
        from map_labels import poi_category,label_options,draw_labels,POI_CATEGORIES
        self.assertEqual(len(POI_CATEGORIES),32)
        for props,expected in [({'class':'bus','subclass':'bus_stop'},'transit'),({'class':'shop','subclass':'convenience'},'shopping'),({'class':'residential'},'residential'),({'class':'unknown'},'other')]:
            self.assertEqual(poi_category(props),expected)
        base=Image.new('RGB',(256,256),'#202020')
        layers={'poi':dict(extent=256,features=[dict(properties=dict(name='Bus stop',**{'class':'bus'}),geometry=dict(type='Point',coordinates=[128,128]))])}
        visible=draw_labels(base,layers,14,0,0,label_options(dict(show_pois=True)))
        hidden=draw_labels(base,layers,14,0,0,label_options(dict(poi_excluded=['transit'])))
        self.assertNotEqual(visible.tobytes(),base.tobytes())
        self.assertEqual(hidden.tobytes(),base.tobytes())
        self.assertNotEqual(label_options({}),label_options(dict(poi_excluded=['transit'])))
        self.assertEqual(set(POI_CATEGORIES)-set(label_options({})['poi_excluded']),{'transit','government','parks','attraction'})
        with self.assertRaises(ValueError):label_options(dict(poi_excluded=['invalid']))

    def test_route_clipping_preserves_fractional_boundaries(self):
        points=[dict(lat=0,lon=i,offset=i*10,segment=0) for i in range(4)]
        route=renderer.clipped_route(points,dict(start_offset=5,end_offset=25))
        self.assertEqual([p['offset'] for p in route],[5,10,20,25])
        self.assertEqual([p['lon'] for p in route],[.5,1,2,2.5])
        self.assertEqual(renderer.clipped_route(points,{}),points)

    def test_preview_cache_separates_trim_boundaries(self):
        points=[dict(lat=0,lon=i,offset=i*10,segment=0) for i in range(3)]
        renderer._preview_png.cache_clear()
        with patch.dict(renderer.tracks,{'trim-test':dict(points=points)}):
            with patch.object(renderer,'render',return_value=b'png') as draw:
                renderer.preview_png('trim-test',dict(start_offset=0,end_offset=20),points[1])
                renderer.preview_png('trim-test',dict(start_offset=5,end_offset=15),points[1])
                self.assertEqual(draw.call_count,2)
                self.assertEqual(draw.call_args.args[2]['start_offset'],5)

    def test_preserves_duplicate_points_and_irregular_timing(self):
        track = renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"><time>2026-01-01T00:00:00Z</time></trkpt><trkpt lat="1" lon="2"><time>2026-01-01T00:00:07Z</time></trkpt></trkseg></trk></gpx>')
        self.assertEqual(len(track['points']), 2)
        self.assertEqual(track['points'][1]['offset'], 7)
        self.assertTrue(track['timed'])

    def test_missing_times_and_segments(self):
        track = renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg><trkseg><trkpt lat="3" lon="4"/></trkseg></trk></gpx>')
        self.assertFalse(track['timed'])
        self.assertEqual([p['offset'] for p in track['points']], [0, 1])
        self.assertNotEqual(track['points'][0]['segment'], track['points'][1]['segment'])

    def test_dimensions_and_center_marker(self):
        points = renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>')['points']
        with patch.object(renderer, 'tile', return_value=Image.new('RGB', (256, 256), 'white')):
            image = Image.open(BytesIO(renderer.render(points, 0, dict(zoom=14, width=640, height=360))))
        self.assertEqual(image.size, (640, 360))
        self.assertEqual(image.getpixel((320,180)), (41,181,255))

    def test_key_persistence_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            with patch.object(renderer, 'ENV_FILE', env), patch.object(renderer, 'MAP_API_KEY', ''):
                renderer.save_map_key('test-key-local-only')
                self.assertEqual(renderer.load_map_key(), 'test-key-local-only')
                self.assertEqual(env.stat().st_mode & 0o777, 0o600)
                self.assertIn('key=test-key-local-only', renderer.tile_url('dark', 4, 1, 1))
                self.assertNotIn('key=', renderer.tile_url('default', 4, 1, 1))
                renderer.save_map_key('')
                self.assertEqual(renderer.load_map_key(), '')

    def test_key_rejects_multiline_values(self):
        with self.assertRaises(ValueError):
            renderer.save_map_key('invalid\nINJECTED=value')

    def test_cache_isolates_keys_ignores_legacy_and_reuses_disk(self):
        data = BytesIO()
        Image.new('RGB', (256,256), '#123456').save(data, format='PNG')
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            legacy = cache / 'dark/4/1/1.png'
            legacy.parent.mkdir(parents=True)
            Image.new('RGB',(256,256),'red').save(legacy)
            with patch.object(renderer, 'CACHE', cache), patch.object(renderer, 'MAP_API_KEY', 'test-key-a'):
                renderer.tile.cache_clear()
                with patch.object(renderer, 'urlopen', side_effect=lambda *a,**k: BytesIO(data.getvalue())) as network:
                    first = renderer.tile('dark',4,1,1)
                    self.assertEqual(first.getpixel((0,0)),(18,52,86))
                    renderer.tile('dark',4,1,1)
                    self.assertEqual(network.call_count,1)
                    self.assertIn('key=test-key-a',network.call_args.args[0].full_url)
                    renderer.tile.cache_clear()
                    renderer.tile('dark',4,1,1)
                    self.assertEqual(network.call_count,1)
                    renderer.tile('dark',4,1,1,api_key='test-key-b')
                    self.assertEqual(network.call_count,2)
                    self.assertIn('key=test-key-b',network.call_args.args[0].full_url)
                self.assertEqual(len(list((cache/'v2').rglob('*.png'))),2)
                with renderer.cache_lock: renderer._inventory['time']=0
                state = renderer.cache_status()
                self.assertEqual(state['files'],2)
                self.assertGreater(state['bytes'],0)
                self.assertNotIn('test-key', str(state))
        renderer.tile.cache_clear()

    def test_auth_placeholder_is_not_cached(self):
        data=BytesIO()
        im=Image.new('RGB',(256,256),'red')
        im.save(data,format='PNG')
        digest=renderer.hashlib.sha256(im.tobytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory, patch.object(renderer,'CACHE',Path(directory)), patch.object(renderer,'AUTH_TILE_HASHES',{digest}):
            renderer.tile.cache_clear()
            with patch.object(renderer,'urlopen',return_value=BytesIO(data.getvalue())) as network:
                with self.assertRaisesRegex(RuntimeError,'API KEY REQUIRED'):
                    renderer.tile('dark',4,1,1,api_key='test-key')
                self.assertEqual(network.call_count,1)
            self.assertFalse(list(Path(directory).rglob('*.png')))
        renderer.tile.cache_clear()

    def test_concurrent_same_tile_downloads_once(self):
        data = BytesIO()
        Image.new('RGB',(256,256),'blue').save(data,format='PNG')
        with tempfile.TemporaryDirectory() as directory, patch.object(renderer,'CACHE',Path(directory)):
            renderer.tile.cache_clear()
            with patch.object(renderer,'urlopen',side_effect=lambda *a,**k: BytesIO(data.getvalue())) as network:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    images=list(pool.map(lambda _:renderer.tile('default',4,1,1),range(4)))
                self.assertEqual(network.call_count,1)
                self.assertTrue(all(im.getpixel((0,0))==(0,0,255) for im in images))
        renderer.tile.cache_clear()

    def test_drawing_controls_apply_to_pixels(self):
        points=renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="1" lon="2.01"/></trkseg></trk></gpx>')['points']
        config=dict(zoom=14,width=320,height=240,line_color='#00ff00',line_width=8,point_color='#ff0000',point_diameter=30,point_outline_width=2,point_outline_color='#0000ff')
        with patch.object(renderer,'tile',return_value=Image.new('RGB',(256,256),'white')):
            im=Image.open(BytesIO(renderer.render(points,0,config)))
            self.assertEqual(im.getpixel((160,120)),(255,0,0))
            self.assertEqual(im.getpixel((173,120)),(0,0,255))
            self.assertEqual(im.getpixel((190,120)),(0,255,0))
            im=Image.open(BytesIO(renderer.render(points,0,dict(config,show_point=False,show_line=False))))
            self.assertEqual(im.getpixel((160,120)),(255,255,255))

    def test_five_hours_at_5x_becomes_one_hour(self):
        track=dict(points=[dict(offset=0),dict(offset=18000)])
        plan=renderer.export_plan(track,dict(export_speed=5,fps=30),0,1)
        self.assertEqual(plan['estimated_duration'],3600)
        self.assertEqual(plan['frame_count'],108000)
        self.assertEqual(plan['sequence_duration'],3600)
        self.assertEqual(renderer.frame_offset(plan,0,107999),18000)

    def test_interpolation_respects_recorded_time(self):
        points=[dict(lat=0,lon=0,offset=0,segment=0),dict(lat=10,lon=20,offset=100,segment=0)]
        position=renderer.interpolate_position(points,25)
        self.assertEqual(position['lat'],2.5)
        self.assertEqual(position['lon'],5)
        points[1]['segment']=1
        self.assertEqual(renderer.interpolate_position(points,25)['lat'],0)

    def test_export_plan_rejects_invalid_rate(self):
        track=dict(points=[dict(offset=0),dict(offset=10)])
        for config in [dict(fps=24),dict(export_speed=0),dict(export_speed=101),dict(fps=30.5)]:
            with self.assertRaises(ValueError):renderer.export_plan(track,config,0,1)
        for fps in [25,30,50,60]:
            plan=renderer.export_plan(track,dict(fps=fps,export_speed=5),0,1)
            self.assertEqual(plan['frame_count'],2*fps)

    def test_recorded_timezone_offset_is_preserved(self):
        track=renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"><time>2026-01-01T09:30:00+09:30</time></trkpt></trkseg></trk></gpx>','Asia/Singapore')
        point=track['points'][0]
        self.assertEqual(point['recorded_offset_minutes'],570)
        self.assertEqual(point['timestamp'],1767225600)

    def test_timezone_missing_uses_browser_zone(self):
        data=b'<gpx><trk><trkseg><trkpt lat="1" lon="2"><time>2026-01-01T09:00:00</time></trkpt></trkseg></trk></gpx>'
        tokyo=renderer.parse_gpx(data,'Asia/Tokyo')['points'][0]
        utc=renderer.parse_gpx(data,'UTC')['points'][0]
        self.assertIsNone(tokyo['recorded_offset_minutes'])
        self.assertEqual(utc['timestamp']-tokyo['timestamp'],9*3600)

    def test_gap_detection_and_optional_time_fit(self):
        points=[dict(lat=i,lon=i,offset=t,segment=0) for i,t in enumerate([0,30,60,120,150])]
        gaps,threshold=renderer.detect_gaps(points)
        self.assertEqual(threshold,60)
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['start'],60)
        self.assertEqual(gaps[0]['end'],120)
        self.assertEqual(renderer.interpolate_position(points,90)['lat'],2)
        self.assertEqual(renderer.interpolate_position(points,90,fit_gaps=True)['lat'],2.5)
        self.assertEqual(renderer.interpolate_position(points,45)['lat'],1.5)
        self.assertEqual(renderer.interpolate_position(points,120)['lat'],3)

    def test_fit_also_bridges_separate_recording_segments(self):
        points=[dict(lat=0,lon=0,offset=0,segment=0),dict(lat=10,lon=10,offset=100,segment=1)]
        renderer.detect_gaps(points)
        self.assertEqual(renderer.interpolate_position(points,50)['lat'],0)
        self.assertEqual(renderer.interpolate_position(points,50,fit_gaps=True)['lat'],5)

    def test_gap_line_visibility_matches_fit_setting(self):
        points=[dict(lat=1,lon=2,offset=0,segment=0),dict(lat=1,lon=2.01,offset=100,segment=0,gap_before=True)]
        config=dict(zoom=14,width=320,height=240,show_point=False,line_color='#00ff00',line_width=4)
        with patch.object(renderer,'tile',return_value=Image.new('RGB',(256,256),'white')):
            off=Image.open(BytesIO(renderer.render(points,0,config)))
            on=Image.open(BytesIO(renderer.render(points,0,dict(config,fit_gaps=True))))
        self.assertEqual(off.getpixel((190,120)),(255,255,255))
        self.assertEqual(on.getpixel((190,120)),(0,255,0))

    def test_identical_consecutive_frames_render_only_once(self):
        points=[dict(lat=1,lon=2),dict(lat=1,lon=2),dict(lat=3,lon=4),dict(lat=3,lon=4)]
        with patch.object(renderer,'render',side_effect=[b'first-png',b'second-png']) as draw:
            output=list(renderer.render_sequence(points,points,dict(line_width=8)))
        self.assertEqual(draw.call_count,2)
        self.assertEqual(output,[(b'first-png',False),(b'first-png',True),(b'second-png',False),(b'second-png',True)])

    def test_stationary_interpolation_keeps_exact_coordinate(self):
        points=[dict(lat=1.28,lon=103.85,offset=0,segment=0),dict(lat=1.28,lon=103.85,offset=100,segment=0)]
        for offset in [0,1,50,99,100]:
            p=renderer.interpolate_position(points,offset)
            self.assertEqual((p['lat'],p['lon']),(1.28,103.85))

    def test_preview_reuses_same_visual_frame_but_not_changed_style(self):
        renderer._preview_png.cache_clear()
        with patch.dict(renderer.tracks,{'preview-test':dict(points=[dict(lat=1,lon=2)])}):
            with patch.object(renderer,'render',side_effect=[b'same',b'new-style']) as draw:
                first=renderer.preview_png('preview-test',dict(offset=0),dict(lat=1,lon=2))
                second=renderer.preview_png('preview-test',dict(offset=100,fps=60),dict(lat=1,lon=2))
                changed=renderer.preview_png('preview-test',dict(line_color='#123456'),dict(lat=1,lon=2))
                self.assertEqual(draw.call_count,2)
                self.assertEqual(first,second)
                self.assertNotEqual(first,changed)
        renderer._preview_png.cache_clear()

    def test_map_label_variants_and_defaults(self):
        self.assertEqual(renderer.map_style('dark'),'dark')
        self.assertEqual(renderer.map_style('dark',False),'dark-nolabels')
        self.assertEqual(renderer.map_style('light',False),'light-nolabels')
        self.assertEqual(renderer.map_style('default',False),'default')
        self.assertIn('/dark_nolabels/',renderer.tile_url('dark-nolabels',14,1,1,api_key='test-key'))

    def test_label_toggle_invalidates_png_preview(self):
        renderer._preview_png.cache_clear()
        with patch.dict(renderer.tracks,{'labels-test':dict(points=[dict(lat=1,lon=2)])}):
            with patch.object(renderer,'render',side_effect=[b'labels',b'no-labels']) as draw:
                a=renderer.preview_png('labels-test',{},dict(lat=1,lon=2))
                b=renderer.preview_png('labels-test',dict(map_labels=False),dict(lat=1,lon=2))
                self.assertNotEqual(a,b)
                self.assertEqual(draw.call_count,2)
        renderer._preview_png.cache_clear()

    def test_fractional_cursor_boundaries_are_exported_exactly(self):
        track=dict(points=[dict(offset=0),dict(offset=3),dict(offset=9)])
        plan=renderer.export_plan(track,dict(start_offset=1.25,end_offset=6.75,export_speed=5,fps=30),0,2)
        self.assertEqual(plan['source_duration'],5.5)
        self.assertEqual(plan['frame_count'],33)
        self.assertEqual(renderer.frame_offset(plan,plan['source_start'],0),1.25)
        self.assertEqual(renderer.frame_offset(plan,plan['source_start'],32),6.75)

    def test_gap_threshold_has_no_sixty_second_floor(self):
        points=[dict(offset=t,segment=0) for t in [0,1,2,4,5]]
        gaps,threshold=renderer.detect_gaps(points)
        self.assertEqual(threshold,2)
        self.assertEqual([(g['start'],g['end']) for g in gaps],[(2,4)])

    def test_label_language_fallback_and_feature_categories(self):
        from map_labels import label_name,label_options,draw_labels
        properties={'name':'人民大道','name:en':"People's Avenue",'name:zh-Hant':'人民大道'}
        self.assertEqual(label_name(properties,'en'),"People's Avenue")
        self.assertEqual(label_name(properties,'ja'),'人民大道')
        self.assertEqual(label_name(properties,'local'),'人民大道')
        base=Image.new('RGB',(256,256),'#202020')
        layers={'transportation_name':dict(extent=256,features=[dict(properties=properties,geometry=dict(type='LineString',coordinates=[[10,128],[246,128]]))]),
                'poi':dict(extent=256,features=[dict(properties={'name':'商店','name:en':'Shop'},geometry=dict(type='Point',coordinates=[100,60]))])}
        off=draw_labels(base,layers,14,0,0,dict(label_options(dict(show_streets=False,show_places=False,show_pois=False)),_dark=True))
        self.assertEqual(off.tobytes(),base.tobytes())
        roads=draw_labels(base,layers,14,0,0,dict(label_options(dict(show_places=False,show_pois=False,label_language='en')),_dark=True))
        pois=draw_labels(base,layers,14,0,0,dict(label_options(dict(show_places=False,show_streets=False,label_language='en',poi_excluded=[],show_pois=True)),_dark=True))
        self.assertNotEqual(roads.tobytes(),base.tobytes())
        self.assertNotEqual(pois.tobytes(),base.tobytes())
        self.assertNotEqual(roads.tobytes(),pois.tobytes())
        with self.assertRaises(ValueError):label_options(dict(label_language='unknown-language'))

    def test_target_video_duration_sets_exact_frame_count(self):
        track=dict(points=[dict(offset=0),dict(offset=18000)])
        plan=renderer.export_plan(track,dict(target_duration=3600,fps=30,export_speed=10),0,1)
        self.assertEqual(plan['frame_count'],108000)
        self.assertEqual(plan['export_speed'],5)
        self.assertEqual(plan['sequence_duration'],3600)
        self.assertEqual(plan['point_conflicts'],0)

    def test_original_points_use_nearest_frames_without_stretching(self):
        points=[dict(offset=t) for t in [0,1,2,3,6,7]]
        plan=renderer.export_plan(dict(points=points),dict(target_duration=7,fps=25),0,5)
        assignments,_=renderer.point_frame_assignments(points,plan)
        self.assertEqual(plan['frame_count'],175)
        self.assertEqual(set(assignments.values()),set(range(6)))
        for frame,index in assignments.items():
            self.assertLessEqual(abs(renderer.frame_offset(plan,0,frame)-points[index]['offset']),7/174/2+1e-9)

    def test_dense_point_conflicts_do_not_add_frames(self):
        track=dict(points=[dict(offset=i) for i in range(100)])
        plan=renderer.export_plan(track,dict(target_duration=1,fps=25),0,99)
        self.assertEqual(plan['frame_count'],25)
        self.assertEqual(plan['point_conflicts'],75)
        self.assertEqual(plan['preserved_points'],25)
        self.assertEqual(plan['sequence_duration'],1)

    def test_target_duration_rounds_to_closest_frame(self):
        track=dict(points=[dict(offset=0),dict(offset=10)])
        plan=renderer.export_plan(track,dict(target_duration=1.01,fps=25),0,1)
        self.assertEqual(plan['frame_count'],25)
        self.assertLessEqual(abs(plan['duration_error']),.5/25)
        for value in [0,-1,float('nan')]:
            with self.assertRaises(ValueError):renderer.export_plan(track,dict(target_duration=value),0,1)

    def test_reject_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="nan" lon="2"/></trkseg></trk></gpx>')


if __name__ == '__main__':
    unittest.main()
