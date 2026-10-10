"""Duration preservation across the real project pack planning entry."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v5.media import compose, video_plan


class TestPackDuration(unittest.TestCase):
    def shots(self):
        return [dict(name=f'LN{i:02d}', seconds=sec, scene='bridge' if i < 6 else 'pavilion')
                for i, sec in enumerate([10, 8, 10, 8, 10, 8, 8], 1)]

    def test_sixty_second_project_cannot_be_compressed_to_forty_six(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'brief.json').write_text(json.dumps({'target_duration': '约60秒'}), encoding='utf-8')
            shots = self.shots()
            with patch.object(compose, 'TRIM', .15), patch.object(compose, 'XFADE', 0):
                groups = video_plan.group_project_shots(root, shots)
            self.assertEqual([s['name'] for g, _ in groups for s in g], [s['name'] for s in shots])
            seconds = [int(sum(d)) for _, d in groups]
            self.assertTrue(all(4 <= x <= 12 for x in seconds))
            self.assertGreaterEqual(sum(seconds) - .3 * len(groups), 60 * .85)
            self.assertEqual(sum(seconds), 56)
            self.assertEqual(len(groups[0][0]), 2, 'Keep useful in-request cuts rather than disabling packing')

    def test_per_episode_duration_uses_current_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'brief.json').write_text(json.dumps({'target_duration': '第1集约30秒，第2集约60秒'}, ensure_ascii=False), encoding='utf-8')
            with patch.object(compose, 'TRIM', 0), patch.object(compose, 'XFADE', 0):
                first = video_plan.group_project_shots(root, self.shots(), ep=1)
                second = video_plan.group_project_shots(root, self.shots(), ep=2)
            self.assertLess(sum(sum(d) for _, d in first), sum(sum(d) for _, d in second))
            self.assertGreaterEqual(sum(sum(d) for _, d in second), 51)

    def test_legal_fast_cuts_are_still_one_request(self):
        shots = [dict(name=f'LN{i}', seconds=.5, scene='bridge') for i in range(24)]
        groups = video_plan.group_shots(shots, max_group=24, min_total_seconds=11)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][1], [.5] * 24)

    def test_compose_loss_is_reserved(self):
        with patch.object(compose, 'TRIM', .2), patch.object(compose, 'XFADE', .3):
            self.assertAlmostEqual(video_plan.minimum_request_total(self.shots(), 60), 55.6)

    def test_speech_floor_adjustment_does_not_round_request_down_to_eleven(self):
        fitted = video_plan._pack_fit([10, 8], [0, 5.4])
        self.assertIsNotNone(fitted)
        self.assertAlmostEqual(sum(fitted), 12)
        self.assertEqual(int(sum(fitted)), 12)
        self.assertGreaterEqual(fitted[1], 5.4)
