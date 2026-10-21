import unittest
import tempfile
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

    def test_reject_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            renderer.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="nan" lon="2"/></trkseg></trk></gpx>')


if __name__ == '__main__':
    unittest.main()
