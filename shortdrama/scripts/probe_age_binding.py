# -*- coding: utf-8 -*-
"""零配额探针：拆表 + 本集默认年龄段之后，第 2 集**实际会绑到哪张脸**（改前/改后对账）。

不生成任何东西，只跑绑定层。用法：
    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/probe_age_binding.py <项目名> <集号>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import guards  # noqa: E402
from v5.media import assets, cast, storyboard  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "xiaoman-workshop-1030"
ep = int(sys.argv[2]) if len(sys.argv) > 2 else 2
root = Path("projects") / name

chars = cast.parse_characters(
    (root / "worldbuilder" / "worldbuilder.md").read_text(encoding="utf-8"))
split = cast.split_same_name_cards(chars, log=lambda *_: None)
p = guards.resolve_path(root, "scenedesigner", ep)
shots = storyboard.parse(p.read_text(encoding="utf-8"))

# 拆表后注册表会多出这些条目（生图前先用同名占位，只为看绑定去向）
reg = assets.auto_sync(root)
names = {a.get("name") for a in reg.get("assets", [])}
extra = [{"id": c["name"], "name": c["name"], "type": "character",
          "alias_of": c.get("alias_of"), "age_tag": c.get("age_tag"),
          "ref_image": c["name"] + ".png",
          "identity": c.get("appearance", "")[:80]}
         for c in split if c["name"] not in names]
reg = {"assets": list(reg.get("assets", [])) + extra}

defaults = assets.episode_defaults(reg, shots)
print("第 %d 集：%d 镜 / 注册表 %d 条 / 本集默认年龄段 %s"
      % (ep, len(shots), len(reg["assets"]), defaults or "（无）"))

sw = base_n = 0
for s in shots:
    old = [h.get("name") for h in assets.hits_for_shot(reg, s)[0]]
    new = [h.get("name") for h in assets.hits_for_shot(reg, s, defaults=defaults)[0]]
    if old != new:
        sw += 1
        print("   %s  %s  →  %s" % (s["name"], old, new))
    base_n += len([x for x in new if x in defaults])
print("改绑 %d/%d 镜" % (sw, len(shots)))
