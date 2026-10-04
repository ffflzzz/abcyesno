# -*- coding: utf-8 -*-
"""探针：**提示词细节增强**——把"运镜数值化"和"光路与材质"两类细节加进 pack 提示词，
   到底能不能换来看得见的差别。

起因（2026-10-02 与用户对话）：用户拿一份他实测有效的同模型（agnes）提示词对照我们的
pack 提示词，指出我们的细节全花在**身份与连续性**（参考图分工、承接、落幅、人数声明），
而**摄影与光学**这两类几乎没有：

    他：`镜头缓慢向前推近，速度0.5m/s，聚焦女性1面部`
    我们：`缓推`                       ← `prompt.camera_line` 只是把三列用逗号连起来
    他：`金属棒球棒表面反射灯光，形成亮白色高光条`
    我们：`呈现低饱和与深青、暗绿与水泥灰交织的压抑氛围`   ← 氛围词，不是光路

本探针只回答一句：**加上这两类细节，成片会不会真的变好。**
⛔ 不建判据、不改架构、不进 `pipeline`——变好了才谈接进去（用户定的流程）。

## 五臂（同组、同参考图、同总秒数、同风格块、**画面描述一字不动**）

    BASE     生产提示词原样重跑
    REPEAT   与 BASE 同一份文本再提交一次   ⇒ **噪声地板**（同提示词两轮的天然差异）
    CAM      只把「运镜」列换成数值化写法（速度 m/s + 行程 + 全程景别 + 静止段）
    LIGHT    只把「视觉风格」列换成光路写法（光位方向 + 高光落点 + 阴影落点 + 材质吃光/反光）
    BOTH     两列一起换

单变量靠**磁盘事实**自证：`conservation()` 逐镜比对 BASE 与本臂的 13 列，
只许 `camera` / `visual_style` 两列出差异，其余任何一格不同 ⇒ 当场终止。

## 读数（两个数，都要逐字引用）

1. **节拍执行率**——这一组的动作拍有没有因为加了细节被挤掉（四臂共同的原始拍清单）。
2. **细节落地率**——CAM 臂逐条问"机位有没有按声明位移/停下"，LIGHT 臂逐条问
   "声明的那处高光/阴影/吃光在画面里看不看得到"。
   判法沿用 `probe_beat_obedience` 那三道闸：**抄不出画面依据不算命中**、
   依据帧不是抽出来的帧不算、落在窗口外不算；每臂判两轮，两轮不一致的单列
   （同提示词两轮结果本就不同 ⇒ **只当报告，闸门另议**）。
   基准是 REPEAT 臂的翻转数：CAM/LIGHT 的增量不超过地板就不许说"变好了"。

用法：
    python scripts/probe_prompt_detail.py --dry-run     # 只看五臂差异与守恒，不花额度
    python scripts/probe_prompt_detail.py               # 下单 + 轮询落盘
    python scripts/probe_prompt_detail.py --score       # 落盘后打分（吃视觉额度）
产物全部落 `tmp/`，**不写 projects/**：
    tmp/PD_<项目>_<臂>.mp4 / tmp/PD_frames/... / tmp/PD_score_<臂>.json
    tmp/PD_same_ts_grid.jpg   同一时刻各臂并排（人眼验收用）
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                              # noqa: E402
from v5.media import prompt as prompt_mod                          # noqa: E402
from v5.media import providers, storyboard, style, video           # noqa: E402

OBEDIENCE = ROOT / "scripts" / "probe_beat_obedience.py"

#: 分镜 13 列里**参与组装**的、探针不许顺手改动的格（`camera`/`visual_style` 除外）。
FROZEN = ("visual", "dialogue", "sfx", "seconds", "scene", "tail",
          "shot_type", "angle", "text_shot", "join_note")

# ─── 两列的增强写法（**手工按镜起草**，不在运行时叫模型写——那会把变量污染成两个）──
# 键 = 镜名。换组/换项目时这里必须补齐，缺一条就响亮终止（不静默按 BASE 跑）。

CAM_CELLS = {
    # 第 4 场「楼顶天台 · 凌晨 04:00」
    "LN12": ("机位固定，位移速度0m/s，画面仅有极轻微手持呼吸（幅度不超过画幅1%），"
             "焦点落在两人之间那半步间距上，全程保持全景"),
    "LN13": ("缓慢向前推近，推进速度0.3m/s，行程0.5米，终点停在她双手裂开纸边的位置，"
             "全程保持近景、不换机位"),
    "LN14": ("俯视固定，位移0m/s，焦点从她低头看的视线落到脚踝与鞋口交接处，"
             "景深压在脚边，最后1秒完全静止"),
    "LN15": ("缓慢向前推近，推进速度0.25m/s，行程0.4米，全程保持中景，"
             "最后2秒机位完全静止不动"),
}

#: 原「视觉风格」列已含光位与色温（3000K 城市灯带），这里**只追加光路与材质**，
#: 不删原句——删了就等于同时改了两句，读数没法归因。
LIGHT_ADDED = {
    "LN12": ("光从画面右外侧水平射入，强度低；两人朝右一侧的颧骨、肩线与手背各有一条"
             "细窄暖橙高光边，背向灯的另侧面整体落在蓝灰阴影里；水泥地面是粗糙哑光的，"
             "人物脚下拖出短而软的影子"),
    "LN13": ("撕开的纸边在暖橙光里形成一道亮白切口，毛边一圈细高光；米色真丝吊带受光面"
             "泛暖并有丝质流动感，灰色针织开衫的绒面把光吃掉、边缘不发亮；发梢的水珠挂一点"
             "橙色高光"),
    "LN14": ("旧棉布鞋面呈哑光，鞋口内侧近乎全黑；脚踝旧疤那一小片皮肤的反光略强于周围"
             "皮肤；斜射的暖橙光把水泥颗粒照出密集的短影"),
    "LN15": ("悬在发梢的那颗水珠被远处灯带照成一颗亮橙亮点；仰头时下颌线成一条暖橙亮边、"
             "颈部投下硬阴影；睡裙下摆受光面泛暖、背光的褶皱内侧偏蓝灰"),
}

#: 空间几何（1002 深夜追加，用户点名单测这一条）。
#: ★ 全部按 `assets.json` 里「楼顶天台」登记的描述**追加尺寸与层次**，不改它已有的
#:   陈设（水泥地面／一侧锈旧水塔／上方一条晾衣绳／远处地平线城市灯带）——
#:   同一处空间写两个互相矛盾的源，是本项目记过的病（光源打架那次整段漂成暖光室内）。
#: ⚠️ 架构位置另议：这些是**场景级**属性，长期该进 location 资产描述（`scene_line`
#:   逐镜注入），而不是每镜的「视觉风格」列。探针先借这一列做单变量。
GEOM_CELLS = {
    "LN12": ("两人脚下是一片完全空的水泥地，从前景一直铺到女儿墙，进深约八米；"
             "齐胸高的女儿墙沿画面横向贯穿，墙顶有一条平直的亮边；"
             "锈旧水塔在左后方约六米、塔身压住画面左上；远处楼群天际线在墙头上排成三层高低；"
             "地平线以上整片是空旷的夜空"),
    "LN13": ("头顶上方约两米四有一条晾衣绳横拉贯穿画面，绳上夹子每隔约四十厘米一个；"
             "她身后约三米就是那道女儿墙，墙外只有夜空与远处灯带"),
    "LN14": ("水泥地面由一格一格的分缝铺开，缝与缝间隔约一米二，缝里积着灰；"
             "鞋底与地面贴合处压出一圈窄阴影，四周地面空到墙根"),
    "LN15": ("她仰头之后，画面上半部整片是空的夜空，只有晾衣绳的一条细黑线横穿；"
             "水塔的轮廓在右上方远处，比她小得多，衬出天台的开阔"),
}

ARMS = ("BASE", "REPEAT", "CAM", "LIGHT", "BOTH", "GEOM", "GEOM2")


def build_cells(group: list[dict], arm: str) -> list[dict]:
    """按臂产出**改过列**的镜列表（画面描述等其余格原样带过去）。"""
    out = []
    for s in group:
        n = s["name"]
        s2 = dict(s)
        if arm in ("CAM", "BOTH"):
            if n not in CAM_CELLS:
                raise SystemExit("[%s] 缺 %s 的「运镜」增强稿——补齐 CAM_CELLS 再跑，"
                                 "不许静默按 BASE 出图" % (arm, n))
            s2["camera"] = CAM_CELLS[n]
        if arm in ("LIGHT", "BOTH"):
            if n not in LIGHT_ADDED:
                raise SystemExit("[%s] 缺 %s 的「光路」增强稿——补齐 LIGHT_ADDED 再跑"
                                 % (arm, n))
            old = (s.get("visual_style") or "").strip()
            s2["visual_style"] = ((old.rstrip("。") + "。") if old else "") + LIGHT_ADDED[n]
        if arm in ("GEOM", "GEOM2"):
            if n not in GEOM_CELLS:
                raise SystemExit("[%s] 缺 %s 的「空间几何」增强稿——补齐 GEOM_CELLS 再跑"
                                 % (arm, n))
            old = (s.get("visual_style") or "").strip()
            s2["visual_style"] = ((old.rstrip("。") + "。") if old else "") + GEOM_CELLS[n]
        out.append(s2)
    return out


def conservation(base: list[dict], arm: list[dict], name: str) -> None:
    """单变量自证：除 `camera`/`visual_style` 外，任何一格不同就终止。"""
    bad = []
    for b, x in zip(base, arm):
        if b["name"] != x["name"]:
            bad.append("%s 镜序变了" % b["name"])
            continue
        for k in FROZEN:
            if (b.get(k) or "") != (x.get(k) or ""):
                bad.append("%s 的「%s」被改动" % (b["name"], k))
    if len(base) != len(arm):
        bad.append("镜数不同")
    if bad:
        raise SystemExit("[%s] 内容不守恒（探针作废）：%s" % (name, "；".join(bad[:5])))


#: **同一套问题问五臂**（⛔ 不许各臂一套：BASE 8 条、CAM 4 条的命中率没法比）。
#: 每条只问**增强稿新加的那一个特征**（问"有没有推近"是废话——BASE 的「缓推」本来就推），
#: 并给时间窗：依据帧落在窗外一律不算命中。窗口按 pack04 的分配秒 [3,3,2,4] 累计。
CHECKS = [
    {"kind": "运镜", "win": (0.0, 3.0),
     "text": "这一个镜头的机位有没有**任何平移或推拉**（应当停在原位，只有极轻微呼吸）",
     "neg": "这一个镜头的机位有没有**明显持续地推近或横移**（画面在整段里不断变大或不断侧移）"},
    {"kind": "运镜", "win": (3.0, 6.0),
     "text": "推近的过程有没有**在她双手裂开纸边的位置停住**（有明确的终点，不是一路推到底）",
     "neg": "这一个镜头有没有**一路推近到最后都不停**（结尾仍在继续变大）"},
    {"kind": "运镜", "win": (6.0, 8.0),
     "text": "焦点是不是落在**脚踝与鞋口交接处**（不是脸、不是全身），且最后一段机位静止",
     "neg": "这一个镜头的焦点是不是**主要在人脸或全身**，而不是脚边"},
    {"kind": "运镜", "win": (8.0, 12.0),
     "text": "后段（约第 10 秒起）机位有没有**完全静止不动**（画面停止位移）",
     "neg": "第 10 秒到结尾，画面有没有**一直在移动或一直景别在变大**"},
    {"kind": "光路", "win": (0.0, 3.0),
     "text": "两人朝向画面右侧那一侧的**颧骨或肩线上有没有一条细窄的暖橙高光边**，"
             "而背光的另一侧整体偏暗",
     "neg": "这两个人脸上和肩上是不是**受光均匀**（找不到明显的亮边，也没有一侧亮一侧暗）"},
    {"kind": "光路", "win": (3.0, 6.0),
     "text": "她手里撕开的**纸边有没有一道亮白的切口或一圈细高光**；"
             "灰色针织开衫是不是**吃光不发亮**（边缘无亮边）",
     "neg": "手里那张纸的边缘是不是**和纸面一样暗**（没有任何亮白边）"},
    {"kind": "光路", "win": (6.0, 8.0),
     "text": "旧布鞋面是不是**哑光**（无反光），而脚踝那一小片皮肤有比周围更强的反光",
     "neg": "鞋面是不是有**明显的反光亮点**（像打蜡或塑料那样发亮）"},
    {"kind": "光路", "win": (8.0, 12.0),
     "text": "她**仰头时的下颌线有没有一条暖橙亮边**、颈部有没有投下阴影",
     "neg": "她的下颌与颈部是不是**一片平光**（没有亮边也没有阴影）"},
]


def check_list(group: list[dict], bounds: list[tuple[float, float]]) -> list[dict]:
    """固定 8 问，按本组的真实秒界贴窗口（窗口改了 ⇒ 直接终止，不静默错判）。"""
    span = bounds[-1][1]
    need = max(c["win"][1] for c in CHECKS)
    if abs(need - span) > 0.01:
        raise SystemExit("CHECKS 的时间窗（到 %.1fs）与本组实际跨度（%.1fs）不符 —— "
                         "换组/换分配秒后要重开窗，不许按错窗口判" % (need, span))
    out = []
    for i, c in enumerate(CHECKS, 1):
        out.append({"no": i, "kind": c["kind"], "text": c["text"],
                    "lo": c["win"][0] - 0.5, "hi": c["win"][1] + 0.5,
                    "win": "%.0f-%.0fs" % c["win"]})
    return out


def beat_list(group: list[dict], bounds: list[tuple[float, float]]) -> list[dict]:
    """四臂共同的原始动作拍（`visual` 列没动 ⇒ 与生产逐字相同），窗口=全局轴上的拍窗。"""
    beats = []
    for s, (lo, _hi) in zip(group, bounds):
        for a, b, body in storyboard.split_beats(s.get("visual") or ""):
            beats.append({"no": len(beats) + 1, "shot": s["name"], "text": body,
                          "lo": lo + float(a) - 0.5, "hi": lo + float(b) + 0.5,
                          "win": "%.1f-%.1fs" % (lo + float(a), lo + float(b))})
    return beats


def load_obedience():
    spec = importlib.util.spec_from_file_location("obe", OBEDIENCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def motion_profile(clip: Path, fdir: Path, arm: str, step: float = 0.25) -> dict:
    """**不用模型的读数**：逐帧差曲线 ⇒ 机位/画面在不在动。

    为什么要有这一条：第一轮打分里 BASE 臂 8/8 全命中（它从没声明过那些运镜细节），
    证明"问模型有没有停住"这种单帧判定**没有区分度**。帧间差是确定性的：
    画面停止位移 ⇒ 相邻帧差塌到接近 0；持续推近 ⇒ 差值稳定不为零。
    混淆因素要写进读数里：这一组画面**本身在动**（撕纸、飘纸屑、风吹头发），
    所以"静止"不等于 0，只能**跟 BASE/REPEAT 的地板比相对高低**。
    """
    import subprocess

    from PIL import Image, ImageChops, ImageStat

    fdir.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(clip)],
        capture_output=True, text=True).stdout.strip() or 0) or 0.0
    ts, mats = [], []
    t = 0.0
    while t <= dur - 1e-6:
        p = fdir / ("m%05.2f.png" % t)
        if not p.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", str(clip),
                            "-frames:v", "1", "-vf", "scale=96:170", "-q:v", "3",
                            str(p)], capture_output=True)
        if p.exists():
            ts.append(round(t, 2))
            mats.append(Image.open(p).convert("L"))
        t = round(t + step, 2)
    diffs = [round(ImageStat.Stat(
        ImageChops.difference(mats[i + 1], mats[i])).mean[0], 2)
        for i in range(len(mats) - 1)]
    prof = {ts[i]: (diffs[i] if i < len(diffs) else None) for i in range(len(ts))}

    def w(lo: float, hi: float) -> float:
        vals = [v for k, v in prof.items() if lo <= k <= hi and v is not None]
        return round(sum(vals) / len(vals), 2) if vals else -1.0

    return {"arm": arm, "dur": dur, "profile": prof,
            # LN12 声明「位移0m/s，仅极轻呼吸」；LN13「推近后停在纸边」；
            # LN15「最后2秒机位完全静止」——这三段是 CAM 臂可机械验证的声明。
            "w1_0_3": w(0.5, 2.75), "w2_3_6": w(3.5, 5.75),
            "w4_8_12": w(8.5, 11.5), "w4_last2": w(10.0, 11.75)}


def run_motion(arms: list[str], project: str, out: Path) -> int:
    rows = []
    for arm in arms:
        clip = out / ("PD_%s_%s.mp4" % (project, arm))
        if not clip.exists():
            print("[pd] !! 缺成片 %s" % clip.name)
            continue
        r = motion_profile(clip, out / "PD_motion" / arm, arm)
        rows.append(r)
        print("[pd] %-7s 帧差 0-3s=%.2f  3-6s=%.2f  8-12s=%.2f  **后2秒=%.2f**  成片 %.2fs"
              % (arm, r["w1_0_3"], r["w2_3_6"], r["w4_8_12"], r["w4_last2"], r["dur"]),
              flush=True)
    base = [r["w4_last2"] for r in rows if r["arm"] in ("BASE", "REPEAT")]
    floor = max(base) if base else 0.0
    print("\n地板（BASE/REPEAT 后 2 秒帧差取高者）= %.2f" % floor)
    for r in rows:
        if r["arm"] in ("CAM", "BOTH"):
            d = r["w4_last2"] - floor
            print("  %s 后 2 秒帧差比地板 %+.2f ⇒ %s"
                  % (r["arm"], d,
                     "机位确实停住了（声明落地）" if d < -0.5 else
                     ("没测出差别——声明没落地或画面自身动作盖住了" if abs(d) <= 0.5
                      else "反而更动（反常，要人眼看）")))
    print("RESULT: 帧差读数完成（%d 臂）" % len(rows))
    return 0


ASK = {
    "beat": "对每一条，回答它有没有在画面里出现。命中必须：给一个**窗口内的时刻**"
            "（照抄格子上的黄字）+ 抄出那一格里看到的东西（谁、做什么、在画面哪里）。",
    "check": "对每一条，回答画面里有没有这件事。⛔ **只看一帧不许判命中**："
             "必须给出**两个不同的时刻**（t1 与 t2，照抄格子上黄字，都要落在窗口内），"
             "并写出 t1→t2 之间**发生了什么变化**（机位移动了什么、停在哪里 / "
             "哪一处比哪一处亮、亮边在画面哪个位置）。写不出两个时刻就判 hit=false。",
    "neg": "对每一条，回答画面里有没有这件事。命中必须给一个**窗口内的时刻**并抄出"
           "那一格看到的东西。",
}


def _parse_arr(txt: str) -> list[dict]:
    """稳健取 JSON 数组。上一轮打分就是栽在这：`\\[.*\\]` 抓到正文里的方括号就崩。"""
    s = re.sub(r"```(?:json)?", "", txt or "").strip()
    i = s.find("[")
    if i >= 0:
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[i:j + 1])
                    except Exception:                                    # noqa: BLE001
                        break
    objs = re.findall(r"\{[^{}]*\}", s)
    out = []
    for o in objs:
        try:
            out.append(json.loads(o))
        except Exception:                                    # noqa: BLE001
            continue
    return out


def judge2(obe, items: list[dict], frames, out_dir: Path, mode: str) -> list[dict]:
    """一次调用判一批；解析失败或缺号 ⇒ 重试两次，全拿不到就终止（不静默按零算）。"""
    from langchain_core.messages import HumanMessage

    from v5 import llm

    key = "text" if mode != "neg" else "neg"
    listing = "\n".join("%d）[%s] %s" % (b["no"], b["win"], b.get(key) or b["text"])
                        for b in items)
    sheets = obe.make_sheets(frames, out_dir)
    parts = [{"type": "text",
              "text": "帧时间戳范围：%.2fs ~ %.2fs，共 %d 帧，拼成 %d 张网格图，"
                      "每格左上角黄字是该帧的时刻（秒）。视觉接口单次最多 4 张图，"
                      "所以引用时**写黄字时刻**，不要写第几张图。"
                      % (frames[0][0], frames[-1][0], len(frames), len(sheets))}]
    parts += [{"type": "image_url", "image_url": {"url": obe.data_uri(sp)}}
              for sp in sheets]
    parts.append({"type": "text", "text": ASK[mode] + """
只输出 JSON 数组，每项形如：
{"beat": 3, "hit": true, "frame": "10.25", "quote": "第9.75秒她下颌线有一条暖橙亮边，到第11.5秒机位不再移动、亮边停在同一位置"}

未命中写 {"beat": 编号, "hit": false, "frame": "", "quote": ""}。抄不出来就不要判命中。

条目清单：
%s""" % listing})
    best: list[dict] = []
    for attempt in range(3):
        try:
            r = llm.chat_for("", 3000, temperature=0).invoke([HumanMessage(content=parts)])
            got = _parse_arr(r.content if hasattr(r, "content") else str(r))
        except Exception as e:                                    # noqa: BLE001
            print("[pd]   判定调用失败（%d/3）：%s" % (attempt + 1, str(e)[:80]), flush=True)
            continue
        if len(got) >= len(best):
            best = got
        if len({int(x.get("beat")) for x in best if isinstance(x, dict)
                and str(x.get("beat")).isdigit()}) >= len(items):
            break
        time.sleep(3)
    if not best:
        raise SystemExit("[pd] !! %s 判定三次都没回来 —— **终止**（不留「按全未命中继续跑」的"
                         "假读数：上一轮就是因为代码报错却照常打分）" % mode)
    return best


def light_profile(clip: Path, fdir: Path, arm: str, step: float = 0.5) -> dict:
    """**不靠模型的第二条读数**：直接量画面的明暗分布。

    为什么要有它：模型打分那一路已经证明不能用——同一份文本两轮从 0/7 跳到 7/7，
    翻转（9）比任何臂间差都大，拿它判光效等于掷硬币。
    而 LIGHT 臂声明的是**可测的物理量**：光从画面右外侧水平射入 ⇒ 右半边比左半边亮；
    颧骨/肩线有细窄暖橙高光边 ⇒ 暖橙高亮像素占比升高；仰头时下颌线有亮边、颈部有阴影
    ⇒ 画面下部出现一条亮带 + 其上方一块暗区。
    这三个数用 Pillow 就能算，确定、可复现、不吃额度。
    """
    import subprocess

    from PIL import Image, ImageStat

    fdir.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(clip)],
        capture_output=True, text=True).stdout.strip() or 0) or 0.0
    t, rows = 0.0, []
    while t <= dur - 1e-6:
        p = fdir / ("l%05.2f.png" % t)
        if not p.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", str(clip),
                            "-frames:v", "1", "-vf", "scale=120:213", "-q:v", "3",
                            str(p)], capture_output=True)
        if p.exists():
            im = Image.open(p).convert("RGB")
            w, h = im.size
            left = im.crop((0, 0, w // 2, h))
            right = im.crop((w // 2, 0, w, h))
            lp = list(left.getdata())
            rp = list(right.getdata())
            lum = lambda px: 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]  # noqa: E731
            asym = (sum(map(lum, rp)) / len(rp)) - (sum(map(lum, lp)) / len(lp))
            # 暖橙高亮：R 明显大于 B、且够亮
            warm = sum(1 for px in lp + rp if px[0] - px[2] > 40 and lum(px) > 110)
            warm_frac = 100.0 * warm / (len(lp) + len(rp))
            hi = sum(1 for px in lp + rp if lum(px) > 175)
            rows.append((round(t, 2), round(asym, 2), round(warm_frac, 2),
                         round(100.0 * hi / (len(lp) + len(rp)), 2)))
        t = round(t + step, 2)

    def w(lo: float, hi_: float, idx: int) -> float:
        vals = [r[idx + 1] for r in rows if lo <= r[0] <= hi_]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {"arm": arm,
            "asym_0_3": w(0.0, 3.0, 0), "warm_0_3": w(0.0, 3.0, 1),
            "asym_8_12": w(8.0, 12.0, 0), "warm_8_12": w(8.0, 12.0, 1),
            "hi_8_12": w(8.0, 12.0, 2)}


def run_light(arms: list[str], project: str, out: Path) -> int:
    rows = []
    for arm in arms:
        clip = out / ("PD_%s_%s.mp4" % (project, arm))
        if not clip.exists():
            print("[pd] !! 缺成片 %s" % clip.name)
            continue
        r = light_profile(clip, out / "PD_light" / arm, arm)
        rows.append(r)
        print("[pd] %-7s 0-3s 右-左亮度差=%+6.2f 暖橙高亮占比=%5.2f%%   "
              "8-12s 右-左亮度差=%+6.2f 暖橙高亮占比=%5.2f%% 亮部占比=%5.2f%%"
              % (arm, r["asym_0_3"], r["warm_0_3"], r["asym_8_12"],
                 r["warm_8_12"], r["hi_8_12"]), flush=True)

    def band(key: str) -> tuple:
        v = [r[key] for r in rows if r["arm"] in ("BASE", "REPEAT")]
        return (min(v), max(v)) if v else (0, 0)

    print("\n地板 = BASE/REPEAT 两次的取值区间（同文本的固有差异）")
    for key, label in (("asym_0_3", "0-3s 右比左亮多少"),
                       ("warm_0_3", "0-3s 暖橙高亮占比"),
                       ("asym_8_12", "8-12s 右比左亮多少"),
                       ("warm_8_12", "8-12s 暖橙高亮占比")):
        lo, hi = band(key)
        print("  %s：地板 %.2f~%.2f ｜CAM %.2f ｜LIGHT %.2f ｜BOTH %.2f ｜BASE %.2f"
              % (label, lo, hi,
                 next((r[key] for r in rows if r["arm"] == "CAM"), 0),
                 next((r[key] for r in rows if r["arm"] == "LIGHT"), 0),
                 next((r[key] for r in rows if r["arm"] == "BOTH"), 0),
                 next((r[key] for r in rows if r["arm"] == "BASE"), 0)))
    print("读法：只有 CAM/LIGHT/BOTH 里**声明了这一项的那一臂**超出地板区间，才算声明落地；"
          "没声明的那臂必须落在区间内（阴性对照）。")
    print("RESULT: 光效读数完成（%d 臂）" % len(rows))
    return 0


def geom_metrics(clip: Path, fdir: Path, arm: str, step: float = 0.5) -> dict:
    """**空间几何的机械读数**（不靠模型）：沿竖直方向的亮度剖面。

    量什么（对应 GEOM 臂声明的三件事）：
    · `hedges` —— **横贯画面的长直边**有几条。判据用"整行平均亮度的跳变"：
      一条从左边拉到右边的墙头／绳／天际线，会让那一行的均值整体抬或塌。
      （第一版用的是"一行里 55% 像素梯度>18"，缩到 120 宽之后**一条都触发不了**
      ——所有臂全读 0，那是量法设错，不是画面没边。改成行均值跳变后先要在
      已有臂上确认有分辨力才用。）
    · `top_std` —— 画面上三分之一的行均值**波动**。声明"地平线以上整片是空旷夜空"
      ⇒ 这个数应该小。
    · `bot_std` —— 下三分之一同样的波动（近处水泥地的分缝、腿、鞋会带来起伏）。
      声明了前景铺到墙根 ⇒ 这个数与 `top_std` 的差应该拉开。
    ⚠️ 代理指标，只看相对带宽外的差异。
    """
    import subprocess

    from PIL import Image

    fdir.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(clip)],
        capture_output=True, text=True).stdout.strip() or 0) or 0.0
    W, H = 120, 213
    per_frame = []
    t = 0.0
    while t <= dur - 1e-6:
        p = fdir / ("g%05.2f.png" % t)
        if not p.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", str(clip),
                            "-frames:v", "1", "-vf", "scale=%d:%d" % (W, H), "-q:v", "3",
                            str(p)], capture_output=True)
        if p.exists():
            px = list(Image.open(p).convert("L").getdata())
            rowmean = [sum(px[y * W:(y + 1) * W]) / W for y in range(H)]
            jumps = [abs(rowmean[y + 1] - rowmean[y]) for y in range(H - 1)]
            med = sorted(jumps)[len(jumps) // 2]
            cut = max(2.0, med * 3.0)          # 至少 2 个灰阶的整行跳变才算"边"
            edges, run = 0, False
            for j in jumps:
                if j >= cut and not run:
                    edges += 1
                    run = True
                elif j < cut:
                    run = False
            third = H // 3
            top = rowmean[:third]
            bot = rowmean[-third:]

            def sd(v):
                m = sum(v) / len(v)
                return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
            per_frame.append((round(t, 2), edges, round(sd(top), 2), round(sd(bot), 2)))
        t = round(t + step, 2)

    def w(lo: float, hi: float, idx: int) -> float:
        vals = [r[idx] for r in per_frame if lo <= r[0] <= hi]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {"arm": arm, "nframes": len(per_frame),
            "edges_0_3": w(0.5, 2.5, 1), "topstd_0_3": w(0.5, 2.5, 2),
            "edges_8_12": w(8.5, 11.5, 1), "topstd_8_12": w(8.5, 11.5, 2),
            "botstd_all": w(0.5, 11.5, 3), "frames": per_frame}


def run_geom(arms: list[str], project: str, out: Path) -> int:
    rows = []
    for arm in arms:
        clip = out / ("PD_%s_%s.mp4" % (project, arm))
        if not clip.exists():
            print("[pd] !! 缺成片 %s（先下单这一臂）" % clip.name)
            continue
        r = geom_metrics(clip, out / "PD_geom2" / arm, arm)
        rows.append(r)
        print("[pd] %-7s 帧%d  全景段 横边=%4.2f 条/帧 上半波动=%5.2f   "
              "仰拍段 横边=%4.2f 条/帧 上半波动=%5.2f   下半波动=%5.2f"
              % (arm, r["nframes"], r["edges_0_3"], r["topstd_0_3"],
                 r["edges_8_12"], r["topstd_8_12"], r["botstd_all"]), flush=True)
    spread = [max((r[k] for r in rows), default=0) - min((r[k] for r in rows), default=0)
              for k in ("edges_0_3", "topstd_0_3", "edges_8_12", "topstd_8_12")]
    print("\n分辨力自检：各指标在 %d 条臂之间的**极差** %s"
          % (len(rows), ["%.2f" % x for x in spread]))

    def val(arm, key):
        return next((r[key] for r in rows if r["arm"] == arm), None)

    # 两样本比较：**各自文本内部的带宽**都要摆出来，才知道差值是信号还是抖。
    # （实测教训：`横边条数` 在 BASE 与 REPEAT 这两个**同一份文本**的成片之间
    #   就能差到 7.6 条/帧，比任何臂间差都大 ⇒ 这个指标在本组上不可用。）
    old_band = [("BASE", "REPEAT"), ("CAM",), ("LIGHT",), ("BOTH",)]
    print("\n同一份文本两次的带宽（真噪声）：BASE %.2f vs REPEAT %.2f ｜ "
          "GEOM %.2f vs GEOM2 %.2f"
          % (val("BASE", "edges_0_3") or 0, val("REPEAT", "edges_0_3") or 0,
             val("GEOM", "edges_0_3") or 0, val("GEOM2", "edges_0_3") or 0))
    for key, label in (("edges_0_3", "全景段 横边条数"),
                       ("topstd_0_3", "全景段 上半亮度波动"),
                       ("edges_8_12", "仰拍段 横边条数"),
                       ("topstd_8_12", "仰拍段 上半亮度波动"),
                       ("botstd_all", "全片 下半亮度波动")):
        b, r_ = val("BASE", key), val("REPEAT", key)
        g, g2 = val("GEOM", key), val("GEOM2", key)
        if None in (b, r_) or (g is None and g2 is None):
            continue
        gs = [x for x in (g, g2) if x is not None]
        gm = sum(gs) / len(gs)
        old_w = abs(b - r_)
        new_w = abs(gs[0] - gs[1]) if len(gs) > 1 else old_w
        band = max(old_w, new_w)
        diff = gm - (b + r_) / 2.0
        print("  %s：旧文本 %.2f,%.2f（带 %.2f）｜新文本 %s（带 %.2f）｜差 %+0.2f ⇒ %s"
              % (label, b, r_, old_w,
                 "，".join("%.2f" % x for x in gs), new_w, diff,
                 "超出带宽 = 有读数" if abs(diff) > band
                 else "落在带宽内 = **这把尺子测不出**（带宽 %.2f ≥ 差值 %.2f）"
                      % (band, abs(diff))))
    print("RESULT: 空间几何读数完成（%d 臂）" % len(rows))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="leak-upstairs-1002")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--group", default="pack04")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--grid", action="store_true",
                    help="把各臂同一时刻的帧并排成一张图（人眼验收）")
    ap.add_argument("--motion", action="store_true",
                    help="只算帧间差曲线（不调模型、不吃额度）")
    ap.add_argument("--light", action="store_true",
                    help="只算画面明暗分布（不调模型、不吃额度）")
    ap.add_argument("--geom", action="store_true",
                    help="只算空间几何代理数：长水平边条数 + 上半部有内容占比")
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()

    root = ROOT / "projects" / a.project
    epdir = root / "media" / ("ep%d" % a.ep)
    out = ROOT / "tmp"
    out.mkdir(exist_ok=True)
    want = [x.strip().upper() for x in a.arms.split(",") if x.strip()]
    if a.motion:
        return run_motion(want, a.project, ROOT / "tmp")
    if a.light:
        return run_light(want, a.project, ROOT / "tmp")
    if a.geom:
        return run_geom(want, a.project, ROOT / "tmp")

    md = (root / "scenedesigner" / ("scenedesigner_ep%d.md" % a.ep)).read_text(encoding="utf-8")
    by = {s["name"]: s for s in storyboard.parse(md)}
    jobs = json.loads((epdir / "video_jobs.json").read_text(encoding="utf-8"))
    jobs = jobs.get("jobs", jobs)
    if a.group not in jobs:
        raise SystemExit("没有组 %s（可选：%s）" % (a.group, ",".join(sorted(jobs))))
    rec = jobs[a.group]
    names, declared = rec["shots"], rec["declared_seconds"]
    total = sum(declared)
    base_group = prompt_mod.resolve_styles([by[n] for n in names])

    stills_raw = json.loads((epdir / "stills.json").read_text(encoding="utf-8"))
    items = stills_raw.get("items") if isinstance(stills_raw.get("items"), dict) else stills_raw
    own = {n: (items.get(n) or {}).get("url") for n in names}
    if not all(own.values()):
        raise SystemExit("缺静帧 URL：%s" % [n for n, u in own.items() if not u])
    # 参考图走**生产那一份槽位判据**；探针不跨组，锚帧一律不给（五臂同图 ⇒ 图不是变量）
    urls, roles = video.pack_ref_images(root, base_group, own, prev_url=None, ep=a.ep)
    sb = style.wrap(style.load(root))

    print("[pd] %s / %s = %s 分配秒 %s 合计 %ds；参考图 %d 张"
          % (a.project, a.group, "+".join(names), declared, total, len(urls)))
    print("[pd]   画幅 %s｜模式档 %s（探针固定按 reference 提交）｜key %d 条｜提交间隔 %ss"
          % (config.ASPECT_RATIO, config.VIDEO_MODE, len(config.AGNES_API_KEYS or [1]),
             config.video_submit_interval_per_key()))

    prompts: dict[str, str] = {}
    groups: dict[str, list[dict]] = {}
    bounds: list[tuple[float, float]] = []
    left = 0.0
    for s, d in zip(base_group, declared):
        bounds.append((left, left + float(d)))
        left += float(d)
    for arm in want:
        g2 = build_cells(base_group, arm)
        conservation(base_group, g2, arm)
        groups[arm] = g2
        prompts[arm] = prompt_mod.build_pack_prompt(g2, declared, total, style_block=sb,
                                                    ref_roles=roles)
        print("[pd] %-7s 提示词 %5d 字（BASE %+d 字）"
              % (arm, len(prompts[arm]),
                 len(prompts[arm]) - len(prompts.get("BASE", prompts[want[0]]))))

    if a.dry_run:
        print("\n===== BASE 与 BOTH 的逐字差异（应当只有两列）=====\n")
        for line in difflib.unified_diff(prompts["BASE"].splitlines(),
                                         prompts[want[-1]].splitlines(),
                                         "BASE", want[-1], lineterm="", n=0):
            print(line[:300])
        print("\n===== 每臂要逐条核对的清单条数（五臂同一套）=====")
        print("[pd] 原始动作拍 %d 条 + 摄影/光学固定 8 问（窗口 %s）"
              % (len(beat_list(base_group, bounds)),
                 " ".join("%.0f-%.0f" % c["win"] for c in CHECKS)))
        return 0

    if a.score:
        obe = load_obedience()
        beats_all = beat_list(base_group, bounds)
        checks = check_list(base_group, bounds)
        rows = []
        for arm in want:
            clip = out / ("PD_%s_%s.mp4" % (a.project, arm))
            if not clip.exists():
                print("[pd] !! 缺成片 %s（先不带 --score 跑一遍）" % clip.name)
                continue
            frames = obe.grab_frames(clip, out / "PD_frames" / arm, 0.0, bounds[-1][1])
            fts = [t for t, _ in frames]

            def judged(lst, mode, sub):
                vs = judge2(obe, lst, frames, out / "PD_frames" / (arm + sub), mode)
                hit, rej = obe.score(lst, vs, fts)
                # `score` 只回命中数与落选清单 ⇒ 命中**集合**用全集减落选反推
                return set(lst and {x["no"] for x in lst}) - {r[0] for r in rej}, hit, rej

            res, contradictions = {}, []
            for rnd in range(1, a.rounds + 1):
                b_ids, hb, _rb = judged(beats_all, "beat", "_b")
                p_ids, hp, _rp = judged(checks, "check", "_c")
                n_ids, hn, _rn = judged(checks, "neg", "_n")
                both = p_ids & n_ids          # 正反都"看得到" ⇒ 这条问话没有区分度
                res[rnd] = (hb, len(p_ids - n_ids), len(both))
                contradictions.append(sorted(both))
                (out / ("PD_score_%s_r%d.json" % (arm, rnd))).write_text(json.dumps(
                    {"beats_hit": hb, "beats_total": len(beats_all),
                     "detail_true": len(p_ids - n_ids), "detail_pos": hp,
                     "矛盾（正反都命中）": sorted(both),
                     "check_list": checks, "beat_list": beats_all},
                    ensure_ascii=False, indent=1), encoding="utf-8")
                print("[pd] %-7s 第%d轮：节拍 %d/%d ｜细节 真命中 %d（正向 %d，"
                      "其中矛盾 %d）" % (arm, rnd, hb, len(beats_all),
                                        len(p_ids - n_ids), hp, len(both)), flush=True)
            (b1, d1, x1), (b2, d2, x2) = res[1], res[2]
            flip = abs(b1 - b2) + abs(d1 - d2)
            rows.append((arm, len(beats_all), b1, b2, len(checks), d1, d2, flip,
                         max(x1, x2)))
        print("\n| 臂 | 节拍命中 | 两轮 | 细节真命中 | 清单 | 两轮 | 翻转 | "
              "矛盾条数（正反都命中）|")
        print("|---|---|---|---|---|---|---|---|")
        for arm, nb, b1, b2, nc, d1, d2, flip, x in rows:
            print("| %s | %d/%d | %d,%d | %d/%d | %d | %d,%d | %d | %d |"
                  % (arm, b1, nb, b1, b2, d1, nc, nc, d1, d2, flip, x))
        base_hits = [r[5] for r in rows if r[0] in ("BASE", "REPEAT")]
        floor = max((r[7] for r in rows if r[0] in ("BASE", "REPEAT")), default=0)
        ref = max(base_hits) if base_hits else 0
        gains = ["%s %+d" % (r[0], r[5] - ref) for r in rows
                 if r[0] in ("CAM", "LIGHT", "BOTH")]
        print("\n同文本两轮的翻转（噪声地板）= %d 条；BASE/REPEAT 细节命中取高者 = %d/%d"
              % (floor, ref, rows[0][4] if rows else 0))
        print("各臂相对地板的增量：%s" % "，".join(gains))
        print("⛔ 增量不超过地板 ⇒ 那是噪声，不许说变好了、更不许进架构。")
        print("RESULT: 打分完成（%d 臂）" % len(rows))
        return 0

    if a.grid:
        # **人眼验收**：同一时刻把各臂并排成一张图。模型打分只是旁证，
        # 运镜有没有停住、高光在不在，最终要人眼看（本项目定案：审的对象是交付物）。
        from PIL import Image, ImageDraw

        obe = load_obedience()
        times = [1.0, 4.0, 6.5, 9.5, 11.0]
        cell_w, cell_h = 270, 480
        pad = 26
        cols = [x for x in want if (out / ("PD_%s_%s.mp4" % (a.project, x))).exists()]
        if not cols:
            raise SystemExit("tmp/ 下没有本探针的成片，先跑一遍下单")
        grid = Image.new("RGB", (60 + cell_w * len(cols) + 6,
                                 len(times) * (cell_h + pad) + pad), (20, 20, 20))
        d = ImageDraw.Draw(grid)
        fdir = out / "PD_grid_frames"
        for ri, t in enumerate(times):
            d.text((6, ri * (cell_h + pad) + 4), "%.1fs" % t, fill=(255, 255, 0))
            for ci, arm in enumerate(cols):
                clip = out / ("PD_%s_%s.mp4" % (a.project, arm))
                p = fdir / ("g_%s_%05.2f.jpg" % (arm, t))
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    import subprocess
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t),
                                    "-i", str(clip), "-frames:v", "1",
                                    "-vf", "scale=%d:%d" % (cell_w, cell_h),
                                    "-q:v", "3", str(p)], capture_output=True)
                if p.exists():
                    grid.paste(Image.open(p).convert("RGB"),
                               (60 + ci * (cell_w + 2), ri * (cell_h + pad) + pad))
                d.text((60 + ci * (cell_w + 2) + 4, ri * (cell_h + pad) + 4), arm,
                       fill=(255, 255, 0))
        dest = out / ("PD_%s_%s_grid.jpg" % (a.project, a.group))
        grid.save(dest, quality=92)
        print("RESULT: 并排图已写 %s（%d 臂 × %d 个时刻）" % (dest, len(cols), len(times)))
        return 0

    # ── 下单 ──
    keys = list(config.AGNES_API_KEYS) or [None]
    ar = config.ASPECT_RATIO
    print("[pd] 提交 %d 臂 × %ds ⇒ 预计 %d-%d 分钟（封顶 45 分钟，超时保留已落盘的臂）"
          % (len(want), total, len(want) * 3, len(want) * 9), flush=True)
    ids = {}
    for i, arm in enumerate(want):
        for attempt in range(10):
            key = keys[(i + attempt) % len(keys)]
            try:
                r = providers.submit_video(prompts[arm], mode="reference", images=urls,
                                           seconds=total, aspect_ratio=ar,
                                           timeout=300, key=key)
                ids[arm] = (r.get("video_id"), time.time(), key)
                print("[pd] %s submitted id=%s key=%s…" % (arm, r.get("video_id"),
                                                           str(key)[:8]), flush=True)
                break
            except Exception as e:                                    # noqa: BLE001
                wait = min(90, 20 * (1 + attempt // max(1, len(keys))))
                print("[pd] %s 提交失败（%d/10）：%s → %ds 后换 key 重试"
                      % (arm, attempt + 1, str(e)[:70], wait), flush=True)
                time.sleep(wait)
        time.sleep(int(config.video_submit_interval_per_key()))

    pending = dict(ids)
    deadline = time.time() + 60 * 45
    import httpx
    while pending and time.time() < deadline:
        time.sleep(15)
        for arm, (vid, t0, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception as e:                                    # noqa: BLE001
                print("[pd] %s 查询异常：%s" % (arm, str(e)[:60]), flush=True)
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                dest = out / ("PD_%s_%s.mp4" % (a.project, arm))
                try:
                    with httpx.Client(timeout=180, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[pd] %s done → %s（%.1f MB，%.0fs）"
                          % (arm, dest.name, dest.stat().st_size / 1e6, time.time() - t0),
                          flush=True)
                    pending.pop(arm)
                except Exception as e:                                # noqa: BLE001
                    print("[pd] %s 下载失败：%s" % (arm, str(e)[:80]), flush=True)
            elif st in ("failed", "error"):
                print("[pd] %s FAILED：%s" % (arm, str(q.get("error"))[:120]), flush=True)
                pending.pop(arm)
    print("RESULT: %d/%d 臂落盘；打分跑 --score" % (len(want) - len(pending), len(want)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
