# -*- coding: utf-8 -*-
"""确定性脚手架：给 agent 的分镜骨架（新架构版）。

与旧脚手架的三处升级：
  1. 小节标题带【幕｜场】→ 镜间关系可判定（旧版只有 S1/S2，无从判断连续还是跳切）
  2. 画面描述默认带反烧字约束（这是模型的顽固倾向，必须默认带上）
  3. 每行以「【镜N 待填】」开头 → 解析器按 visual[:80] 去重时不会互相吞掉
  4. （2026-09-08）新增可选列：场景 / 视觉风格 / 落幅 / 文字镜 / 承接
     ——提示词组装器（media/prompt.py）靠它们产出六段式提示词。
     全局约束（反烧字/BGM/禁止静音段）不进正文，由组装器统一加尾缀。
"""
from __future__ import annotations

NL = chr(10)

# 保留常量名（旧代码/测试可能引用）；正文不再默认塞这句——
# 否定句违反 skill「正面描述」规范，改由 prompt.py 的静帧尾缀
# （STILL_TAIL_PRESETS，按 pack 选档）/ GLOBAL_VIDEO 统一收尾。
ANTI_TEXT = "画面中不得出现任何可读文字、字符或字幕；木牌、招牌、纸张、屏幕一律只保留模糊纹理与色块。"

HEADER = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | 画面描述 "
          "| 落幅 | 对白 | 音效 | 文字镜 | 承接 |")
SEP = ("|--------|------|------|------|----------|------|----------|----------"
       "|------|------|------|--------|------|")


def build(topic: str, acts: list[dict], style: str = "") -> str:
    """acts: [{"name": "第一幕·纸扎铺-日", "shots": [{"seconds": 6}, ...]}, ...]"""
    parts = ["# 分镜：" + topic, ""]
    n = 0
    for ai, act in enumerate(acts, 1):
        for si, sh in enumerate(act.get("shots") or [], 1):
            n += 1
            secs = int(sh.get("seconds") or 8)
            parts += [
                "## 第%d幕｜%s｜S%d / %ds" % (ai, act.get("name", ""), n, secs),
                "",
                HEADER,
                SEP,
                "| %d | 待填 | 待填 | 待填 | %d | 待填 | %s "
                "| 【镜%d 待填】主体+动作+场景描述待填 | 待填 "
                "| 【对白或（无声）】 | 【音效】 | 否 |  |"
                % (n, secs, (style or "待填"), n),
                "",
            ]
    return NL.join(parts)
