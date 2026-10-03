# -*- coding: utf-8 -*-
"""补偿触发记账（compensation ledger）—— 2026-10-04。

**要解决的问题**（直接关系「这套系统能否随模型发展水涨船高」）：

    判据/补偿分两类（见 `qc.JUDGEMENT_KINDS`）：
      · **能力类**（烧字、分屏、中文污染）= 兜"模型现在做不到"，
        模型升版后可能自己就好了 ⇒ **该复查并可能删除**，否则变成误伤源
        （模型做对了却判 P0 → 触发重画 → 白烧配额）。
      · **契约类**（人数、形制、凭空添加）= 查"画面照没照做分镜"，
        模型再强也做不到 ⇒ **永不删**。
    而「随模型发展水涨船高」的正解不是"少加判据"，是**分清哪些该扔**。
    前提是能回答：**每条补偿/判据，在真实跑批里到底还有没有被用到？**
    答不出来 ⇒ 清理只能靠记忆 ⇒ 补丁只增不减 ⇒ 天花板被静默压住。

**本模块只做记账，不改任何行为**：
    · 不改提示词、不改画面、不改流程、不改门禁。
    · 删掉本模块（+ `prompt.py` 里 5 行埋点）后，系统行为与现在**逐字节一致**。
    · 这也是它**不是**"自动删判据器"的原因：没有实测数据就删等于闭眼砍。

**记的是"触发"而不是"启用"**：
    启用 = 档案里那个开关是 True（恒定的，不随镜头变）。
    触发 = 这一镜真的改了文字。例：`burns_text=True` 但这镜描述里没有"招牌"，
          则该镜**没有触发**。记录必须区分，否则数出来的"启用 N 次"是废数据。

**重复计数问题**（`stills.py` 的 `force` 重渲会重跑提示词）：
    记"**这一集有没有用过**"（去重的镜号集合），不记次数。
    次数会被重渲污染；"本集是否用到"是事实、天然免疫重复计数。
    次数另存在 `counts` 里，仅作参考，**不参与任何判断**。
"""
from __future__ import annotations

import json
from pathlib import Path

#: 落盘文件名（与 `still_requeue_tally.json` / `gates.json` 同级）。
LEDGER_FILE = "compensation_ledger.json"

#: 能力类补偿的键→ 与 `qc.JUDGEMENT_KINDS` 同名，便于**按类分组统计**。
CAPABILITY = "CAPABILITY"
CONTRACT = "CONTRACT"

#: 补偿键 → 类别。**判据**的分类在 `qc.JUDGEMENT_KINDS`；这里管的是
#: **提示词补偿**（model_profile 那一族）的分类，分界线与判据完全一致：
#:   · 兜模型能力缺陷 ⇒ CAPABILITY（模型修好后可删）
#:   · 保提示词契约/一致性 ⇒ CONTRACT（永远需要）
LEDGER_KINDS = {
    # ── 能力类：模型升版后复查，可能已失效 ──
    "burns_text": CAPABILITY,       # 招牌类名词 → 纯材质（含字形）
    "text_clauses": CAPABILITY,     # 文字概念分句整句删
    "splits_frames": CAPABILITY,    # 时间性描述 → 状态词
    "negative_induces": CAPABILITY,  # 负面禁令整段删
    "leaks_asset": CAPABILITY,      # same asset as S5 记账泄漏
    "cjk_pollution": CAPABILITY,    # strip_cjk / 术语表
    # ── 契约类：无论模型多强都该留 ──
    "aspect_declaration": CONTRACT,  # 清分镜自带的 Ratio:16:9（与真实画幅冲突）
    "punctuation_tidy": CONTRACT,    # 连续标点规整
}


class Ledger:
    """一集（或一次跑批）的补偿触发记录。

    用法（生产侧只需两行）：
        led = Ledger()
        ...  # 在 sanitize_text 等处led.hit("burns_text")
        led.save(project_root, ep)

    字段：
        shots   {补偿键: [镜号, ...]}   去重镜号集合的列表，**判断用这个**
        counts  {补偿键: 次数}           参考用，重渲会虚高，**不参与判断**
        shots_qc {判据类别: [镜号, ...]} 静帧 QC 实际报出来的硬伤类别
    """

    def __init__(self) -> None:
        self.shots: dict[str, list[str]] = {}
        self.counts: dict[str, int] = {}
        self.shots_qc: dict[str, list[str]] = {}
        self._seen: set[tuple[str, str]] = set()

    # ── 埋点（生产代码里只调这两个）──────────────────────────────
    def hit(self, kind: str, shot: str = "", *, n: int = 1) -> None:
        """记一次**触发**。

        `shot` 传镜号（LN01…）以便去重；**留空也能用**（退化成纯计数）。
        """
        self.counts[kind] = self.counts.get(kind, 0) + n
        key = (kind, shot)
        if shot and key not in self._seen:
            self._seen.add(key)
            self.shots.setdefault(kind, []).append(shot)

    def hit_qc(self, kind: str, shot: str = "") -> None:
        """记静帧 QC 报出来的硬伤类别（接 `qc._defect_kinds` 的分类名）。"""
        if not shot:
            return
        lst = self.shots_qc.setdefault(kind, [])
        if shot not in lst:
            lst.append(shot)

    # ── 落盘 / 读回──────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "_readme": ("compensation 记账。shots=去重镜号（判断用）；counts=次数"
                        "（重渲会虚高，仅参考）；shots_qc=QC 实报硬伤类别。"
                        "类别见 media.model_profile.LEDGER_KINDS 与 qc.JUDGEMENT_KINDS。"
                        "本文件只做记账，不影响任何产出。"),
            "shots": self.shots,
            "counts": self.counts,
            "shots_qc": self.shots_qc,
            "by_kind": {k: v for k, v in LEDGER_KINDS.items()},
        }

    def save(self, project_root: Path, ep: int = 1) -> Path:
        d = Path(project_root) / "media" / ("ep" + str(ep))
        d.mkdir(parents=True, exist_ok=True)
        p = d / LEDGER_FILE
        # 幂等合并：同集重跑（断点续跑 / 重渲）按镜号并集，不覆盖已有记录。
        old = load(project_root, ep)
        merged_shots = dict(old.get("shots") or {})
        for k, v in self.shots.items():
            merged_shots[k] = sorted(set(merged_shots.get(k, [])) | set(v))
        merged_qc = dict(old.get("shots_qc") or {})
        for k, v in self.shots_qc.items():
            merged_qc[k] = sorted(set(merged_qc.get(k, [])) | set(v))
        merged_counts = dict(old.get("counts") or {})
        for k, v in self.counts.items():
            merged_counts[k] = merged_counts.get(k, 0) + v
        payload = {
            "_readme": self.to_dict()["_readme"],
            "shots": merged_shots,
            "counts": merged_counts,
            "shots_qc": merged_qc,
            "by_kind": dict(LEDGER_KINDS),
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p


def load(project_root: Path, ep: int = 1) -> dict:
    p = Path(project_root) / "media" / ("ep" + str(ep)) / LEDGER_FILE
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def report(project_root: Path, ep: int = 1) -> str:
    """人读的一行清单——**换模型时照这个逐条判"这条还有用吗"**。

    输出刻意分两段，因为处置方式相反：
      · CAPABILITY 段 = 可能已失效 ⇒ 逐条问"新模型上这毛病还成立吗"
      · CONTRACT 段   = 永不删⇒ 只报数量，不建议动
    """
    d = load(project_root, ep)
    if not d:
        return "(no ledger: %s/ep%s)" % (LEDGER_FILE, ep)
    shots = d.get("shots") or {}
    lines = []
    for want, label in ((CAPABILITY, "CAPABILITY(换模型时复查，可能已失效)"),
                        (CONTRACT, "CONTRACT(永不删)")):
        ks = [k for k in LEDGER_KINDS if LEDGER_KINDS[k] == want]
        used = ["%s:%d镜" % (k, len(shots.get(k, []))) for k in ks
                if shots.get(k)]
        never = [k for k in ks if not shots.get(k)]
        lines.append("%s = %s" % (label, " ".join(used) or "(全未触发)"))
        if want == CAPABILITY and never:
            lines.append("  ★未触发的能力类(优先复查): %s" % ",".join(never))
    qc = d.get("shots_qc") or {}
    if qc:
        lines.append("QC实报 = %s"
                     % " ".join("%s:%d镜" % (k, len(v)) for k, v in sorted(qc.items())))
    return "\n".join(lines)
