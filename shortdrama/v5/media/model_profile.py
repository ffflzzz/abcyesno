# -*- coding: utf-8 -*-
"""per-model 怪癖档案（model profile）。

**为什么要这一层**（2026-09-10 用户提出的技术债）：
    `prompt.py` 从 17KB 涨到 48KB，里面 23 张规则表、37 处「实测」注释，
    几乎全是在补偿**某一个具体模型**的怪癖：烧字、上下分屏、负面提法诱发、
    中文字形污染、把 `same asset as S5` 当字面文字画出来……
    方向（把质量知识变成代码）是对的，但机制在退化成"模型专属补丁堆"：
      · 换模型时这些规则会变成**死代码**；
      · 更糟的是会变成**有害代码**——比如"按语言主体分流清中文"，
        对一个能正确读中文的模型就是纯粹的破坏。
    本模块把这些规则从核心组装器里挪出来，按模型归档，并用开关控制启用。

**兜底方向（2026-10-04 掉头，勿回退）**：
    未知/新版本模型**一律落 `CAPABLE`（零补偿）+ 响亮告警**，不再"保守按有怪癖处理"。
    理由是坏结果的**可见性不对称**：
      · 打了不该打的补丁 ⇒ 提示词被改坏，片子达不到新模型的上限，而**没有任何
        一道检查会发现**（所有检查都在核对"产物合不合我们的规矩"，而规矩本身
        就是被补丁改过的）⇒ 只会得出"新模型也就这样"，天花板被静默压住；
      · 该打没打的补丁 ⇒ 烧字/分屏回来了，这是**看得见**的硬伤，静帧质检本来就抓。
    ⇒ **选响亮的那个。** 告警必须在**开跑前**响（`WARN_PREFIX`），不是跑完在日志里。

**用法**：组装器只认 `ModelProfile` 的开关，不认具体模型名。
    prof = resolve(config.MODELS["video"], kind="video")
    if prof.burns_text: ...
新增模型 = 在 `PROFILES` 里加一条，**不需要改组装器**。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelProfile:
    """一个视觉模型的"脾气"档案。

    开关全部默认 False / 保守值 = 模型很乖，不需要任何补偿。
    有怪癖的模型逐条打开，并把对应词表挂上。
    """

    key: str
    label: str = ""

    # ─── 烧字类 ───────────────────────────────────────────────────────────
    # 提到"文字/字形/招牌"概念就把文字烧到画面上（越压越烧）。
    burns_text: bool = False
    # 招牌类名词 → 纯材质描述（正向改写，不是负面禁止）。
    sign_replacements: tuple[tuple[str, str], ...] = ()
    # 文字概念词：出现即删掉所在分句。
    text_words: tuple[str, ...] = ()

    # ─── 分屏类 ───────────────────────────────────────────────────────────
    # 时间性/过程性描述被理解成"前后对比" → 画成上下两格。
    splits_frames: bool = False
    time_replacements: tuple[tuple[str, str], ...] = ()       # 中文表
    time_replacements_en: tuple[tuple[str, str], ...] = ()    # 英文表

    # ─── 语言能力 ─────────────────────────────────────────────────────────
    # False → 中文字形会被烧成屏幕文字；混排提示词里要按语言主体清除。
    reads_chinese: bool = True
    # 判定"英文主体"的阈值（中文字占比低于此值 → 中文视为污染）。
    cjk_en_body_max: float = 0.15
    # 结构性中文词 → 英文（景别/机位/运镜/承接/落幅），长词优先。
    cjk_glossary: tuple[tuple[str, str], ...] = ()

    # ─── 提示词注入类 ─────────────────────────────────────────────────────
    # 负面禁令（DO NOT / 不要 / 禁止）会把概念写进提示词 → 诱发该概念被渲染。
    negative_induces: bool = False
    # 内部交叉引用（`same asset as S5` / `same mask model`）被当成字面内容渲染。
    leaks_asset_bookkeeping: bool = False


# ─── agnes-image-2.5-flash ───────────────────────────────────────────────────
# 实测怪癖（26/32 镜烧字、18 镜 14 镜分屏、负面提法诱发、中文名烧到衬衫上）。
_SIGN_WORDS_25 = ("摊位", "招牌", "店招", "横幅", "公告牌", "夜市", "铺面", "广告牌",
                  "海报", "贴纸", "木牌", "旗帜")

_TEXT_WORDS_25 = ("文字", "字符", "字形", "笔画", "乱码", "字母", "字幕",
                  "英文", "水印", "数字", "字迹", "可读", "可辨认")

_TIME_REPL_25 = (
    ("随后", ""), ("逐渐", ""), ("渐渐", ""), ("瞬间", ""), ("接着", ""),
    ("然后", ""), ("之后", ""), ("正在", ""), ("刚刚", ""), ("持续", ""),
    ("慢慢", ""), ("缓缓", ""), ("终于", ""), ("依次", ""), ("不断", ""),
    ("开始", ""), ("闪烁", ""), ("闪动", ""), ("出现半秒", ""), ("半秒", ""),
    ("归位", "静止"), ("重置", "静止"), ("双手交叉的穿模", "双手交叠处可见穿模"),
    ("世界突然卡顿", "世界定格"), ("恢复后", ""), ("已瞬移", "站在"),
    ("突然", ""), ("弹出", "排开"), ("转向", "朝向"),
    ("定格后恢复", "定格"), ("走到", "站在"), ("从画面左侧排开", "在画面左侧"),
    ("从漂浮状态坠落砸向地面", "悬停在半空"),
    ("从扭曲摊平", "平铺"), ("摊平", "平铺"), ("坠落", "悬空"),
    ("展开", "铺开"), ("变化", "不同"), ("换过一轮", "已定"),
)

_TIME_REPL_EN_25 = (
    ("slowly rotating", "rotated"),
    ("slowly turning", "turned"),
    ("slowly raising", "raised"),
    ("slowly lifting", "lifted"),
    ("slowly moving", "moved"),
    ("slowly walking", "walking"),
    ("slowly", ""), ("gradually", ""), ("progressively", ""),
    ("increasingly", ""), ("steadily", ""),
    ("continues to", ""), ("continuing to", ""), ("begins to", ""),
    ("starting to", ""), ("starts to", ""),
    ("is rotating", "rotated"), ("is turning", "turned"),
    ("is raising", "raised"), ("is lifting", "lifted"),
    ("is moving", "moved"), ("is walking", "walking"),
    ("is sitting", "sitting"), ("is standing", "standing"),
    ("momentarily", ""), ("briefly", ""), ("suddenly", ""),
    ("then", ""), ("afterwards", ""), ("next", ""),
    ("over time", ""), ("at the same time", ""),
)

_SIGN_REPL_25 = (
    ("发光公告牌", "发光色块板"), ("公告牌", "纯色板"), ("广告牌", "纯色板"),
    ("店招", "纯色装饰板"), ("招牌", "纯色装饰板"), ("木牌", "纯色板"),
    ("横幅", "纯色布条"), ("旗帜", "纯色布条"), ("海报", "纯色贴纸"),
    ("贴纸", "纯色贴纸"), ("补丁说明板", "发光色块矩形板"),
    ("说明板", "发光色块矩形板"), ("牌子", "纯色板"), ("挂着的纸", "纯色布片"),
    ("摊位群", "几何块台面群"), ("摊位", "几何块台面"), ("铺面", "几何块门脸"),
    ("低模夜市", "低模几何块街区"), ("夜市", "几何块街区"),
    ("地摊", "几何块台面"),
)

_CJK_GLOSSARY_25 = (
    ("后视镜倒影视角", "rearview-mirror reflection POV"),
    ("行车记录仪", "dashcam view"),
    ("车门玻璃反光", "door-glass reflection"),
    ("后视镜倒影", "rearview-mirror reflection"),
    ("平拍机位", "eye-level camera"),
    ("固定镜头", "fixed camera"),
    ("缓慢推进", "slow dolly in"),
    ("承接上一镜的落点", "continuing from where the previous shot ended"),
    ("承接上一镜的构图锚", "holding the previous shot's framing anchor"),
    ("镜头最终停在", "the shot settles on"),
    ("镜头", "camera"),
    ("画面", "frame"),
    ("继续", "hold"),
    ("车内外", "interior and exterior"),
    ("方向盘", "steering wheel"),
    ("车厢内", "bus interior"),
    ("全景", "wide shot"),
    ("中景", "medium shot"),
    ("近景", "close-up"),
    ("特写", "extreme close-up"),
    ("平视机位", "eye-level camera"),
    ("平视", "eye-level"),
    ("俯视", "high angle"),
    ("仰视", "low angle"),
    ("脸部", "face"),
)

AGNES_IMAGE_25 = ModelProfile(
    key="agnes-image-2.5-flash",
    label="Agnes 图 2.5：烧字 / 分屏 / 负面诱发 / 中文污染",
    burns_text=True,
    sign_replacements=_SIGN_REPL_25,
    text_words=_TEXT_WORDS_25,
    splits_frames=True,
    time_replacements=_TIME_REPL_25,
    time_replacements_en=_TIME_REPL_EN_25,
    reads_chinese=False,
    cjk_en_body_max=0.15,
    cjk_glossary=_CJK_GLOSSARY_25,
    negative_induces=True,
    leaks_asset_bookkeeping=True,
)

# ─── agnes-video-2.5-flash ───────────────────────────────────────────────────
# 与图 2.5 同源怪癖，但**视频需要时间/运镜描述**，故 splits_frames=False
# （绝不能对它跑 sanitize_text 的时间改写，会破坏运镜）。
# 招牌改写用独立的 _VIDEO_SIGN_REPL：保留物件剧情语义（木板仍是木板），
# 只去掉会联想"招牌文字"的"牌/匾"字。
_VIDEO_SIGN_REPL_25 = (
    ("木牌", "一块旧木板"), ("牌匾", "一块装饰木板"), ("匾额", "一块装饰木板"),
    ("招牌", "店面装饰框架"), ("广告牌", "宣传装饰框架"),
    ("公告牌", "公示装饰框架"), ("牌子", "标记板"),
    ("海报", "张贴画布"), ("横幅", "垂挂布条"), ("锦旗", "表彰布幔"),
)

AGNES_VIDEO_25 = ModelProfile(
    key="agnes-video-2.5-flash",
    label="Agnes 视频 2.5：烧字 / 中文污染 / 资产记账泄漏 / 需反分屏声明",
    burns_text=True,
    sign_replacements=_VIDEO_SIGN_REPL_25,
    text_words=_TEXT_WORDS_25,
    # 视频**确实会分屏**（实测：静帧修好后视频又分屏），所以需要 NOSPLIT_VIDEO
    # 正向声明。但**不能对它跑时间改写**——视频需要"缓慢推进/逐渐"这类时间信息，
    # 改写成状态等于丢运镜指令。故 time_replacements 保持为空：
    # `splits_frames`（要不要加反分屏声明）与"有没有时间改写表"是**两件事**。
    splits_frames=True,
    time_replacements=(),
    time_replacements_en=(),
    reads_chinese=False,
    cjk_en_body_max=0.15,
    cjk_glossary=_CJK_GLOSSARY_25,
    negative_induces=True,
    leaks_asset_bookkeeping=True,
)

# ─── 通用兜底 ────────────────────────────────────────────────────────────────
# 一个"乖模型"：能正确读中文、不烧字、不分屏、不受负面提法影响。
# 换到这类模型（或不确定时）用它 —— **零补偿**，提示词原样送出。
CAPABLE = ModelProfile(
    key="__capable__",
    label="无怪癖：原样使用提示词（零补偿）",
    reads_chinese=True,
)

_PROFILES: dict[str, ModelProfile] = {
    AGNES_IMAGE_25.key: AGNES_IMAGE_25,
    AGNES_VIDEO_25.key: AGNES_VIDEO_25,
    CAPABLE.key: CAPABLE,
}

# 前缀兜底 = **同族**才继承，不是"不管什么版本号都当2.5"。
# 三元组(前缀, 该族基准版本, 档案)：
#   · 前缀对上 **且** 版本号与基准一致（或没有版本号）→ 继承该族档案（零风险）
#   · 前缀对上但**版本号变了**（最可能的升级路径：2.5 → 3.0）→ 落CAPABLE + 响亮告警
#     理由：补丁是为 2.5 的怪癖写的，套到 3.0 上既可能白费，也**可能有害**
#     （`strip_cjk` 会删中文、`_apply_cjk_glossary` 会改词）。
#     宁可让坏处立刻暴露，也不要静默压住天花板。
_PREFIX_FALLBACK = (
    ("agnes-image", "2.5", AGNES_IMAGE_25),
    ("agnes-video", "2.5", AGNES_VIDEO_25),
)

#: 已经被告警过的模型名（同一进程只吵一次，避免每镜刷屏）。
_WARNED: set[str] = set()

#: 告警文案（供测试与前端"运行前检查"读取，不只是打给 stdout 看）。
WARN_PREFIX = "MODEL-PROFILE-UNVERIFIED"

#: 一次告警输出几行（测试按这个把行数换算成事件数）。
WARN_LINES = 3


def _version_of(name: str) -> str:
    """从模型名里取版本段：`agnes-image-3.0-flash` → `3.0`。取不到返回 ""。"""
    m = re.search(r"(?<!\d)(\d+\.\d+)(?!\d)", name)
    return m.group(1) if m else ""


def _warn_unverified(name: str, log=print) -> None:
    """**在开始生成之前**响亮告警：新模型/新版本没有配套档案。

    为什么必须"响亮"而不是静默降级（2026-10-04 方向掉头）：
        打错补丁 ⇒ 提示词被改坏，片子达不到新模型的上限，而**没有任何一道
        检查会发现**——所有检查都在核对"产物合不合我们的规矩"，而规矩本身
        就是被补丁改过的。结果只会得出"新模型也就这样"。
        该打没打 ⇒ 烧字/分屏回来了，这是**看得见**的硬伤，静帧质检本来就抓。
        坏结果的可见性不对称 ⇒ **选响亮的那个**。
    另见MEMORY：`SHORTDRAMA_STILL_QC` 前端默认关，告警不是"跑完能查"的东西，
    它必须在开跑前就把人喊住。
    """
    if not name or name in _WARNED:
        return
    _WARNED.add(name)
    log(f"[{WARN_PREFIX}] {name!r} 没有配套模型档案 —— "
        f"本次按**零补偿**发送提示词（原样，不做任何怪癖补偿）。")
    log(f"[{WARN_PREFIX}] 若该模型确实仍有烧字/分屏/中文污染等毛病，"
        f"请在 model_profile.PROFILES 补一条档案（见register()），否则成片会带回硬伤。")
    log(f"[{WARN_PREFIX}] 若它是已知档的新版本（例：{name} ≈ 2.5 之后的升级），"
        f"请同时确认旧补偿是否已失效 —— 套用旧补丁会压低新模型的上限。")


def register(profile: ModelProfile) -> ModelProfile:
    """登记一个档案（换模型时调用，或写进 _PROFILES）。"""
    _PROFILES[profile.key] = profile
    return profile


def resolve(model_name: str = "", kind: str = "image",
            log=print) -> ModelProfile:
    """按模型名取档案，**版本感知**：

    | 情况 | 落点 |
    |---|---|
    | 精确命中 `_PROFILES` | 该档案（零告警） |
    | 前缀同族**且版本一致/无版本** | 该族档案（零告警） |
    | 前缀对上但**版本变了**（如 3.0） | `CAPABLE` + `WARN_PREFIX` 告警 |
    | 完全不认识 / 模型名为空 | `CAPABLE` + `WARN_PREFIX` 告警 |

    2026-10-04 起，**未知一律落 CAPABLE 而不是 agnes 2.5 档**（方向掉头，理由见
    `_warn_unverified`）。此前"保守按有怪癖处理"的论证是错的：本模块的补偿
    **并非全是无害的正向改写**（`strip_cjk` 删中文、`_apply_cjk_glossary` 改词），
    对能读中文的模型是主动破坏。
    """
    name = (model_name or "").strip().lower()
    if name in _PROFILES:
        return _PROFILES[name]
    for prefix, base_ver, prof in _PREFIX_FALLBACK:
        if not name.startswith(prefix):
            continue
        ver = _version_of(name)
        if not ver or ver == base_ver:
            return prof          # 同族（含无版本号的裸名）→ 安全继承
        break                     # 版本变了 → 落下面的 CAPABLE + 告警
    _warn_unverified(name or f"(空模型名, kind={kind})", log=log)
    return CAPABLE


def describe() -> str:
    """一行清单（排查/日志用）：哪些模型挂了哪些怪癖。"""
    out = []
    for p in _PROFILES.values():
        flags = [n for n in ("burns_text", "splits_frames", "negative_induces",
                             "leaks_asset_bookkeeping")
                 if getattr(p, n)]
        if not p.reads_chinese:
            flags.append("cjk_pollution")
        out.append("%s[%s]" % (p.key, ",".join(flags) or "clean"))
    return " ".join(sorted(out))


def text_concept_re() -> re.Pattern:
    """（helper）文字概念词的正则——留给需要按正则扫描的调用方。"""
    return re.compile("|".join(re.escape(w) for w in _TEXT_WORDS_25))
