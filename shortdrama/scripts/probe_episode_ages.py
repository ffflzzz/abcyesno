# -*- coding: utf-8 -*-
"""零配额探针：本集分镜里每个人**点名时带的年龄段**分布。

用途：判「按集默认年龄段」这条兜底能不能成立 —— 只有当某一集里一个人
只出现**一个**年龄段时，默认才是盘上事实；出现两个（闪回/混写）就不猜。
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5.media import storyboard, variants  # noqa: E402

root = Path("projects/xiaoman-workshop-1030")
for ep in (1, 2):
    p = root / "scenedesigner" / ("scenedesigner_ep%d.md" % ep)
    if not p.exists():
        continue
    shots = storyboard.parse(p.read_text(encoding="utf-8"))
    print("=== ep%d：%d 镜 ===" % (ep, len(shots)))
    ages = Counter()
    for st in shots:
        text = " ".join(str(v) for v in st.values())
        for name, paren in variants.mentions(text):
            tag = variants.age_of(paren)
            if tag:
                ages[(name[:8], tag)] += 1
    for (n, t), c in sorted(ages.items()):
        print("   %-12s %s  %d 次" % (n, t, c))
