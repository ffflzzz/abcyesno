"""Reuse must follow actual input changes, without widening scoped renders."""
import tempfile
import unittest
import base64
import io
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from v5.media import video
from v5.media import jobs


class TestPackInputs(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_seam_anchor_is_final_frame_not_first_frame_of_final_second(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / 'tail.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=1.5',
                '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=0.5',
                '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
                '-map', '[v]', '-c:v', 'libx264', str(clip)], check=True, capture_output=True)
            uri = video.extract_last_frame(clip)
            with Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1]))) as image:
                red, _, blue = image.convert('RGB').getpixel((32, 32))
            self.assertGreater(blue, red + 100, '最后半秒是蓝色，倒数一秒起点却是红色')

    def test_normal_entry_resubmits_changed_input_and_reuses_unchanged_input(self):
        shots = [{'name': 'LN01', 'scene': 'A', 'seconds': 12}]
        for signature, expected_calls in [('old', 1), ('current', 0)]:
            with self.subTest(signature=signature), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                out = root / 'media/ep1'
                clip = out / 'clips/pack01.mp4'
                clip.parent.mkdir(parents=True)
                clip.write_bytes(b'video')
                jobs.save(out, {'pack01': {'state': 'completed', 'attempts': 1,
                    'shots': ['LN01'], 'declared_seconds': [12], 'local': str(clip),
                    'input_signature': signature}})
                with mock.patch.object(video, 'pack_input_signature', return_value='current'), \
                        mock.patch.object(video, 'pack_ref_images', return_value=(
                            ['https://cdn/location.png'], [('location', 'hall')])), \
                        mock.patch.object(video.providers, 'submit_video', return_value={'video_id': 'v2'}) as submit, \
                        mock.patch.object(video, '_wait_one', return_value=str(clip)):
                    result = video.submit_packs(root, shots, [], log=lambda *_: None)
                self.assertEqual(submit.call_count, expected_calls)
                self.assertEqual(result['pack01']['input_signature'], 'current')

    def test_local_pixels_change_even_when_public_url_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            image = root / 'location.png'
            image.write_bytes(b'old location')
            with mock.patch.object(video, 'pack_ref_images', return_value=(
                    ['https://cdn/location.png'], [('location', 'hall')])), \
                    mock.patch.object(video.assets, 'local_ref_path', return_value=image), \
                    mock.patch.object(video.style_mod, 'prepare_shots', side_effect=lambda r, g: g), \
                    mock.patch.object(video.style_mod, 'visual_block', return_value=''), \
                    mock.patch.object(video.prompt_mod, 'build_pack_prompt', return_value='scene'):
                before = video.pack_input_signature(root, [], [12], 1)
                (root / 'unused.png').write_bytes(b'unrelated')
                self.assertEqual(before, video.pack_input_signature(root, [], [12], 1))
                image.write_bytes(b'new location')
                self.assertNotEqual(before, video.pack_input_signature(root, [], [12], 1))

    def test_preceding_actual_tail_is_part_of_input(self):
        with mock.patch.object(video, 'extract_last_frame', side_effect=['tail-old', 'tail-new']), \
                mock.patch.object(video, 'pack_ref_images', side_effect=lambda r, g, prev_url, ep:
                                  ([prev_url], [('prev', 'previous')])), \
                mock.patch.object(video.style_mod, 'prepare_shots', side_effect=lambda r, g: g), \
                mock.patch.object(video.style_mod, 'visual_block', return_value=''), \
                mock.patch.object(video.prompt_mod, 'build_pack_prompt', return_value='scene'):
            self.assertNotEqual(video.pack_input_signature(Path('.'), [], [12], 1, Path('prev.mp4')),
                                video.pack_input_signature(Path('.'), [], [12], 1, Path('prev.mp4')))

    def test_scoped_render_does_not_compose_stale_untargeted_groups(self):
        shots = [{'name': 'LN01', 'scene': 'A', 'seconds': 12},
                 {'name': 'LN02', 'scene': 'B', 'seconds': 12}]
        records = {'pack01': {'shots': ['LN01'], 'declared_seconds': [12],
                              'input_signature': 'old'},
                   'pack02': {'shots': ['LN02'], 'declared_seconds': [12],
                              'input_signature': 'current'}}
        with mock.patch.object(video, 'submit_packs', return_value=records) as submit, \
                mock.patch.object(video, 'poll_all', return_value={
                    'pack01': '/old.mp4', 'pack02': '/new.mp4'}), \
                mock.patch.object(video, 'pack_input_signature', return_value='current'):
            _, done = video.run_packs(Path('.'), shots, [], only=['LN02'], log=lambda *_: None)
            self.assertEqual(done, {}, '下游也不能沿用失效前组的接续状态')
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.kwargs['only'], ['LN02'])

    def test_legacy_jobs_are_not_assigned_unverified_current_provenance(self):
        shots = [{'name': 'LN01', 'scene': 'A', 'seconds': 12}]
        records = {'pack01': {'shots': ['LN01'], 'declared_seconds': [12]}}
        with mock.patch.object(video, 'submit_packs', return_value=records), \
                mock.patch.object(video, 'poll_all', return_value={'pack01': '/legacy.mp4'}), \
                mock.patch.object(video, 'pack_input_signature') as signature:
            _, done = video.run_packs(Path('.'), shots, [], log=lambda *_: None)
            self.assertEqual(done, {'pack01': '/legacy.mp4'})
            signature.assert_not_called()
            self.assertNotIn('input_signature', records['pack01'])
