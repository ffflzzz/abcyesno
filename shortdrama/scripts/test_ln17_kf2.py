# -*- coding: utf-8 -*-
"""混用模式测试的最后一镜：LN17 用**本镜静帧**当首帧（keyframe 正确姿势）。

正反打镜不能直连上一镜尾帧（会把不存在的角色凭空摇出来，见 memory
agnes-video-modes-and-continuity）⇒ 首帧必须用画面对不对得上的本镜静帧。
幂等：LN17_kf2.mp4 已在盘即跳过提交。成功后自动重拼 mixed_kf.mp4。
"""
import base64
import importlib.util
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v5 import config
from v5.media import providers, video

ROOT = Path("projects/osmanthus-vow")
OUT = ROOT / "media" / "test_mixed"
CLIPS = ROOT / "media" / "ep1" / "clips"

spec = importlib.util.spec_from_file_location(
    "t", str(Path(__file__).with_name("test_mixed_modes_3shots.py")))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main() -> int:
    dest = OUT / "LN17_kf2.mp4"
    if not dest.exists():
        s17 = m.shot_row(17)
        uri = "data:image/jpeg;base64," + base64.b64encode(
            (ROOT / "media/ep1/stills/LN17.jpg").read_bytes()).decode()
        prompt = m.to_prompt(s17).replace(
            "画面从上一镜的结尾构图与人物站位自然延续，机位与光线保持连续。",
            "画面从给定起始画面自然动起来，人物、服装、道具、光线保持一致不漂移。")
        r = None
        for rnd in range(1, 21):
            for i, k in enumerate(config.AGNES_API_KEYS):
                try:
                    r = providers.submit_video(prompt, first_frame=uri,
                                               seconds=s17["secs"],
                                               mode="keyframe", key=k)
                    print("[kf2] 第%d轮 key#%d 提交成功" % (rnd, i + 1), flush=True)
                    break
                except providers.QueueFullError:
                    print("[kf2] 第%d轮 key#%d 队列满" % (rnd, i + 1), flush=True)
            if r:
                break
            time.sleep(300)
        else:
            print("[kf2] 100 分钟仍队列满，放弃", flush=True)
            return 1
        got = video._wait_one(r["video_id"], dest, rounds=90, interval=10, log=print)
        if not got:
            print("[kf2] 渲染失败/超时", flush=True)
            return 1
    else:
        print("[kf2] 已在盘，跳过渲染", flush=True)

    m.concat([CLIPS / "LN15.mp4", OUT / "LN16_kf.mp4", dest], OUT / "mixed_kf.mp4")
    print("[kf2] ALL DONE → media/test_mixed/mixed_kf.mp4", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
