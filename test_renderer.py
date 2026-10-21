import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from PIL import Image
import render_gpx_png_sequence as renderer


class RendererTests(unittest.TestCase):
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

    def test_reject_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="nan" lon="2"/></trkseg></trk></gpx>')


if __name__ == '__main__':
    unittest.main()
