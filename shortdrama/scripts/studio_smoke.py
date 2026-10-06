# -*- coding: utf-8 -*-
"""工作室冒烟测试：直接打 8787 的真接口，**只看盘上事实**。

⚠️ 两条纪律：
1. **绝不打真项目**。改一镜会作废它的静帧与成片（`webwrite._invalidate_shot`），
   拿别人的片子做冒烟 = 替人返工。这里自建一个 `studio-smoke-fixture` 夹具，跑完删掉。
2. 写成文件而不是 curl：curl 的 `-d` 带中文在 GBK 控制台下会把 JSON 打坏
   （实测报 "There was an error parsing the body" → 400，与真校验的 400 分不开）。

跑法：先起后端（`python -m v5.server --port 8787`），再
    python scripts/studio_smoke.py
"""
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8787"
P = "/v1/pixa/short-drama"
PID = "studio-smoke-fixture"
EP = 1

SB_MD = """# 分镜：冒烟夹具

| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 | 场景 |
|--------|------|------|------|---------|---------|------|------|------|
| 1 | 全景 | 平视 | 固定 | 6 | 夹具第一镜的画面描述足够长以便被解析器认作镜头 | 甲：这一句够长了吧。 | 环境音 | 夹具场 |
| 2 | 近景 | 俯视 | 缓推 | 6 | 夹具第二镜的画面描述同样足够长以便被解析器认作镜头 | 乙：这一句也够长了吧。 | 电流声 | 夹具场 |
"""

# 1x1 JPEG（最小合法文件）；canvasout 只要求文件存在
JPG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300"
                    "ffffffffffffffffffffffffffffffffffffffffffffffffff"
                    "ffffffffffffffffffffffffffffffffffffffffffffffffff"
                    "ffc00011080001000103012200021101031101ffc4001f0000"
                    "01050101010101010000000000000000010203040506070809"
                    "ffc400b5100002010303020403050504040000017d01020300"
                    "ffda000c03010002110311015320003dffb80001ffd9")


def call(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:160]}


def fixture() -> Path:
    root = Path("projects") / PID
    shutil.rmtree(root, ignore_errors=True)
    (root / "scenedesigner").mkdir(parents=True)
    (root / "media" / "ep1" / "stills").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "brief.json").write_text(json.dumps(
        {"topic": "冒烟夹具", "pack": "shortdrama", "genre": "测试", "episodes": 1,
         "target_duration": "约 12 秒", "protagonist": "甲：测试用",
         "must_have": ["夹具第一镜"], "key_props": [], "禁忌": [],
         "tone": "无", "结局": "定格"}, ensure_ascii=False), encoding="utf-8")
    (root / "scenedesigner" / "scenedesigner_ep1.md").write_text(SB_MD, encoding="utf-8")
    (root / "assets.json").write_text(json.dumps({"assets": []}, ensure_ascii=False),
                                      encoding="utf-8")
    (root / "media" / "ep1" / "stills" / "LN01.jpg").write_bytes(JPG)
    (root / "media" / "ep1" / "stills" / "LN02.jpg").write_bytes(JPG)
    (root / "media" / "ep1" / "stills.json").write_text(json.dumps(
        {"LN01": {"prompt": "夹具第一镜的提示词", "seconds": 6,
                  "url": "http://example.invalid/a.jpg"},
         "LN02": {"prompt": "夹具第二镜的提示词", "seconds": 6,
                  "url": "http://example.invalid/b.jpg"}}, ensure_ascii=False),
        encoding="utf-8")
    return root


def main() -> int:
    fails = 0

    def ok(label, cond, extra=""):
        nonlocal fails
        print(("  PASS  " if cond else "  FAIL  ") + label + (("   " + extra) if extra else ""))
        fails += 0 if cond else 1

    try:
        call("GET", "/health")
    except Exception as e:  # noqa: BLE001
        print("后端没起：%s" % e)
        return 2

    fixture()
    try:
        inbox = P + "/projects/%s/director/inbox?ep=%d" % (PID, EP)

        st, b = call("GET", inbox)
        d = b.get("data") or {}
        ok("GET inbox 200 + 全套字段",
           st == 200 and all(k in d for k in
                             ("messages", "pending", "edits", "stats", "hitl", "targets")),
           "stats=%s" % d.get("stats"))

        st, b = call("POST", P + "/projects/%s/director/message" % PID,
                     {"text": "冒烟：LN01 的钱盒要漆成朱红色", "ep": EP, "by": "studio"})
        rec = b.get("data") or {}
        ok("POST message 200 且是排队中（不谎报已送达）",
           st == 200 and rec.get("delivered_to") == "" and rec.get("kind") == "message",
           "id=%s" % rec.get("id"))

        st, b = call("POST", P + "/projects/%s/director/message" % PID,
                     {"text": "定向给评审", "ep": EP, "to": "reviewer"})
        ok("定向 message 200", st == 200, "to=%s" % (b.get("data") or {}).get("to"))

        st, b = call("POST", P + "/projects/%s/director/message" % PID,
                     {"text": "非法目标", "ep": EP, "to": "scene_desinger"})
        ok("非法 to → 400（不静默降级成广播）", st == 400, str(b.get("message"))[:56])

        st, b = call("POST", P + "/projects/%s/director/message" % PID,
                     {"text": "   ", "ep": EP})
        ok("空文本 → 400", st == 400)

        st, b = call("GET", inbox)
        d = b.get("data") or {}
        ok("两条未投递都排着", d.get("stats", {}).get("pending", 0) >= 2,
           "pending=%s" % d.get("stats", {}).get("pending"))

        st, b = call("GET", P + "/projects/%s/canvas?ep=%d" % (PID, EP))
        doc = b.get("data") or {}
        shots = [n["id"][2:] for n in doc.get("nodes", []) if str(n.get("id", "")).startswith("s:")]
        ok("canvas 200 且出镜节点", st == 200 and len(shots) == 2, "%s" % shots)

        if shots:
            st, b = call("POST", P + "/segments/%s-ep%d-%s" % (PID, EP, shots[0]),
                         {"seconds": "8", "source": "canvas", "by": "studio"})
            dd = b.get("data") or {}
            ok("改一镜 200 且 source/by 没漏进 skipped",
               st == 200 and dd.get("skipped") == [] and dd.get("logged") == 1,
               "logged=%s skipped=%s" % (dd.get("logged"), dd.get("skipped")))

            st, b = call("GET", inbox)
            edits = (b.get("data") or {}).get("edits") or []
            ok("改动进台账且标明画布来源",
               any("画布" in str(x.get("text")) for x in edits), "edits=%d" % len(edits))

            st, b = call("POST", P + "/segments/%s-ep%d-%s" % (PID, EP, shots[0]),
                         {"seconds": "8", "source": "canvas"})
            dd = b.get("data") or {}
            ok("写回同一个值 → 不记假账", dd.get("logged") == 0, "logged=%s" % dd.get("logged"))

        st, b = call("POST", P + "/projects/%s/director/redo" % PID,
                     {"note": "重来", "ep": EP})
        ok("redo 无 target 且无挂起 → 400", st == 400, str(b.get("message"))[:56])

        # 台账的投递语义只能在派发侧验，这里确认接口读得到同一份事实
        st, b = call("GET", inbox)
        d = b.get("data") or {}
        ok("inbox 一次调用给齐右栏要的全套",
           isinstance(d.get("hitl"), dict) and "pending" in d["hitl"]
           and isinstance(d.get("targets"), list))
    finally:
        shutil.rmtree(Path("projects") / PID, ignore_errors=True)
        print("\n  (夹具 %s 已删)" % PID)

    print("%s  fails=%d" % ("全部通过" if fails == 0 else "有失败", fails))
    return fails


if __name__ == "__main__":
    sys.exit(main())
