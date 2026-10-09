import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from v5 import config,roles
from v5.media import storyboard,video_plan,prompt,assets

class TestCoverage(unittest.TestCase):
    def input(self,enabled,role='scenedesigner',mode='pack',limit=6):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'brief.json').write_text(json.dumps({'topic':'交玉','target_duration':'约12秒','audio_mode':'silent'}),encoding='utf-8')
            with patch.object(config,'SHOT_COVERAGE',enabled),patch.object(config,'VIDEO_MODE',mode),patch.object(config,'VIDEO_PACK_MAX_GROUP',limit),patch.object(config,'LOOSE_STORYBOARD',False),patch.object(config,'SHOTCHECK','off'):
                return roles.role_input(role,root,{'episode_index':1})
    def test_legacy_and_trial_do_not_conflict(self):
        old=self.input(False);new=self.input(True)
        self.assertIn('一次生成只包含一镜',old)
        self.assertNotIn('【试验·叙事镜头组织】',old)
        self.assertNotIn('一次生成只包含一镜',new)
        self.assertIn('【试验·叙事镜头组织】',new)
        self.assertIn('85%',new)
        self.assertIn('台词字数 ÷ 4',new)
    def test_review_and_unsupported_modes(self):
        self.assertIn('【试验·叙事镜头组织】',self.input(True,'reviewer'))
        for mode,limit in [('reference',6),('keyframe',6),('pack',1)]:
            self.assertNotIn('【试验·叙事镜头组织】',self.input(True,mode=mode,limit=limit))
    def test_scene_column_does_not_collapse_camera_rows(self):
        text='| 场次 | 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 画面描述 | 对白 | 音效 |\n|---|---|---|---|---|---|---|---|---|---|\n'
        for i,camera in enumerate(['跟住交玉右手','靠近接玉者','拉开看转身'],1):
            text+=f'| 1 | {i} | 中景 | 同侧平视 | {camera} | 4 | 月亭 | 0-4秒：甲右手持玉，乙接玉后留在右侧，甲空手转身。 | （无声，环境音） | 脚步声 |\n'
        shots=storyboard.parse(text)
        self.assertEqual([s['index'] for s in shots],[1,2,3])
        groups=video_plan.group_shots(shots,max_group=6)
        self.assertEqual(len(groups),1)
        rows,seconds=groups[0]
        with patch.object(config,'PACK_BGM',False):
            result=prompt.build_pack_prompt(rows,seconds,sum(seconds),ref_roles=[('character','甲')])
        for camera in ['跟住交玉右手','靠近接玉者','拉开看转身']:self.assertIn(camera,result)
        self.assertIn('镜头 3/3',result)
        self.assertNotIn('全程不切镜',result)

class TestReferenceDimensions(unittest.TestCase):
    def test_encoding_preserves_vendor_edge_bounds_and_original_file(self):
        import base64,io,hashlib
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'card.png'
            for size in [(5022,1640),(1640,5022),(864,1152),(128,128),(5000,100)]:
                with self.subTest(size=size):
                    Image.new('RGB',size,'red').save(path)
                    before=hashlib.sha256(path.read_bytes()).hexdigest()
                    uri=assets._data_uri(path)
                    with Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1]))) as im:
                        self.assertGreaterEqual(min(im.size),256)
                        self.assertLessEqual(max(im.size),5760)
                        self.assertGreaterEqual(im.width/im.height,0.4)
                        self.assertLessEqual(im.width/im.height,2.5)
                        if size==(5022,1640):
                            self.assertEqual(im.getpixel((im.width//2,0)),(255,255,255))
                            self.assertGreater(im.getpixel((im.width//2,im.height//2))[0],200)
                    self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

if __name__=='__main__':unittest.main()
