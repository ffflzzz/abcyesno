"""One vision call per rendered pack, with chronological frames and seam context.

This is sampled visual evidence, not proof of every event or an audio review.
Reports are cached by actual media and input content; failures remain unknown.
No automatic regeneration is performed until the detector is validated.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage

from . import clipqc, qc

VERSION = 3
RULE = """你是短剧视频验收员。图片是按时间排列的抽帧，不是同时发生的拼图。
previous是上一组末帧，仅用于接续；current是本组首、中、尾3张抽帧。
首抽帧取约0.3秒：动作可以正在开始，不能要求区间末才完成的动作在首帧已经完成。
brief描述全片，当前剧情阶段以本组分镜为准；不能把后组的跟随、进入或结局提前要求在本组完成。
依据brief与本组分镜，只报告图片直接证明的错误：多出角色、关键道具形状或归属改变、
明确要求的移动方向相反、明显的跨组状态跳变。允许正常切镜、特写、人物出画和遮挡。
角色左右手指角色自身左右，不是观众画面左右；不能辨认时不要猜。
空手仅表示没有持物，不要求手臂垂放；抬手、弯臂、抱臂或手被遮挡不能证明持物。
报空手违规必须指出图片里实际可见的物件，不能把姿态或疑似持物当作证据。
未在抽帧中看到某事件不能证明事件遗漏。不能猜抽帧对应的具体节拍时间，不能判断对白、声音或两张图之间的完整动作。
不要把前一组与当前组的人数相加，不要把特写里的局部身体算成额外人物。
返回JSON：{"status":"pass|fail|unknown","observations":["可见事实"],
"issues":[{"desc":"具体错误及其违反的要求","frames":["current1","current3"]}],
"limitations":["看不清或无法从抽帧验证的内容"]}。
fail必须有具体图片证据；没有可见错误可pass，但仅代表抽帧未发现问题。
"""


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _normalize(raw, labels: set[str]) -> dict:
    if not isinstance(raw, dict) or raw.get("status") not in {"pass", "fail", "unknown"}:
        return {"status": "unknown", "issues": [], "reason": "invalid verdict"}
    issues = []
    for item in raw.get("issues", []) if isinstance(raw.get("issues"), list) else []:
        if not isinstance(item, dict):
            continue
        refs = item.get("frames")
        desc = item.get("desc")
        if (isinstance(desc, str) and desc.strip() and isinstance(refs, list)
                and refs and all(isinstance(r, str) and r in labels for r in refs)
                and any(r.startswith("current") for r in refs)):
            issues.append({"desc": desc, "frames": refs})
    status = raw["status"]
    if status == "fail" and not issues:
        status = "unknown"
    elif issues:
        status = "fail"
    observations = raw.get("observations")
    if status == "pass" and not (isinstance(observations, list) and observations):
        status = "unknown"
    return {"status": status, "issues": issues,
            "observations": observations if isinstance(observations, list) else [],
            "limitations": raw.get("limitations", [])}


def review(clip: Path, previous: Path | None, shots: list[dict], brief: dict,
           work_dir: Path) -> dict:
    # 当前Agnes聊天模型每请求最多4图：当前首/中/尾 + 前组末帧。
    frames = clipqc.grab(clip, work_dir / "current", n=3)
    if len(frames) != 3:
        return {"status": "unknown", "issues": [], "reason": "frame extraction incomplete"}
    images = []
    if previous:
        tail = clipqc.grab(previous, work_dir / "previous", n=3)
        if not tail:
            return {"status": "unknown", "issues": [], "reason": "previous frame unavailable"}
        images.append(("previous", tail[-1]))
    images += [("current%d" % i, p) for i, p in enumerate(frames, 1)]
    content = [{"type": "text", "text": RULE + "\n制作要求与本组分镜：\n" +
                json.dumps({"brief": brief, "shots": shots}, ensure_ascii=False)}]
    for label, p in images:
        content += [{"type": "text", "text": label},
                    {"type": "image_url", "image_url": {"url": qc._data_uri(str(p))}}]
    response = qc.chat_for("", 1600, temperature=0).invoke([HumanMessage(content=content)])
    raw = qc._json_from(response.content)
    return _normalize(raw, {label for label, _ in images})


def audit(root: Path, clips: dict[str, str], shots: list[dict], *, ep: int = 1,
          only: list[str] | None = None, log=print) -> dict:
    """Inspect each current physical group once; include the real preceding group.

    only narrows paid calls but never removes preceding seam evidence. Cache keys
    include preceding video bytes, so rerendering its tail invalidates the next QC.
    """
    try:
        brief = json.loads((root / "brief.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        brief = {}
    groups: dict[Path, list[dict]] = {}
    for shot in shots:
        p = clips.get(shot["name"])
        if p and Path(p).exists():
            groups.setdefault(Path(p), []).append(shot)
    target = set(only) if only else None
    work = root / ".tmp" / "packqc" / ("ep%d" % ep)
    work.mkdir(parents=True, exist_ok=True)
    reports = {}
    previous = None
    for clip, members in groups.items():
        if target is not None and not any(s["name"] in target for s in members):
            previous = clip
            continue
        started = time.monotonic()
        try:
            signature = hashlib.sha256(json.dumps({"version": VERSION,
                "clip": _hash(clip), "previous": _hash(previous) if previous else "",
                "shots": members, "brief": brief}, ensure_ascii=False,
                sort_keys=True).encode()).hexdigest()
            cache = work / (signature + ".json")
            if cache.exists():
                report = json.loads(cache.read_text(encoding="utf-8"))
                report = dict(report, cached=True)
            else:
                report = review(clip, previous, members, brief, work / signature)
                if report["status"] != "unknown":
                    cache.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            report = {"status": "unknown", "issues": [], "reason": str(exc)[:240]}
        report.update(shots=[s["name"] for s in members], seconds=round(time.monotonic()-started, 2))
        reports[clip.stem] = report
        log("[packqc] %s %s%s" % (clip.stem, report["status"], "（复用同输入检查）" if report.get("cached") else ""))
        previous = clip
    output = {"scope": "sampled visual evidence; audio and unsampled events not verified",
              "groups": reports}
    path = root / "media" / ("ep%d" % ep) / "packqc.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
