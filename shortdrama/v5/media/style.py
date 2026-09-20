# -*- coding: utf-8 -*-
"""风格块（style block）：类型包级的全局审美约束，注入每一镜的提示词。

为什么需要它：
  新架构把风格从「画面描述」列剥离到「视觉风格」列，但**单镜的风格列只是
  光线/质感/焦段的局部描述**，撑不起一个类型包的整体审美（如牛来风格的
  「真诚的技术不足」——极低面数、失真比例、僵硬绑定、穿模、廉价渲染）。
  旧架构把这些塞在画面描述里所以生效；剥离后若没有替代通道，模型会退回
  默认审美（写实照片 / 精致低模插画）——实测两张静帧全部跑偏。

查找顺序（配置驱动，不硬编码包名）：
  1. 项目 brief.json 的 "pack" 字段 → skills/packs/<pack>/style-block.md
  2. 项目根 style.md（项目自带风格块，优先级最高，覆盖包级）
  3. 都没有 → 空串（提示词朴素，行为与之前一致）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def pack_of(root: Path) -> str:
    """项目用哪个类型包（brief.json 的 pack 字段；缺省空）。"""
    p = root / "brief.json"
    if not p.exists():
        return ""
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("pack") or "")
    except Exception:  # noqa: BLE001
        return ""


def pack_config(root: Path) -> dict:
    """类型包配置（pack.json），供消费方读 pack 级开关。"""
    pack = pack_of(root)
    if not pack:
        return {}
    p = config.SKILLS_DIR / "packs" / pack / "pack.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ─── 叙事技法（script-craft）：**按包/按项目 opt-in，默认不注入** ──────────────
#
# 为什么需要这道开关（2026-09-16 用户要求「**不要污染我的生产线**」）：
#   编剧方法论（开场法则 / 钩子 / 爽点）是**商业微短剧**的判据 ——
#   含「每集必须有钩子」「密度递增」「反派要惨烈翻车」这类**上头**的配方。
#   而生产线上还有**微电影 / 音乐剧 / 商业短片**这类追求表达与质感的内容：
#   把短剧判据灌给它们 = **污染**（用户原话：「我不想拍所有东西都像短剧那套，上头、恶俗」）。
#   ⇒ 所以**默认不注入**：只有**显式声明**了才给。
#
# 实测背景（2026-09-16）：`plotdesigner` 曾把「短剧节奏极快」「每集至少 1 个反转」
#   「结尾悬念必须无法抵抗」通过**回落**漏给了羊毛毡/牛来/国风三个包 ——
#   那是**一个文件服务两个目标**（既当 shortdrama 自己的契约、又当所有包的兜底）造成的。
#   现已把商业判据从 `shortdrama/plotdesigner/SKILL.md` **剥离**到 `craft/`，
#   由本开关按包 opt-in 取回。
#
# 读取顺序（项目优先，与 `style.load` 的「项目 style.md > 包 style-block.md」同型）：
#   1. 项目 `brief.json` 的 `script-craft`（列表）—— 项目级，**可覆盖**
#   2. 类型包 `pack.json` 的 `script-craft` —— 包级**默认**
#   3. 都没有 → **空列表 = 不注入**（干净）
#
# ⚠️ `shortdrama` **刻意不加这份配置**：它是**回落基准包**，
#   在它上面开 = 所有回落到它的包都吸到（污染面最大）。要用的项目请在 brief 里声明。
def script_craft_of(root: Path) -> list:
    """本项目要注入哪些叙事技法（**默认空 = 不注入**）。"""
    try:
        b = json.loads((root / "brief.json").read_text(encoding="utf-8"))
        v = b.get("script-craft")
        if v is not None:                     # 显式写了就以它为准（含空列表 = 主动关掉）
            return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        pass
    v = pack_config(root).get("script-craft")  # 包级默认
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def read_craft(name: str) -> str:
    """读一份叙事技法（`skills/packs/craft/<name>/SKILL.md`）。

    读不到返回空串；**调用方必须把「声明了但读不到」报出来**（不能静默当没有）。
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""                       # 防路径穿越：技法名不含路径分隔符
    return _read(config.SKILLS_DIR / "packs" / "craft" / name / "SKILL.md")


def still_refs_enabled(root: Path) -> bool:
    """本项目的静帧是否该绑参考图（pack 级开关，缺省 True）。

    为什么需要这个开关（实测根因）：
      图像模型会**连参考图的渲染质量一起照抄**。牛来化四视图虽然是低模，
      但精修过（干净白底、均匀光照、平滑抗锯齿）——一旦绑定，成片就被拉回
      「精致低模」，正是牛来风格判定为失败的形态。受控实验：
        有参考图：A/B/C/F/G/H/I/J/K  → 全部干净低模
        无参考图：D(部分)/E/L/M/N     → 达到牛来风格
      因此「真诚的技术不足」这类**反质量**风格必须放弃参考图，改由风格块
      逐镜锁定审美；身份一致性交给分镜文字描述（"块状黑色短发、矩形手指"）。
      pack.json 里设 "still-refs": false 即启用该策略。
    """
    cfg = pack_config(root)
    v = cfg.get("still-refs")
    if v is None:
        return True
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "n")


# 静帧尾缀档位（pack.json 的 "still-tail"）。取值：
#   "material" 写实/材质档（默认）  "flat" 平涂色块档（反质量风格）
#   "none"     不注入
# 文本定义在 `prompt.STILL_TAIL_PRESETS`（提示词文本属于 prompt 层，此处只给档位）。
_STILL_TAIL_KINDS = ("material", "flat", "none")


def still_tail_kind(root: Path) -> str:
    """本项目的静帧尾缀档位。

    为什么放在 pack 配置里而不是硬编码包名：
      「静帧该不该宣称表面是平涂色块」是**类型包审美**问题——反质量风格
      （低模/平涂）要，写实/3D 渲染不要。它与 `still-refs` 同属 pack 级开关，
      故走同一条配置通道，避免把包名写进媒体链的判断里。
    缺省 "material"：风险不对称，理由见 `prompt.DEFAULT_STILL_TAIL` 的注释。
    """
    v = pack_config(root).get("still-tail")
    if v is None:
        return "material"
    k = str(v).strip().lower()
    return k if k in _STILL_TAIL_KINDS else "material"


def style_is_criterion(root: Path) -> bool:
    """**风格本身是不是硬判据**（pack 级开关，缺省 False）。

    为什么需要（2026-09-15 实测事故，village-tractor / 牛来包）：
      `qc.py` 明文写着「**风格、氛围、构图偏好一律不判**（那是导演选择题）」——
      这在 `shortdrama`（写实短剧）下是对的，但**反质量包的核心审美恰恰就是
      "必须粗糙、精致即失败"**：牛来包的定义里"写实照片 / 精致低模"就是**失败**。
      于是最该被守的那一项**完全没有判据**：实测 LN24 画成**写实照片级** +
      三个完全陌生的演员，静帧 QC 全部放行、直接进成片。
    ⇒ 让**包自己声明**"风格是硬判据"，QC 据此加一条风格轴。
      **绝不可全局打开** —— 写实包需要"风格不被机械判死"。`pack.json` 里
      设 `"style-is-criterion": true` 即启用。
    """
    v = pack_config(root).get("style-is-criterion")
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "y")


def style_criterion_spec(root: Path) -> str:
    """风格作为硬判据时，给 QC 的**期望风格一句话**（取自 pack.json 的 `visual-style`）。

    为什么取 `visual-style` 而不是整块 `style-block.md`：QC 只需要"该长成什么样"的
    **可判定摘要**（一个多模态模型拿整段 353 字风格散文去比图，判据会飘）。
    包没写 `visual-style` 时返回空串 —— 那种情况**不启用风格轴**（没有判据就别判）。
    """
    if not style_is_criterion(root):
        return ""
    return str(pack_config(root).get("visual-style") or "").strip()


def _truthy(v) -> bool:
    """pack.json 里的布尔开关（容忍 true/1/yes/on/y 与字符串形式）。"""
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "y")


def style_watch_spec(root: Path) -> str:
    """**期望形态**（风格"只记不改"轴）。pack 声明 `style-watch` 时取 `visual-style`。

    为什么需要这条通道（2026-09-16 两次实测的血）：
      `qc.PROMPT` 明文写「风格、氛围、构图偏好**一律不判**」，而唯一能打开风格判据的
      `style-is-criterion` 开关，其注入文本**写死了前提**——「本片属于**反质量类型包**……
      要拍成粗糙、廉价、笨拙」，判据是「呈**写实照片质感 / 精致渲染** → P0」。
      ⇒ **追求真实质感的包（羊毛毡 / 3D 动画）根本不能用它**（判据方向相反会稳定误判）
      ⇒ 结果：**风格维度完全无人管**，两次实测都在这上面出血：
        · felt-frog **LN02 出成写实真人照片**，全套 QC 放行、直接进成片；
        · felt-bach 大特写的脸漂成「**精细手办**」（对照源 skill 明确禁止 doll-like），
          QC 全程一声没吭。
      ⇒ 补一条**只记不改**的通道：注入期望形态，但**明令只许报 P1**。
        静帧 QC 侧 P1 不触发重画、clipqc 侧 P1 只打印 → **既让人看见，又不误杀**。
        这比 `style-is-criterion` 的"全有/全无 + 反质量语义"合理得多。

    与 `style-is-criterion` 互斥：后者已开时返回空串（那条走 P0，不重复注入）。
    """
    if style_is_criterion(root):
        return ""
    cfg = pack_config(root)
    if not cfg or not _truthy(cfg.get("style-watch")):
        return ""
    return str(cfg.get("visual-style") or "").strip()


def hard_keys(root: Path) -> tuple[str, ...] | None:
    """本项目的 QC 硬伤**关键词表**（pack 级可覆盖；None = 用全局默认）。

    为什么只让包"收窄"、不删掉闸门本身：
      `qc.HARD_KEYS` 那道子串闸门是**必要的**——它拦的是"模型标了 P0 但其描述并不
      指向真问题"的误报，删了它误报会直接变成重画。真正的问题是**全局表里的词**
      与某些包的契约天然抵触：`缺` / `五官` / `面部特征` / `真人脸` 是**子串**，
      而羊毛毡 / 3D 动画这类包**明确要求"五官清晰可读"**。
      实测（2026-09-16 felt-bach）：青蛙的白色巩膜被判「长出真人五官」不合格，
      而本包契约就要求五官可见 → **包与判据自相矛盾**。

    `pack.json` 三个字段（都没写 → 返回 None，行为与历史完全一致）：
      · `hard-keys`      完全替换（罕用）
      · `hard-keys-add`  追加本包特有的硬伤词
      · `hard-keys-drop` 移除与本包审美抵触的词
    """
    cfg = pack_config(root)
    if not cfg:
        return None
    raw = cfg.get("hard-keys")
    if raw is not None:
        return tuple(str(x) for x in raw if str(x).strip())
    add = [str(x) for x in (cfg.get("hard-keys-add") or []) if str(x).strip()]
    drop = {str(x) for x in (cfg.get("hard-keys-drop") or [])}
    if not add and not drop:
        return None
    from . import qc as _qc          # 局部导入：避免 style ↔ qc 的导入顺序纠缠
    base = [k for k in _qc.HARD_KEYS if k not in drop]
    return tuple(base + [k for k in add if k not in base])


def load(root: Path) -> str:
    """返回本项目的风格块文本（可能为空）。"""
    # 项目自带优先（允许单项目微调风格）
    own = _read(root / "style.md")
    if own:
        return own
    pack = pack_of(root)
    if not pack:
        return ""
    return _read(config.SKILLS_DIR / "packs" / pack / "style-block.md")


def diagnose(root: Path) -> str:
    """风格块缺失的**原因**，用于日志。

    为什么要这个函数：缺失只有一种表现（提示词变朴素），但原因有三种，
    日志里混成一句「brief.json 缺 pack」会把排查带偏——实测某项目明明
    配了 pack，真正原因是该包**没有 style-block.md**，却一直按前者排查。
    """
    if _read(root / "style.md"):
        return ""
    pack = pack_of(root)
    if not pack:
        return "brief.json 未配置 pack"
    if not _read(config.SKILLS_DIR / "packs" / pack / "style-block.md"):
        return "类型包 %s 缺少 style-block.md" % pack
    return ""


def wrap(block: str) -> str:
    """规范成提示词里的一段（去空行、去标题行，保持单段）。

    注意：不能简单用空格 join——源文件是硬折行的中文长句，空格 join 会在
    中文词之间插入空格（"笨拙的大平面 和原始几何体"），既污染提示词又让
    模型把断行当成分句。这里用空格 join 后，再抹掉**与中日韩字符/全角标点
    相邻**的空格；纯拉丁词之间的空格保留（如 "去掉 PBR、法线" → "去掉PBR、法线"，
    "VHS/CRT/故障" 原样）。
    """
    if not block:
        return ""
    lines = [ln.strip() for ln in block.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    s = " ".join(lines)
    cjk = r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]"
    s = re.sub(r"\s+(?=%s)" % cjk, "", s)     # 空格在中文前 → 去掉
    s = re.sub(r"(?<=%s)\s+" % cjk, "", s)    # 空格在中文后 → 去掉
    s = re.sub(r"\s{2,}", " ", s)
    # Markdown 强调标记是给人读的，进提示词只是噪声
    s = s.replace("**", "")
    return s.strip()
