# -*- coding: utf-8 -*-
"""一次性对照测试（不属管线、不动架构）：三镜连续段的 reference vs reference+keyframe 混用。

A = 已完成的 LN15（reference 模式产物，直接复用，不重烧）
B/C = keyframe 模式，first_frame = 上一镜**真实尾帧**（extract_last_frame）
对照 = A + 已完成的 LN16/LN17（全 reference 版）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v5.media import providers, video  # noqa: E402

ROOT = Path("projects/osmanthus-vow")
SB = ROOT / "scenedesigner" / "scenedesigner_ep1.md"
OUT = ROOT / "media" / "test_mixed"
OUT.mkdir(parents=True, exist_ok=True)


ASSET_NAMES = ["青荷油纸伞", "青玉莲花玉佩", "干桂花枝", "老桂花树下", "旧木箱",
               "青石巷", "姜念", "裴砚"]


def shot_row(n):
    for line in SB.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        # 列序：1镜头号 2景别 3角度 4运镜 5时长 6场景 7视觉风格 8画面描述 9落幅 10对白 11音效
        if len(cells) > 13 and cells[1] == str(n):
            return {"camera": "%s/%s" % (cells[2], cells[3]), "move": cells[4],
                    "secs": int(cells[5]), "style": cells[7], "visual": cells[8],
                    "tail": cells[9], "dialogue": cells[10], "audio": cells[11]}
    raise KeyError(n)


def _dequote(t):
    for name in ASSET_NAMES:
        t = t.replace("@" + name, name)
    return t.replace("@", "")


def to_prompt(s):
    """近似生产六段式：风格+镜头语言+画面+承接声明+落幅+音效（本测试三镜均无台词）。"""
    return (
        "竖屏 9:16，电影级真人国风短剧。%s 镜头：%s、%s。\n%s\n"
        "画面从上一镜的结尾构图与人物站位自然延续，机位与光线保持连续。\n"
        "本镜结束时的画面：%s\n环境音：%s\n"
        "No subtitles. No on-screen text. No watermark."
    ) % (s["style"], s["camera"], s["move"], _dequote(s["visual"]),
         _dequote(s["tail"]), s["audio"])


def render_keyframe(name, first_frame, s):
    dest = OUT / ("%s_kf.mp4" % name)
    if dest.exists():
        print("[test] %s 已在盘，跳过" % name)
        return dest
    p = to_prompt(s)
    print("[test] %s 提交 keyframe（%ds，first_frame %d 字节）"
          % (name, s["secs"], len(first_frame)))
    for attempt in range(1, 11):
        try:
            r = providers.submit_video(p, first_frame=first_frame, seconds=s["secs"],
                                       mode="keyframe")
            break
        except providers.QueueFullError:
            print("[test] %s 队列满（第 %d 次）→ 90s 后重试" % (name, attempt))
            import time
            time.sleep(90)
    else:
        raise SystemExit("[test] %s 队列持续满，放弃" % name)
    got = video._wait_one(r["video_id"], dest, rounds=90, interval=10, log=print)
    if not got:
        raise SystemExit("[test] %s 渲染失败/超时" % name)
    print("[test] %s done → %s" % (name, dest))
    return dest


def concat(parts, dest):
    lst = OUT / "list.txt"
    # concat demuxer 的相对路径按**清单文件所在目录**解析 ⇒ 必须写绝对路径
    lst.write_text("\n".join("file '%s'" % Path(p).resolve().as_posix() for p in parts),
                   encoding="utf-8")
    import subprocess
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(dest)])
    if r.returncode != 0:  # 参数不齐时重编码兜底
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-r", "24", str(dest)], check=True)
    print("[test] 拼接完成:", dest)


def main():
    clips = ROOT / "media" / "ep1" / "clips"
    a = clips / "LN15.mp4"
    s16, s17 = shot_row(16), shot_row(17)

    tail_a = video.extract_last_frame(a)
    assert tail_a, "LN15 尾帧抽取失败"
    b = render_keyframe("LN16", tail_a, s16)

    tail_b = video.extract_last_frame(b)
    assert tail_b, "LN16_kf 尾帧抽取失败"
    c = render_keyframe("LN17", tail_b, s17)

    concat([a, b, c], OUT / "mixed_kf.mp4")            # 混用版：reference + keyframe 链
    concat([a, clips / "LN16.mp4", clips / "LN17.mp4"],
           OUT / "all_reference.mp4")                   # 对照版：全 reference（现产）
    print("[test] ALL DONE")


if __name__ == "__main__":
    main()
