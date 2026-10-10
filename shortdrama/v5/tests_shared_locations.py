"""A shared building identity reaches both independently generated scene references."""
import json
import tempfile
import unittest
from pathlib import Path

from v5.media import cast


class TestSharedLocations(unittest.TestCase):
    def load(self, root, spec):
        folder = root / 'assetdesigner'
        folder.mkdir(exist_ok=True)
        (folder / cast.CONTRACT_NAME).write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        return cast._load_contract(root)[1]

    def contract(self):
        return {'shared_locations': {'月亭': '暖褐木柱，单层飞檐，矮木栏杆入口缺口，入口连接白玉直桥'},
                'assets': [{'name': '桥面外景', 'type': 'location', 'visible_locations': ['月亭'],
                            'prompt': '暮蓝雾中白玉直桥延伸到远处月亭'},
                           {'name': '月亭内景', 'type': 'location', 'visible_locations': ['月亭'],
                            'prompt': '暖黄光照亮木板，朝外可见暮蓝云海'},
                           {'name': '铜灯', 'type': 'prop', 'prompt': '方形铜框，圆弧提柄'}]}

    def test_real_contract_compiles_identical_structure_in_both_views(self):
        with tempfile.TemporaryDirectory() as folder:
            items = self.load(Path(folder), self.contract())
            exterior, interior, prop = items
            for item in (exterior, interior):
                compiled = cast._scene_prompt(item)
                for shape in ('暖褐木柱', '单层飞檐', '入口缺口', '白玉直桥'):
                    self.assertIn(shape, compiled)
                self.assertLess(compiled.index('单层飞檐'), compiled.index(item['prompt']))
            self.assertIn('暮蓝雾', cast._scene_prompt(exterior))
            self.assertIn('暖黄光', cast._scene_prompt(interior))
            self.assertNotIn('shared_location_specs', prop)
            self.assertNotIn('单层飞檐', cast._asset_prompt(prop))

    def test_shared_identity_changes_both_actual_image_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec = self.contract()
            old = [cast._scene_prompt(x) for x in self.load(root, spec)[:2]]
            spec['shared_locations']['月亭'] = '白石柱，双层屋顶，石栏杆入口连接白玉直桥'
            new = [cast._scene_prompt(x) for x in self.load(root, spec)[:2]]
            self.assertTrue(all(a != b for a, b in zip(old, new)))
            self.assertTrue(all(cast._fp_of(a) != cast._fp_of(b) for a, b in zip(old, new)))

    def test_undefined_reference_fails_instead_of_silently_omitting_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = self.contract()
            spec['assets'][0]['visible_locations'] = ['错名']
            with self.assertRaisesRegex(ValueError, '错名.*未定义'):
                self.load(Path(folder), spec)

    def test_legacy_scene_contract_keeps_its_exact_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            item = dict(name='孤立山谷', type='location', prompt='青灰岩壁与溪流，暮蓝天光')
            loaded = self.load(Path(folder), {'assets': [item]})[0]
            self.assertEqual(cast._scene_prompt(loaded), cast._scene_prompt(item))

    def test_named_list_and_object_compile_identically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec = self.contract()
            original = [cast._scene_prompt(x) for x in self.load(root, spec)[:2]]
            spec['shared_locations'] = [dict(name=k, appearance=v) for k, v in spec['shared_locations'].items()]
            self.assertEqual(original, [cast._scene_prompt(x) for x in self.load(root, spec)[:2]])

    def test_conflicting_duplicate_identity_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = self.contract()
            spec['shared_locations'] = [dict(name='月亭', appearance='木柱'), dict(name='月亭', appearance='石柱')]
            with self.assertRaisesRegex(ValueError, '冲突'):
                self.load(Path(folder), spec)
