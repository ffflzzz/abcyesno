# -*- coding: utf-8 -*-
"""资产生产层：从角色卡 / 资产卡**确定性生成**权威参考图。

为什么必须有这一层（实测事故）：
    新架构把 `generate_turnaround` / `generate_image` 从角色工具里拿掉了，
    只在 TOOL_NOTE 里写「本系统未提供，直接跳过」。于是角色只能写**文本**
    `assets.md`，里面写着 `参考图：images/天花板监控屏幕.png`，而图从未生成。
    媒体层 `assets.bind()` 读 `assets.json` + `images/` 全空 → 注册表 0 个资产
    → 参考图绑定 0/8 镜、身份锚点也空 → 静帧既无参考图也无文字锚点
    → **每镜模型自由发挥，人物一致性归零**（nightshift-45：2s 帧与 30s 帧是两个人）。

设计取舍：**确定性预生成**，不交回给角色自决。
    旧架构把生图工具挂给 worldbuilder/assetdesigner，靠提示词催它「必须调用」。
    新架构的教训是「跑满角色不能靠提示词，要靠静态边」——同一逻辑用在资产上：
    资产图由媒体层在静帧之前统一产出，角色只负责**描述**（它擅长的），
    出图由代码保证（它擅长的）。角色轮数也不会被生图耗尽。

产物：
    images/<名称>.png      权威参考图（角色=2x2 四视图，道具/场景=单张）
    images/<名称>.png.url  公网 URL 或 data URI 伴侣（Agnes 拒绝裸本地路径）
    assets.json            注册表（keywords 供 assets.bind 逐镜匹配）

可续跑：已有图 + 注册表条目 → 跳过（不重复烧配额）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path

import httpx

from .. import config
from . import keypool
from . import providers

IMAGES_DIR = "images"
PANEL = (432, 576)          # 三视图单格 3:4
VIEWS = (("front", "正面"), ("three-quarter", "3/4侧面"),
         ("side", "正侧面"), ("back", "背面"))

# 参考图版本：1 = 2x2 拼图直接当参考图；2 = 只绑**单格正面像**。
#
# 为什么必须换（2026-09-09 实测 A/B，nightshift-45 LN01）：
#   绑 2x2 拼图 → 静帧画出 **3 个穿同样制服的人**（模型把拼图的四个格子
#   读成"画面里该有多个同款人"），且四格互相漂移（背面格被无视、右下格
#   变成插画风两个人）。
#   绑单格正面像 → 只有 **1 个人**，构图与服装正确。
# 注册表带 ref_ver：旧版本图会自动重生成，不用手工 --force。
#
# v3（2026-09-15）：**源照片直绑规则**。用户反馈「为什么样子都变了，不是和我给的
#   照片完全一样的」→ 根因是照片被**重画了两次**（照片 →(img2img) 四视图 →(取正面格)
#   注册表 →(img2img) 静帧）。v3 起：有源照片时**直绑照片本身**（只剩一跳）。
#   升版本号即可让所有旧 ref 按新规则重建（这是本常量的既有用途，见 docstring）。
REF_VERSION = 3
SHEET_DIR = "_sheets"       # 四视图拼图仅作人工查看，不进注册表（避免被 auto_sync 收编）

# 角色关键词**只保留角色名本身**（2026-09-13 修正）。
#
# 旧实现是 `kws = [name] + _ROLE_ALIASES`，而别名表
# ("主角", "男主", "女主", "他", "她", "本人") 被**无条件追加给每一个角色**。
# 后果：多角色项目的 keyword 表**完全重合**，零区分力 —— 实测 noodle-night 的
# 「老陈」与「女孩」表一模一样，任何含「她」的镜两个角色同时命中；而 `bind()`
# 只留一张人物参考图（`chars[:1]`）→ 直接绑错脸。
#
# 这些泛词的原始诉求由别处承担，所以移除是安全的：
#   · "本镜有没有人" → `assets.person_in_text`（自带 `_PERSON_HINTS`，含
#     主角/男主/女主/本人，外加 "他"/"她" 判据）驱动 `bind()` 的主角兜底；
#   · "本镜是哪个角色" → `assets._chars_by_name`（按角色名精确补漏）。
# 保留常量名，是为了让搜旧名的人立刻读到这段结论。**不要再往里加泛词。**
_ROLE_ALIASES: tuple[str, ...] = ()

# 资产卡里必须剔除的"参考图路径"行——它是**产物声明**，不是描述。
_REF_LINE = re.compile(r"^\s*[-*]?\s*参考图\s*[:：].*$", re.M)


def images_dir(root: Path) -> Path:
    d = root / IMAGES_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


#: 用户提供的**人物源照片**的约定文件名后缀（2026-09-15）：
#: `images/<角色名>.source.<ext>`。命名沿用仓库里的既有先例（paperface-2 的
#: `images/主角.source.jpg`）。
_SOURCE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _source_photo(root: Path, name: str) -> str:
    """本角色的源照片**路径**（`images/<名>.source.<ext>`）；没有则返回 `""`。

    只用来看"有没有 / 叫什么名字"（日志、测试）。**真正传给接口的是
    `_source_ref_uri` 编出来的 data URI** —— 见它的说明。
    """
    d = images_dir(root)
    for ext in _SOURCE_EXTS:
        p = d / (name + ".source" + ext)
        if p.exists():
            return str(p)
    return ""


def _source_ref_uri(root: Path, name: str) -> str:
    """把源照片编成 **data URI** —— 供应商只吃 URL / data URI，**不吃本地路径**。

    ★ 2026-09-15 实测事故（village-bees，第一版接线）：把**本地路径**直接塞进
      `gen_image(refs=[...])` → `extra_body["image"]` → 供应商 **400 Bad Request**，
      **12 次生成（3 角色 × 4 视图）全部失败** → `[cast] 完成：角色 0` →
      注册表里没有角色条目 → **连文字身份锚点也一起丢了**（双重失效）。
      而 `_save_ref` 的注释早就写着「public_url 为空时写 data URI（**Agnes 也接受**）」
      —— 本系统传图给接口的约定就是 URL / data URI。

    编码参数与 `_save_ref` 的 data URI 分支**刻意保持一致**（缩到 ≤576×768、JPEG q86、
    base64）：同一件事一套参数，避免两处漂移。
    """
    p = _source_photo(root, name)
    if not p:
        return ""
    return _file_ref_uri(p)


def _file_ref_uri(p) -> str:
    """把**任意一张本地图**编成 data URI（供应商只吃 URL / data URI，不吃路径）。

    `_source_ref_uri` 与「分龄变体以基础定妆照为源」共用这一份编码参数，
    避免两处漂移（同一件事一套参数）。
    """
    if not p:
        return ""
    try:
        from PIL import Image

        im = Image.open(p).convert("RGB")
        im.thumbnail((576, 768))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 -- 编码失败就退回"无源图"，不阻断
        return ""


def sheets_dir(root: Path) -> Path:
    """四视图拼图的归档目录：**仅供人工查看**，不进注册表。

    为什么不放在 images/ 根下：assets.auto_sync 会把 images/ 里所有图
    收编成资产，拼图被当成资产后又会被绑进镜头（正是 REF_VERSION=1 的坑）。
    """
    d = images_dir(root) / SHEET_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _fetch(url: str, timeout: int = 120) -> bytes:
    with httpx.Client(timeout=timeout, trust_env=False) as c:  # CDN 直连
        return c.get(url).content


def _save_ref(root: Path, name: str, data: bytes, public_url: str = "") -> Path:
    """落盘参考图 + .url 伴侣。public_url 为空时写 data URI（Agnes 也接受）。"""
    dest = images_dir(root) / (name + ".png")
    dest.write_bytes(data)
    if public_url:
        uri = public_url
    else:
        buf = io.BytesIO()
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((576, 768))
        im.save(buf, "JPEG", quality=86)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    dest.with_name(dest.name + ".url").write_text(uri, encoding="utf-8")
    return dest


# ─── 解析：角色卡 / 资产卡 ───────────────────────────────────────────────────

def _clean_name(s: str) -> str:
    """`林宇（固定人名，全片统一使用）` → `林宇`。

    同时剥掉 **markdown 强调星号**与 Windows 文件名非法字符——
    实测（2026-09-10《热牛奶》）：worldbuilder 把姓名写成 `**苏晚**`，原实现
    只剥括号，`**苏晚**.png` 撞上 Windows 非法字符 `*` → Errno 22 →
    参考图全灭 → 参考图绑定 0/10 → 人物一致性归零（每镜换装/换人）。
    """
    t = re.sub(r"[（(][^）)]*[）)]", "", s or "").strip()
    t = t.strip("：: 　")
    t = re.sub(r"[*#]", "", t)                       # markdown 强调 / 标题残留
    t = re.sub(r"[「」『』\"'“”‘’]", "", t)            # 引号**全剥**（名字不作引用用途）
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "", t)         # Windows 非法字符
    return t.strip()


# 外貌段标题的多种措辞（同一语义）。见 `parse_characters` 里的三段实测。
_APPEARANCE_KEYS = (
    "外貌特征", "外貌与材质", "外貌", "外形与材质", "外形特征", "外形",
    "外观特征", "外观", "人物形象", "形象描述",
)
# 兜底拼字段时**排除**的字段名（剧情/用途类，不是"长相与服装"）。
# 依据：`parse_characters` 的原始设计意图是"只取长相与服装，不把背景故事/角色弧光
# 带进生图提示词"（tests_cast 有一条专门锁它）。
_APPEARANCE_EXCLUDE = ("姓名", "用途", "背景", "故事", "弧光", "关系", "动机",
                       "性格", "简介", "说明", "参考", "台词", "对白", "备注")


def _appearance_from_bullets(blk: str) -> str:
    """兜底：把 `- 字段：内容` 里的**外观类字段**拼成一段外貌描述。

    ## 为什么必须换"结构假设"（2026-09-14 第三次实测，代价是两条链）

    worldbuilder 的写法至少三种，前两种都被"找某个外貌段标题"抓不到时有解，
    **第三种连"外貌段"这个概念都没有**：
      ① `## 外貌特征（用于生图）` + 下一行正文（规范格式）
      ② `- 外形与材质（出图提示词口径）：正文同行`（牛来包第一版）
      ③ **把外观拆成多个字段**（dawn-broadcast）：
         `- 物种：` `- 脸型与头部：` `- 体型：` `- 服装：` `- 标记：` `- 材质：` `- 低模风格：`
    → 前两轮我都在**加措辞**（每次只多撑一轮）；这一轮起改成**换结构假设**：
      **抓不到"外貌段"时，就把外观类字段拼起来**，并排除剧情/用途类字段。
    """
    keep: list[str] = []
    for ln in blk.splitlines():
        m = re.match(r"^\s*[-*]\s*\**([^：:*]{1,14})\**\s*[:：]\s*(.+)$", ln)
        if not m:
            continue
        field, val = m.group(1).strip(), m.group(2).strip()
        if any(x in field for x in _APPEARANCE_EXCLUDE):
            continue
        keep.append(val)
    return "；".join(keep)


#: 代码围栏行（``` / ```yaml）—— 模型会**把契约里的示例格式连同围栏一起复制**（实测）。
_FENCE_LINE_RE = re.compile(r"^\s*`{3,}\s*[A-Za-z0-9_-]*\s*$")
#: 角色卡标题：`# 角色卡：<名>` / `## 角色卡：` / **`#+ 角色卡：`**（实测第三种）
#: `[^\w\n]{0,4}` 吃掉 `#` 与「角色卡」之间可能出现的装饰字符（`+` / `*` / `-` / `>` / 空格）。
_CARD_SPLIT_RE = re.compile(r"^#+[^\w\n]{0,4}角色卡\s*[:：]", re.M)


def _appearance_from_paragraph(blk: str) -> str:
    """兜底之二：整块里**既没有字段、也没有标题**，只有一段普通正文 → 直接取这段正文。

    实测（dawn-broadcast 第三跑）：模型把契约里的**示例格式连同代码围栏一起复制**：
        ```
        #+ 角色卡：老赵
        ```
        人类，男性，55 岁左右。圆脸……
    → 外貌既不在"外貌段"下、也不是 `- 字段：内容`，而是**围栏之后的一段正文**。
    取法：丢掉围栏行、丢掉首行（那是名字行）、丢掉剧情类字段行，其余拼接。
    """
    keep: list[str] = []
    for i, ln in enumerate(blk.splitlines()):
        if i == 0 or _FENCE_LINE_RE.match(ln):
            continue
        m = re.match(r"^\s*[-*]\s*\**([^：:*]{1,14})\**\s*[:：]", ln)
        if m and any(x in m.group(1) for x in _APPEARANCE_EXCLUDE):
            continue
        keep.append(ln.strip())
    return " ".join(x for x in keep if x).strip()


def parse_characters(worldbuilder_md: str) -> list[dict]:
    """从 worldbuilder.md 抽角色卡 → [{name, appearance, keywords}]。

    只取「外貌特征」段——生图要的是长相与服装，不是背景故事与角色弧光。
    角色卡标题形如 `# 角色卡：值夜店员（主角）`，姓名在 `- 姓名：林宇`。
    """
    out: list[dict] = []
    # 按角色卡标题切块（`# 角色卡：…` / `## 角色卡：…`）
    blocks = _CARD_SPLIT_RE.split(worldbuilder_md or "")[1:]
    for blk in blocks:
        head = blk.split("\n", 1)[0].strip()
        name = ""
        m = re.search(r"^\s*[-*]?\s*姓名\s*[:：]\s*(.+)$", blk, re.M)
        if m:
            name = _clean_name(m.group(1))
        if not name:
            name = _clean_name(head)
        if not name:
            continue
        # 外貌段：从外貌/外形段标题到下一个标题或分隔线。
        #
        # ★ **必须容忍多种措辞**（2026-09-14 实测事故，代价是一整条链）：
        #   原实现写死「外貌特征」。而 `packs/niulai-movie-style/worldbuilder/SKILL.md`
        #   产出的是「**外形与材质（出图提示词口径）：**…」（且**正文在同一行**，不换行）。
        #   → `m2` 不匹配 → `ap=""` → 下面 `len(ap) < 20 → continue` → **两个角色全被丢弃**。
        #   连锁后果（全部实测到）：
        #     · `pipeline` 的 `_names` 为空 → `prompt._has_person()` 判「本镜无人」→
        #       **全片 26 镜都注入「空镜：画面内容为场景与道具本身，环境静物」** →
        #       模型按静物处理 → 实测 **5/26 镜退回写实照片级**（牛来包唯一失败条件）；
        #     · `identity_lines` 无锚点 → 身份一致性归零。
        #   **一个解析口径差异废了一整条链 —— 所以这里按"同一语义的多种措辞"收，且
        #   允许正文同行（`[^\n]*?[:：]\s*` 后直接接内容）。**
        ap = ""
        # ★★ **KEY 必须站在一行的开头**（2026-10-06 实测事故，代价是两集画风全错）：
        #   旧写法 `(?:"外貌特征|外貌|外形|…")[^\n]*?…` 没有行首锚，而世界设定卡里
        #   「- 年龄／身份：约二十岁**外貌**，秋宫山谷守桥的寒水剑修」这一行**正文里**
        #   就含"外貌"两个字 ⇒ 匹配从这一行开始、吃到下一个标题为止 ⇒
        #   抓到的"外貌段"其实只有下一行「- 核心性格（3个词）：冷静、克制、决断」，
        #   而真正的「## 外貌特征（用于生图）」整段 200 多字（淡青渐变薄纱长裙、
        #   白莲发饰、3D 游戏 CG 渲染造型、非真人实拍…）**一个字都没进生图提示词**。
        #   后果：定妆照画成**现代职业装真人照片**，`identity` 里也只剩性格词，
        #   `sheetcheck` 因此报「卡片里没有可对账的外形项 ⇒ 不判」直接放行 ——
        #   两集 20 镜全部锚在这张错图上。用户看到的"和第 1 集完全不是一回事、
        #   连画风都变了、这是真人了"，根子在这一行正则。
        _K = "(?:" + "|".join(_APPEARANCE_KEYS) + ")"
        m2 = re.search(
            r"(?:^|\n)\s*#{1,6}\s*\**" + _K +      # ① 标题式：`## 外貌特征（用于生图）`
            r"[^\n]*\n+"                            # ② 标题行剩余部分 + 换行
            r"(.*?)(?=\n\s*#{1,6}\s|\n---|\Z)",     # ③ 吃到下一个标题/分隔线/文末
            blk, re.S)
        if not m2:
            m2 = re.search(
                r"(?:^|\n)[ \t]*(?:[-*+][ \t]+)?\**" + _K +   # ④ 同行式 / bullet 式
                r"[^\n]*?[:：]\**[ \t]*"                       #    `**外形与材质：**正文…`
                r"(.*?)(?=\n\s*(?:#{1,6}\s|[-*+][ \t]*\S{1,8}[：:])|\n---|\Z)",
                blk, re.S)
        if m2:
            ap = m2.group(1).strip()
        else:
            # ★ 兜底一：拆成多个 `- 字段：内容`（见 `_appearance_from_bullets`）
            ap = _appearance_from_bullets(blk)
        if not ap:
            # ★ 兜底二：只有一段普通正文（模型把契约示例连同**代码围栏**一起复制了）
            ap = _appearance_from_paragraph(blk)
        # 围栏行不得进提示词（模型会把契约示例的 ``` 一起抄下来）
        ap = "\n".join(x for x in ap.splitlines() if not _FENCE_LINE_RE.match(x))
        ap = re.sub(r"`{3,}[A-Za-z0-9_-]*", "", ap)
        ap = re.sub(r"\*\*", "", ap)
        ap = re.sub(r"\s*\n\s*", " ", ap).strip()
        if len(ap) < 20:
            continue
        kws = [name] + list(_ROLE_ALIASES)
        # 「同一张脸的另一个角色」识别：分身 / 替身 / 镜像 / 克隆。
        # 实测事故（nightshift-45 监控分身）：角色卡写"与主角完全一致的外貌"，
        # 但独立文生图出来是**四个不同的人 + 完全不同的制服**——文字描述锁不住脸。
        # 这类角色必须用主角图做 img2img（source），否则参考图本身就是穿帮源。
        same_as = _same_face_as(blk, ap)
        out.append({"name": name, "appearance": ap[:900], "keywords": kws,
                    # ★ 标题原样带着（`角色卡：小满（13 岁，第 2 集）`）—— 判"这张卡
                    #   自己是多少岁"只能靠它：13 岁卡正文通篇在跟 8 岁比，
                    #   "8 岁"出现 9 次、"13 岁"2 次（2026-09-30 实测），
                    #   按"第一个年龄"取会取成 8 岁。
                    "heading": head, "same_as": same_as})
    return out


# 「与某人同一张脸」的识别词
_SAME_FACE_HINTS = ("完全一致", "一模一样", "同一张脸", "如同镜像", "镜像复制",
                    "同「", "分身", "替身", "克隆", "复制体")

# 自指词：主角色卡里常写「与他自己完全一致」这类描述，不能当"另一个角色"。
_SELF_WORDS = ("自己", "他", "她", "本人", "主角", "本尊", "自身")


def _same_face_as(blk: str, appearance: str) -> str:
    """若角色卡声明"与**另一个**角色同一张脸"，返回那个角色的名字。

    识别形如「与主角完全一致的外貌」「同「林宇」的分身」「如同镜像复制」。

    **只看外貌段**（不看背景故事）：主角色卡的背景故事里常出现「分身」一词
    （讲剧情用的），若扫全块，主角会被误判成"某人的分身"而拿自己的图 img2img。
    """
    text = appearance
    if not any(k in text for k in _SAME_FACE_HINTS):
        return ""
    # 优先取「同「X」」/「与X完全一致」里的 X
    for pat in (r"同\s*[「『]([^」』]{1,12})[」』]",
                r"与\s*([^\s，。、]{1,12}?)\s*(?:完全)?(?:一致|相同|一模一样)",
                r"([^\s，。、]{1,12})\s*的(?:分身|替身|镜像|复制体)"):
        m = re.search(pat, text)
        if m:
            cand = _clean_name(m.group(1))
            # 贪婪匹配可能把「完全」吃进捕获组（`与他自己完全一致` → `他自己完全`）
            cand = re.sub(r"(完全|一模)?(一致|相同|一模一样)$", "", cand).strip()
            # 自指词与空值都不算"另一个角色"
            if cand and cand not in _SELF_WORDS:
                return cand
    # 命中了同脸提示词但取不到具体人名 → 视为「主角的同脸角色」。
    # 但只有在角色卡**明确提到分身/替身/克隆**这类词时才算，
    # 否则「完全一致」可能只是描述服装统一（如"两件制服完全一致"）。
    if any(k in text for k in ("分身", "替身", "克隆", "复制体", "镜像")):
        return "主角"
    return ""


# 资产卡分块：`## 资产卡：<名>`（shortdrama 包契约的写法）。
# **必须排除 `#` 一级标题**：village-scale 的第一行是**文件标题**
# `# 资产卡：村口那台会说话的磅秤（单集 3 分钟）`，用 `^#+` 会把它当卡片头
# → 整个文件被切成 1 块（且那"1 块"是全文）→ 所有场景卡丢失（实测）。
# ⛔⛔ 2026-10-05 修：`角色卡` **漏在这个字符组里** —— 而 `_split_asset_blocks` 里
#   `if len(parts) > 1: return parts[1:]` 会**提前返回**、走不到认角色卡的
#   `_KIND_HEAD_RE` 分支 ⇒ 只要文件里有**任何一张道具卡**，角色卡就**全部静默丢失**。
#   实测 `madfate-madness-1004` 的 `assets.md`：写了 4 张角色卡 + 5 张场景卡，
#   `parse_assets` **只解析出 8 个道具**（角色 0、场景 0）⇒ 4 个主角的角色参考图
#   一张都不会生成 ⇒ 「切镜就换人/换装」，而上游 assetdesigner 会因产物不合格反复重写。
#   ⇒ 加「角色卡」进这个字符组，与 `_KIND_HEAD_RE`（本来就认角色卡）保持一致。
_CARD_HEAD_RE = re.compile(
    r"^#{2,4}\s*(资产卡|角色卡|人物卡|场景卡|道具卡|设施卡|器材卡)"
    r"\s*(?:[①②③④⑤⑥S\d]+\s*)?[:：]", re.M)
# ★ 2026-09-23：分块锚扩展（half-narrated 包用「场景卡：/道具卡：」标题——
#   只认「资产卡」会让 9 张卡静默丢 8 张，实测舞狮项目）。
#   kind 识别交给 _KIND_HEAD_RE（场景卡→location、道具卡→prop）。
# 三级标题里的 `（type）` 括号 —— 牛来 / 3D 包实际产物用它标类型
_HEAD_TYPE_RE = re.compile(r"[（(]\s*(character|object|prop|location|scene)\s*[）)]", re.I)
# **第三种标题写法**：`### <卡类型>：<名>`（village-scale 实测 `### 场景卡：收粮站（深夜）`）。
# 类型词直接写在标题前缀里 —— 比 `（type）` 括号更明确，但必须单独认。
_KIND_TYPE = {"角色卡": "character", "场景卡": "location", "道具卡": "prop",
              "设施卡": "prop", "资产卡": "prop"}
_KIND_HEAD_RE = re.compile(r"^(角色卡|场景卡|道具卡|设施卡|资产卡)\s*[:：]\s*(.+?)\s*$")
# ★ 2026-09-26 第四种标题写法：`### 场景 S1：后巷雨夜伏击点`（命案包实测产物）。
#   小节标题是 `## 场景条目`，三级标题只写「场景 + 编号 + 名」——**没有任何类型标记**
#   （既无 `（scene）` 括号、也无「场景卡」前缀）→ 上面三个正则全不认 → 4 个场景
#   **静默掉出注册表** → `scene_lines()` 无场景可注 → 每一镜的光源/陈设全靠模型
#   自由发挥（用户反馈"场景没有锚定"）。
#   修法：认这个写法并**归一成带类型标记的标题**（`名（scene）`），
#   后续的类型归一（scene→location）与描述提取（字段列表格式 ③）全部照旧可用。
_SCENE_HEAD_RE = re.compile(r"^场景\s*(?:[A-Za-z]?\d+)?\s*[:：]\s*(.+?)\s*$")
# 出图提示词段标题（`**外形提示词（出图 prompt）**` / `- 外形/材质/结构（出图提示词，…）：`）。
# 前缀放宽到 20 字：village-scale 的 `- 外形/材质/结构（出图` 正好 13 字，
# 用 `{0,12}` 会漏掉整张卡的描述（实测）。
_PROMPT_HEAD_RE = re.compile(r"^\s*\*{0,2}[^\n*]{0,20}提示词[^\n*]{0,24}\*{0,2}\s*$", re.M)
# 字段行：容忍 `- 名称：x` / `- **名称**：x` / `**名称**：x` 三种写法
_FIELD_RE = r"^\s*[-*]?\s*\*{0,2}\s*%s\s*\*{0,2}\s*[:：]\s*(.+)$"

# ★ 2026-10-05：`_CARD_HEAD_RE.split()` 会把「卡类型」前缀吃掉（`### 角色卡：沈拾`
#   → 块首行只剩 `沈拾`），导致下游判不出类型、**角色卡全被当成 prop**。
#   这里在每个块首行前把前缀补回去，让 `_KIND_HEAD_RE` / `_KIND_TYPE` 能恢复类型。
_KIND_PREFIX_RE = re.compile(
    r"^\s*(资产卡|角色卡|人物卡|场景卡|道具卡|设施卡|器材卡)\s*"
    r"(?:[①②③④⑤⑥S\d]+\s*)?[:：]\s*")


def _restore_kind_head(kind: str, body: str) -> str:
    """把被 `split` 吃掉的「卡类型」前缀接回块首行（幂等：首行本来就带前缀则不动）。

    ★ 2026-10-06 重写：旧签名 `_restore_kind_head(block)` 只在**首行还带前缀**时才动手，
      而 `split` 交出来的块**恰恰是前缀已被吃掉的** ⇒ 它永远走"不动"那一支；
      更糟的是真带前缀时它会把前缀**再补一遍**（`场景卡：场景卡：X`）。
      也就是说 2026-10-05 那次"补前缀"的修复**从来没有生效过**，
      而 `_CARD_HEAD_RE` 当时是非捕获组 ⇒ 「卡类型」三个字整块丢失 ⇒
      `## 场景卡：…` 静默退化成 prop（场景图不再走 location 的宽景通道）。
    """
    body = (body or "").lstrip("\n")
    first = body.split("\n", 1)[0]
    if _KIND_PREFIX_RE.match(first or ""):
        return body
    return "%s：%s" % (kind, body)


def _split_asset_blocks(md: str) -> list[str]:
    """把 `assets.md` 切成资产块 —— **两种历史格式都认**。

    ① `## 资产卡：<名>`（shortdrama 包契约；原实现只认这一种）
    ② `### <名>（<type>）`（牛来 / 3D 包的**实际产物**：小节 `## 场景参考` /
       `## 道具/设施卡` 下的三级标题，形如 `### 村口电线杆场景（scene）`）

    为什么必须扩（2026-09-14 实测事故，用户报告"切镜就换场景"）：
        牛来包的 assetdesigner 契约**没有规定 `assets.md` 的格式**（对比：shortdrama
        包明写 `## 资产卡：` **且强制产出 `assets.contract.json`**），模型便自由发挥成
        格式 ②。而本模块只认 ① → `parse_assets` 对 dawn-broadcast **恒返回 0 条**
        → 2 个场景资产**从未进注册表** → `scene_lines` 无场景可用、无场景锚点可注入
        → 每一镜的光源/陈设由模型自由发挥 → 切镜就换场景。
    ⚠️ 2026-10-05：`split` 会把「卡类型」前缀**从标题里吃掉**（`### 角色卡：沈拾`
       切完只剩 `沈拾`）⇒ `parse_assets` 的 `_KIND_HEAD_RE.match(head)` 再也匹配不到
       ⇒ **类型信息整块丢失**。所以这里把切出来的块**还原**成带类型前缀的标题，
       让下游 `_KIND_HEAD_RE` / `_KIND_TYPE` 能恢复 `character` / `location`。
    """
    parts = _CARD_HEAD_RE.split(md or "")
    if len(parts) > 1:
        # 一个捕获组 ⇒ parts = [前, 卡类型, 正文, 卡类型, 正文, …]
        return [_restore_kind_head(parts[i], parts[i + 1])
                for i in range(1, len(parts) - 1, 2)]
    out: list[str] = []
    cur: str | None = None
    for ln in (md or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", ln)
        if m:
            head = m.group(1)
            if cur is not None:
                out.append(cur)
            # 只收"标题自带类型信息"的三级标题 —— 那才是资产条目；
            # 其余三级标题（如「全片视觉风格锁」下的行）不是卡片。
            # ★ 2026-09-26：`### 场景 S1：<名>` 无类型标记但**确实是场景条目**
            #   → 归一成 `<名>（scene）`，让后续按类型标记的路径照常处理。
            _ms = _SCENE_HEAD_RE.match(head)
            if _ms:
                cur = "%s（scene）" % _ms.group(1)
                continue
            cur = head if (_HEAD_TYPE_RE.search(head) or _KIND_HEAD_RE.match(head)) else None
            continue
        if cur is not None:
            cur += "\n" + ln
    if cur is not None:
        out.append(cur)
    return out


def _strip_ref_boilerplate(txt: str) -> str:
    """剥掉出图提示词的**样板首句**（"…参考图，浅灰纯色背景，无文字、无水印。"）。

    那句是给"生成参考图"用的模板措辞，对**场景锚点**零信息量，却要占锚点限长
    （`assets.SCENE_ANCHOR_MAX`）—— 实测锚点开头就是这 40 字样板，
    留给「光源/色温/陈设」的位置被压缩，超长截断时甚至可能只剩样板。
    """
    m = re.match(r"^[^。]{0,90}。\s*", txt or "")
    if m and re.search(r"参考图|纯色背景|无文字|无水印|无 logo", m.group(0)):
        rest = (txt or "")[m.end():].strip()
        if len(rest) >= 20:          # 剥完不能空（宁可留着样板也不要空锚点）
            return rest
    return txt


def _prompt_from_block(blk: str, fallback: str = "") -> str:
    """取"出图提示词"正文。两种写法都要认：

    ① `- 用途：<外形描述>` —— shortdrama 包契约语义（**用途列就是出图提示词**）
    ② `**外形提示词（出图 prompt）**` + `> ` 引用正文 —— 牛来 / 3D 包实际产物：
       它们的「用途」写的是**出场镜次**（"主角，村长，LN02/… 出镜"），外形单独成段。
       只取「用途」会把出场镜次当做出图提示词 → 注册表里的场景描述是垃圾。

    取不到成段的提示词时回退 `fallback`。
    """
    m = _PROMPT_HEAD_RE.search(blk or "")
    if m:
        buf: list[str] = []
        quoted = False
        for ln in blk[m.end():].splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.startswith(">"):            # 格式 A：`> ` 引用块（牛来包）
                quoted = True
                buf.append(s.lstrip(">").strip())
            elif s.startswith("-") or s.startswith("|") or s.startswith("#") \
                    or s.startswith("---"):
                break                        # 撞到下一个字段/标题 → 段落结束
            elif quoted:
                break                        # 引用块结束
            else:                            # 格式 B：缩进普通正文（village-scale）
                buf.append(s)
        txt = " ".join(x for x in buf if x)
        if len(txt) >= 20:
            return _strip_ref_boilerplate(txt)
    # ★ 2026-09-23：格式 ③ half-narrated 字段列表（- **脸型**：…）——字段列表
    #   本身就是选角照描述（无独立「外形提示词」段），拼接字段名+值。
    #   注意：这段在 if m: 之外——half-narrated 条目根本没有「外形提示词」标题。
    body_lines: list[str] = []
    for ln2 in (blk or "").splitlines():
        s2 = ln2.strip()
        if not s2 or s2.startswith("#") or s2.startswith("---"):
            if body_lines:
                break
            continue
        if s2.startswith("|"):
            continue
        m2 = re.match(r"^[-*]\s*\*{0,2}([^*：:]+?)\*{0,2}\s*[:：]\s*(.+)$", s2)
        if m2:
            body_lines.append("%s：%s" % (m2.group(1).strip(" *"), m2.group(2).strip()))
        elif body_lines:
            body_lines.append(s2)
    txt = "，".join(x for x in body_lines if x)
    if len(txt) >= 20:
        return _strip_ref_boilerplate(txt)
    return fallback


def parse_assets(assets_md: str) -> list[dict]:
    """从 assetdesigner/assets.md 抽资产卡 → [{name, type, keywords, prompt}]。

    资产卡格式（两种都认，见 `_split_asset_blocks`）：
        ## 资产卡：天花板监控屏幕          ← 格式 ①（shortdrama 包契约）
        - 名称：天花板监控屏幕
        - 类型：object
        - 关键词：监控屏幕、六格监控、…
        - 用途：…

        ### 村口电线杆场景（scene）        ← 格式 ②（牛来 / 3D 包实际产物）
        - **名称**：村口电线杆
        - **类型**：scene
        - **关键词**：村口、电线杆、…
        **外形提示词（出图 prompt）**
        > 中国北方乡镇村口，天将亮未亮…

    类型归一：`scene` → `location`（assetdesigner 契约写 `scene`，
    注册表与 `bind()` / `scene_lines()` 的口径是 `location`）。
    """
    from . import assets as _assets_mod   # 函数内导入：类型口径只定义在一处
    out: list[dict] = []
    for blk in _split_asset_blocks(assets_md):
        # ★ 2026-09-25：`- 参考图：images/xxx.png` 是**产物声明**，不是外观描述 ——
        #   必须在解析前整行剔除。`_REF_LINE`（2026-09-14）本就为此而定义，
        #   却**从未接到任何调用点**（死正则）⇒ 格式③（字段列表即描述）
        #   把它拼进了生图提示词；格式①② 只是恰好靠"撞到 `-` 字段就收束"躲过。
        #   `tests_cast.test_ref_line_not_in_prompt` 锁着这条。
        blk = _REF_LINE.sub("", blk)
        head = blk.split("\n", 1)[0].strip()

        def field(key: str) -> str:
            m = re.search(_FIELD_RE % key, blk, re.M)
            return m.group(1).strip() if m else ""

        # 标题可能自带类型：`### 场景卡：收粮站（深夜）` → 名字取后半、类型来自「卡类型」词
        kind = ""
        mk = _KIND_HEAD_RE.match(head)
        if mk:
            kind = _KIND_TYPE.get(mk.group(1), "")
            head = mk.group(2)
        name = _clean_name(field("名称")) or _clean_name(head)
        if not name:
            continue
        # 类型：字段优先 → 标题的 `（type）` 括号 → 标题的「卡类型」词
        # ⚠️ 2026-10-05 修：产物常把字段写成 **英文 key**（`- **type**：character`），
        #   而 `_FIELD_RE % "类型"` 只认中文「类型」⇒ 取不到 ⇒ 一律回落 `prop`
        #   （实测角色卡全被判成 prop，角色参考图不会生成）。这里两种都认。
        typ_cell = field("类型") or field("type") or field("Type") or field("TYPE")
        kw_extra = ""
        # 同行合并写法：`- 类型：scene｜关键词：`收粮站`、`深夜``（village-scale 实测）
        msep = re.split(r"[｜|]", typ_cell, maxsplit=1)
        if len(msep) == 2:
            typ_cell, kw_extra = msep[0].strip(), msep[1]
            kw_extra = re.sub(r"^\s*关键词\s*[:：]\s*", "", kw_extra).strip()
        typ = typ_cell.lower().strip()
        if not typ:
            m = _HEAD_TYPE_RE.search(head)
            typ = m.group(1).lower() if m else ""
        if not typ:
            typ = kind
        if typ in _assets_mod.LOCATION_TYPES:   # 契约的 `scene` / `location` → 注册表口径
            typ = "location"
        if typ not in ("character", "object", "prop", "location"):
            typ = "prop"
        kw_cell = field("关键词") or kw_extra
        kws = [k.strip().strip("`") for k in re.split(r"[、,，/]", kw_cell)]
        kws = [k for k in kws if k]
        if name not in kws:
            kws.insert(0, name)
        prompt = _prompt_from_block(
            blk, fallback=re.sub(r"\s*\n\s*", " ", field("用途")).strip())
        if len(prompt) < 10:
            continue
        out.append({"name": name, "type": typ, "keywords": kws[:8],
                    "prompt": prompt[:700]})
    return out


# ─── 生成 ────────────────────────────────────────────────────────────────────

def _char_prompt(c: dict, emphasis: str = "") -> str:
    """角色参考图提示词。

    **必须显式写「画面中只有一个人物」**（实测）：参考图会被当 refs 喂给
    每一镜的静帧生成，若参考图本身含多个同款人（拼图、镜像构图），模型会
    照抄"多个人"的版式。单格 + 单人数约束是锁脸的前提。

    emphasis：对账不合格重画时，把**卡片里那几项原文**前置成硬要求（只补被画错的
    那几项，不重写整张卡 —— 重写会把对的项也摇一次骰子）。
    """
    tail = ("★ 这几项必须严格照卡片画：%s。" % emphasis) if emphasis else ""
    return ("人物设定参考图，%s。%s竖版构图，浅灰纯色背景，写实短剧质感，"
            "全身到膝盖的清晰造型，五官与服装细节可辨，"
            "画面中只有这一个人物，不要出现第二个人。" % (c["appearance"], tail))


def _char_identity(c: dict) -> str:
    """注册表里那句**文字身份锚点**（无参考图、或生图失败时由它兜底）。

    默认写「全片每镜必须完全一致」。**分龄变体必须换掉"全片"** —— 一句
    "全片必须完全一致"压在 14 岁版卡上，等于命令模型不许让人长大（实测
    shiguan-series-0926 第 2、3 集因此仍是孩童体型，而旁白在念"十六岁那年"）。
    """
    return ("%s的固定形象（%s每镜必须完全一致）：%s"
            % (c["name"], c.get("identity_scope") or "全片",
               str(c.get("appearance") or "")[:220]))


def _turnaround(root: Path, c: dict, ratio: str, log=print,
                source: str = "", emphasis: str = "") -> str:
    """2x2 四视图卷轴（正面/3-4/侧面/背面）→ 取**正面单格**作参考图。

    为什么是四视图而不是单张：单张只锁一个视角，模型对侧面/背面会自行脑补
    出更平滑的造型（旧架构实测：侧面直接漂移）。四视图让每个角度都有依据。

    **但四视图拼图不能直接当参考图**（2026-09-09 实测，见 REF_VERSION）：
    拼图喂进静帧生成 → 模型画出 3 个穿同样制服的人。因此这里把四视图拼图
    归档到 `images/_sheets/` 供人工查看，**注册表只绑正面单格**。
    生图失败时退回单张正面像，保证"至少有参考图"。

    source：参考图路径。**「与主角同一张脸」的角色必须传**——实测事故
    （nightshift-45 监控分身）：角色卡写"与主角完全一致的外貌"，独立文生图
    出来是四个不同的人 + 完全不同的制服，参考图本身成了穿帮源。
    """
    from PIL import Image

    base = _char_prompt(c, emphasis=emphasis)
    refs = [source] if source else None
    pool = keypool.KeyPool.image_pool()

    def _view(_vk, zh):
        """一个视角 = 一次独立生图：四视角之间没有依赖（同一张卡、同一份源照片）。

        失败语义**照旧**：一个视角失败就少一格，不额外加重试（原来就是一次）。
        """
        p = base + "，%s视角，同一张脸、同一着装，保留人物特征" % zh
        idx, k = pool.claim()
        try:
            _, url = providers.gen_image(p, refs=refs, ratio=ratio, key=k)
            return url, _fetch(url)
        except providers.RateLimitError as e:  # 撞限只停这一条 key
            pool.note_rate_limited(idx)
            log("[cast] %s %s 视图限流（%s）：%s" % (c["name"], zh, pool.label(idx), str(e)[:60]))
            return "", None
        except Exception as e:  # noqa: BLE001
            log("[cast] %s %s 视图失败：%s" % (c["name"], zh, str(e)[:80]))
            return "", None

    workers = min(config.image_workers(), len(VIEWS))
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            got = list(ex.map(lambda kv: _view(*kv), VIEWS))
    else:
        got = [_view(k, zh) for k, zh in VIEWS]
    # ★ 顺序按 VIEWS 定，不按完成先后：`imgs[0]` 是**正面单格**（进注册表当参考图），
    #   `first_url` 取第一个成功的视角 url —— 并发若乱序就会把侧脸当成正面锁进参考图。
    imgs, first_url = [], ""
    for url, data in got:
        if not data:
            continue
        if not first_url:
            first_url = url
        imgs.append(Image.open(io.BytesIO(data)).convert("RGB"))
    if not imgs:
        return ""
    if len(imgs) == 1:
        # 只成功一张 → 直接当参考图（比没有强）
        return str(_save_ref(root, c["name"], _png_bytes(imgs[0]), first_url))
    # 四视图拼图归档（人工查看用；不进注册表）
    sheet = Image.new("RGB", (PANEL[0] * 2, PANEL[1] * 2), (240, 240, 240))
    for i, im in enumerate(imgs[:4]):
        im = im.resize(PANEL, Image.LANCZOS)
        sheet.paste(im, ((i % 2) * PANEL[0], (i // 2) * PANEL[1]))
    try:
        sheet_path = sheets_dir(root) / (c["name"] + ".png")
        sheet_path.write_bytes(_png_bytes(sheet))
    except Exception as e:  # noqa: BLE001
        log("[cast] %s 拼图归档失败：%s" % (c["name"], str(e)[:80]))
    # 参考图 = 正面单格（单个人物），不是整张拼图
    return str(_save_ref(root, c["name"], _png_bytes(imgs[0]), first_url))


def _sheet_data_uri(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return "data:%s;base64," % mime + base64.b64encode(path.read_bytes()).decode()


def verify_character_sheets(root: Path, chars: list[dict], *, log=print,
                            llm=None, ask=None) -> dict:
    """★ 定妆照 ↔ 角色卡 对账（判据与为什么需要，见 `media/sheetcheck.py` 文件头）。

    **有界**：核对不过 ⇒ 把矛盾项原文前置强调、重画一次、再核对一次；仍不符就
    响亮记残留并继续。视觉判据是概率性的（静帧 QC 同一张图连审三次能给出 1/1/2 个硬伤），
    没有出口就会变成无限重画烧配额 —— 所以这里写死一次。

    ask=None 时走真实视觉模型；测试注入 `ask(path, appearance) -> 模型回复文本`。
    """
    from . import sheetcheck

    out = {"checked": 0, "redrawn": 0, "residual": []}
    if not config.SHEET_CHECK:
        log("[sheetcheck] 已关（SHORTDRAMA_SHEET_CHECK=0）→ 定妆照不与角色卡对账")
        return out

    def _ask(path, app):
        if ask is not None:
            return ask(path, app)
        from langchain_core.messages import HumanMessage
        from ..llm import chat_for
        msg = HumanMessage(content=[
            {"type": "text", "text": sheetcheck.ask_text(app)},
            {"type": "image_url", "image_url": {"url": _sheet_data_uri(path)}}])
        r = (llm or chat_for("", 900, temperature=0)).invoke([msg])
        return getattr(r, "content", "") or ""

    for c in chars:
        name, app = c.get("name") or "", c.get("appearance") or ""
        path = images_dir(root) / (name + ".png")
        if not app or not path.exists():
            continue
        if not sheetcheck.items_of(app):
            log("[sheetcheck] %s 卡片里没有可对账的外形项 ⇒ 不判（**不等于通过**）" % name)
            continue
        if path.stat().st_size < 2048:
            # 真图最小也是几十 KB；这里挡的是**写坏/截断**的参考图（以及测试桩）。
            # 拿一张读不出内容的图去问模型，模型会一律答"图上没有" ⇒ 假矛盾 + 白烧一次重画。
            log("[sheetcheck] ⚠️ %s 参考图只有 %d 字节（截断或占位）⇒ 无法对账，"
                "**不判**（不等于通过）" % (name, path.stat().st_size))
            continue
        out["checked"] += 1
        try:
            v = sheetcheck.judge(app, _ask(path, app))
        except Exception as e:                                    # noqa: BLE001
            log("[sheetcheck] ⚠️ %s 对账**没跑成**（%s）⇒ 不判（不等于通过）"
                % (name, str(e)[:70]))
            continue
        if not v["mismatch"]:
            log("[sheetcheck] %s 定妆照与角色卡相符（%d 项已核%s）"
                % (name, len(sheetcheck.items_of(app)),
                   "，%d 项模型没答 ⇒ 未判" % len(v["unchecked"]) if v["unchecked"] else ""))
            continue
        for item, seen, kind in v["mismatch"]:
            log("[sheetcheck] !! %s 定妆照与角色卡**矛盾**（%s）：卡片「%s」≠ 图上「%s」"
                % (name, kind, item, seen))
        emphasis = "；".join(m[0] for m in v["mismatch"])
        for attempt in range(1, max(0, int(config.SHEET_CHECK_MAX_REGEN)) + 1):
            log("[sheetcheck] %s 重画定妆照（第 %d 次，把矛盾项前置为硬要求）"
                % (name, attempt))
            if not _turnaround(root, c, config.STILL_RATIO, log=log, emphasis=emphasis):
                log("[sheetcheck] !! %s 重画失败 → 保留原图" % name)
                break
            out["redrawn"] += 1
            try:
                v = sheetcheck.judge(app, _ask(path, app))
            except Exception as e:                                # noqa: BLE001
                log("[sheetcheck] ⚠️ %s 重画后无法复核（%s）⇒ 保留新图、不判"
                    % (name, str(e)[:70]))
                _register(root, c, name)
                break
            if not v["mismatch"]:
                log("[sheetcheck] %s 重画后与角色卡相符 ✓" % name)
                _register(root, c, name)
                break
            for item, seen, kind in v["mismatch"]:
                log("[sheetcheck] !! %s 重画后仍不符（%s）：卡片「%s」≠ 图上「%s」"
                    % (name, kind, item, seen))
        if v["mismatch"]:
            out["residual"].append(name)
            log("[sheetcheck] ⛔ %s 带**未消化的身份矛盾**进全片（重画已到上限 %d 次）。"
                "参考图是全片身份的权威来源 —— 这条会一路带到成片，"
                "要么改角色卡措辞、要么人工换图。" % (name, int(config.SHEET_CHECK_MAX_REGEN)))
    return out


# 资产图（道具/场景）的**无人物**约束。
#
# 实测事故（nightshift-45）：资产卡「灰蓝色便利店制服」的用途里写着
# 「主角与监控分身的视觉统一基础…两件制服必须完全一致」→ 文生图直接画出
# **两个穿制服的人**；「玻璃门入口」的用途写着「映出主角与分身同框的倒影」
# → 画出 **两个人在玻璃倒影里**。这两张图被绑进 LN01 的 refs 后，
# 静帧随即画出 3 个人——**参考图自己成了"画面该有两个人"的教材**。
#
# 所以资产图必须显式声明「没有任何人」：资产图只锁物件外观，人物身份由
# 角色参考图单独锁。正向声明（而非"不要人"这类负面词）实测更稳。
_ASSET_NO_PERSON = ("画面中没有任何人、没有人物、没有顾客、没有店员、没有倒影人影，"
                    "只有这件物件本身。")

# 资产卡「用途」里必须剔除的内容——两类污染源（实测）：
#   1. **人物/复数联想**：主角、分身、店员、顾客、同框、倒影、两件、两人、人影、手部
#      「两件制服必须完全一致」直接让模型画出两个穿制服的人。
#   2. **文字/水印联想**：文字、字符、字母、英文、水印、标签、字迹、可读
#      「边缘有模糊英文与数字水印」让模型把水印渲染成可读乱码。
#
# 处理粒度是**分句**（按 。；，、 切）：删词会留下"映出与的"这类残句
# （实测），整句删除又会把「白底，字迹不可读，别在左胸口袋上方」全清空。
# 分句过滤保留"白底""别在左胸口袋上方"，丢掉"字迹不可读"，两边都不丢。
# 名称 + 关键词始终前置——它们是最干净的对象描述，保证描述永不为空。
_ASSET_BAD_WORDS = ("主角", "分身", "店员", "顾客", "人物", "同框", "倒影",
                    "两件", "两人", "人影", "人", "手部",
                    "文字", "字符", "字形", "字母", "英文", "水印", "标签",
                    "字迹", "可读", "数字")


def _scene_prompt(a: dict) -> str:
    """场景参考图提示词：**环境全景空镜**——与 _asset_prompt（单一物件、竖版、
    浅灰背景）完全不同。无人物、无文字；画幅不写在文字里（由 ratio 参数控制，
    与项目静帧同画幅）。"""
    chunks = []
    for cl in re.split(r"[。；，、]", a.get("prompt") or a.get("appearance") or ""):
        cl = cl.strip()
        if not cl or any(w in cl for w in _ASSET_BAD_WORDS):
            continue
        chunks.append(cl)
    desc = "，".join(chunks)
    desc = re.sub(r"[（(][^）)]*[）)]?", "", desc).strip(" ，、；。")
    names = [a.get("name") or ""] + list(a.get("keywords") or [])[:4]
    head = "、".join(dict.fromkeys(n for n in names if n))
    body = ("%s：%s" % (head, desc)) if desc else head
    return ("%s。环境全景空镜，画面中没有任何人物，光线与陈设细节清晰，"
            "可作为同场景所有镜头的视觉基准。%s" % (body, _ASSET_NO_PERSON))


def _asset_prompt(a: dict) -> str:
    """资产参考图提示词：单一物件 + 无人物 + 无文字。"""
    chunks = []
    for cl in re.split(r"[。；，、]", a.get("prompt") or ""):
        cl = cl.strip()
        if not cl or any(w in cl for w in _ASSET_BAD_WORDS):
            continue
        chunks.append(cl)
    desc = "，".join(chunks)
    # 分句过滤会留下括号残片（「（同色、同款、同褶皱走向）」被切剩「同款，同褶皱走向）」）
    desc = re.sub(r"[（(][^）)]*[）)]?", "", desc)
    desc = re.sub(r"[）)]", "", desc).strip(" ，、；。")
    names = [a.get("name") or ""] + list(a.get("keywords") or [])[:4]
    head = "、".join(dict.fromkeys(n for n in names if n))
    body = ("%s：%s" % (head, desc)) if desc else head
    return ("%s。竖版构图，单一主体，浅灰纯色背景，写实短剧质感，"
            "物体结构与材质细节清晰可辨。%s" % (body, _ASSET_NO_PERSON))


def _single(root: Path, name: str, prompt: str, ratio: str, log=print) -> str:
    try:
        _, url = providers.gen_image(prompt, ratio=ratio, key=config.image_key())
        _save_ref(root, name, _fetch(url), url)
        return str(images_dir(root) / (name + ".png"))
    except Exception as e:  # noqa: BLE001
        log("[cast] %s 参考图失败：%s" % (name, str(e)[:100]))
        return ""


def _bind_source_photo(root: Path, c: dict, data_uri: str) -> bool:
    """把**源照片本身**落成 `<名>.png`（= 直绑参考图）。返回是否成功。

    为什么不走 `_turnaround`（生成四视图再取正面格）：那张四视图是**照着照片重画**的，
    实测（village-bees 老周）**四个视角的脸彼此都不一致**（3/4 侧面与正侧面明显是另一张脸），
    而绑定的只有正面那一格 —— 于是"照片 → 四视图 → 静帧"是**两次重画**，每次都在丢相似度。
    直绑后只剩"照片 → 静帧"**一跳**，最接近用户给的照片。

    落成 `<名>.png` 是为了复用既有链路（`_register` 写 `ref_image`、
    `bind` 经 `_safe_ref_urls` 转 data URI、`_ref_is_current` 的幂等），**不新开分支**。
    """
    if "," not in data_uri:
        return False
    try:
        from PIL import Image

        data = base64.b64decode(data_uri.split(",", 1)[1])
        im = Image.open(io.BytesIO(data)).convert("RGB")
        _save_ref(root, c["name"], _png_bytes(im), "")
        return True
    except Exception:  # noqa: BLE001 -- 直绑失败就回落四视图，不阻断
        return False


def _register(root: Path, item: dict, ref_name: str, *,
              with_image: bool = True) -> None:
    """写进 assets.json（upsert by id）。identity 供无参考图时兜底。

    ref_ver 记录参考图生成规则版本：升级规则后（如从 2x2 拼图改单格正面像）
    旧图不再符合要求，靠它判定"该图需要重生成"——否则续跑会一直跳过旧图。

    `with_image=False`：**登记但没生图**（`still-refs: false` 的包，或生图失败的资产）。
    此时 `ref_image` 置空 —— 语义诚实：这个资产**没有图**，它有描述/identity。
    """
    from . import assets as assets_mod
    reg = assets_mod.load_registry(root)
    ident = item.get("identity") or ""
    # 最后一道防线（2026-09-13）：`keywords` 缺失不能抛 —— 抛了会让**整个 cast
    # 静默退化**（上游只看到"资产生成异常"，之后所有镜都没有参考图）。
    # 语义与 `assets.py` 的锚定契约一致：缺省用 name 自身。
    rec = {
        "id": item["name"], "name": item["name"], "type": item["type"],
        "keywords": item.get("keywords") or [str(item["name"])],
        "priority": 10 if item["type"] == "character" else 8,
        "public_url": "", "url": "",
        "ref_image": (ref_name + ".png") if with_image else "",
        "ref_ver": REF_VERSION,
    }
    if ident:
        rec["identity"] = ident
    # ★ 分龄变体**必须落进注册表**：绑定层（`assets.hits_for_shot`）要靠它把
    #   分镜里的 `@阿旺（14 岁…）` 选到「阿旺（14岁版）」这张图。只活在内存里的
    #   `chars` 列表上，下一次进程读注册表就认不出来了（续跑会退回绑 10 岁那张）。
    if item.get("alias_of"):
        rec["alias_of"] = str(item["alias_of"])
        rec["age_tag"] = str(item.get("age_tag") or "")
    if item.get("alias_names"):
        rec["alias_names"] = [str(x) for x in item["alias_names"] if x]
    # ★ 出图输入的**内容指纹**（2026-09-29）：`_ref_is_current` 靠它判定"卡改了、
    #   图该重画"。存的是**喂给生图的那段文字**，不是图片本身。
    _st = _src_text(item)
    if _st:
        rec["src_fp"] = _fp_of(_st)
    # ★ **场景把描述一起带进注册表**（2026-09-14）：`assets.scene_lines()` 需要它
    #   做提示词的场景锚点，而注册表原本只有 `ref_image`、**没有描述字段**
    #   → 锚点命中实测 **0/49**（描述只躺在 assetdesigner 的契约里）。
    #   ★ 2026-09-25：**不再无条件清空 `ref_image`**。原实现对 location 恒置空，
    #     于是 2026-09-23 新增的「场景出图 + 宽景选择性绑定」链路（`ensure` 的
    #     location 生图分支 + `bind()` 的 `scene_pick` + `prompt.py` 的场景参考图
    #     分工措辞）**全部落空**：图生成了却永远绑不上（实测 38 个 location 条目
    #     `ref_image` 全空、其中 13 个磁盘上确有 png = 纯烧配额）。
    #     `with_image=False` 时置空已由上面构造 `rec` 时覆盖，这里不必再插一句。
    if item.get("type") == "location":
        rec["prompt"] = str(item.get("prompt") or item.get("appearance") or "")
    elif not with_image:
        # 同理：不生图的角色/道具也把描述留下（`identity_lines` 与人工排查都用得上）
        rec["prompt"] = str(item.get("prompt") or item.get("appearance") or "")
    reg["assets"] = [a for a in reg.get("assets", []) if a.get("id") != item["name"]]
    reg["assets"].append(rec)
    (root / assets_mod.REGISTRY_NAME).write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def _src_text(item: dict) -> str:
    """这张参考图是**由哪段文字画出来的**（与 `_scene_prompt`/`_asset_prompt` 的
    取值优先级一致：先 `prompt` 再 `appearance`；角色卡只有 `appearance`）。"""
    return str(item.get("prompt") or item.get("appearance") or "").strip()


def _fp_of(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _ref_is_current(reg: dict, name: str, dest: Path, src_text: str = "",
                    log=print) -> bool:
    """参考图能否复用 = **规则版本对** 且 **出图输入没变过**。

    ★ 第二条是 2026-09-29 补的（实测事故）：`huashan-duel-v4-0928` 的定妆照生成于
      18:28，`worldbuilder.md` 的角色卡在 **20:01 被重写**（补上了「男性／女性」），
      但 `_ref_is_current` 只看 `ref_ver` 和"文件在不在盘上" ⇒ 旧图被判有效、
      一次都没重画。于是**全片两位主角的性别与剧本相反**（32 张静帧 + 12 组视频
      全部忠实继承了那张女性版裴烛设定表），而日志全绿。
      这正是本项目最忌的「失败不可见」：**上游产物变了，下游不失效**。
      （同一类问题在评审判决指纹里也出现过。）
    判据用**内容指纹**；老注册表没有 `src_fp` 时，退回比对已存的
    `identity` / `prompt` 文本 —— 这样**不需要**为了补指纹而无谓重烧一轮图。
    """
    if not dest.exists():
        return False
    rec = reg.get(name) or {}
    try:
        if int(rec.get("ref_ver") or 0) < REF_VERSION:
            return False
    except Exception:  # noqa: BLE001
        return False
    if not src_text:
        return True
    stored = str(rec.get("src_fp") or "")
    if stored:
        if stored != _fp_of(src_text):
            log("[cast] ⚠️ %s 的**出图输入已改**（指纹 %s→%s）→ 参考图判陈旧，重画"
                % (name, stored[:6], _fp_of(src_text)[:6]))
            return False
        return True
    # 老记录：没有指纹，就用它当初存下来的文本比
    old = str(rec.get("identity") or "").split("：", 1)[-1].strip() or str(rec.get("prompt") or "").strip()
    if old and old[:220] != src_text[:220]:
        log("[cast] ⚠️ %s 的**资产卡已改**（注册表里存的描述与当前卡不一致）"
            "→ 参考图判陈旧，重画" % name)
        return False
    return True


CONTRACT_NAME = "assets.contract.json"
"""结构化资产清单（assetdesigner 产出）。

格式（两份列表都可有可无，但至少一份）：
```json
{
  "characters": [{"name": "老魏", "appearance": "五十二岁男性，深灰旧夹克…",
                  "same_face_as": ""}],
  "assets":     [{"name": "出租车", "type": "prop",
                  "keywords": ["出租车", "车厢"], "prompt": "深绿色老式出租车…"}]
}
```
`type` 取 `character` / `prop` / `location`（缺省 `prop`）；
`keywords` 缺省用 `name` 自身。**这是"锚定契约"**：下游按名字精确匹配参考图。
"""


def _load_contract(root: Path):
    """读结构化资产清单。返回 `(chars, items)`；**读不到时返回 `(None, None)`**
    （调用方据此回退到对 `assets.md` 散文的正则解析）。

    为什么优先它（2026-09-12）：散文解析很容易**静默失败** —— `assets.md` 是给
    人读的，换措辞/加列都可能让正则失配，而失配不报错 → 参考图不生成且无人知晓。
    结构化清单让"资产卡"成为**可校验的锚定契约**，名字能拿来做精确匹配。
    """
    for p in (root / "assetdesigner" / CONTRACT_NAME, root / CONTRACT_NAME):
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- 坏文件当作不存在，走散文兜底
            return None, None
        chars = [c for c in (d.get("characters") or []) if c.get("name")]
        items = [a for a in (d.get("assets") or []) if a.get("name")]
        from . import assets as _assets_mod   # 函数内导入：类型口径只定义在一处
        for a in items:
            a.setdefault("type", "prop")
            # 类型归一：`scene`（assetdesigner 契约的写法）→ `location`（注册表口径）。
            # 不归一的代价：`ensure` 的「location 只登记、不生图」分支不触发
            # → 白烧生图配额，且 `bind()` 的 location 排除失效。
            if str(a["type"]).strip().lower() in _assets_mod.LOCATION_TYPES:
                a["type"] = "location"
            if not a.get("keywords"):
                a["keywords"] = [str(a["name"])]
        # ★ 2026-09-13 修复：**chars 也要补 keywords**，否则 `_register` 取
        #   `item["keywords"]` 抛 `KeyError` → 整个 `cast.ensure` 崩（异常被
        #   pipeline 兜成"退化为无参考图"）→ **所有角色/资产都没有参考图**。
        #   实测（rainy-door）：contract 的 characters 只有 name/appearance/same_face_as
        #   → 陈叙的图生成了却登记失败，周奶奶连同全部道具直接没有图
        #   → 静帧 QC 连续两轮判硬伤、视频 5/6 镜判"缺道具/动作"。
        #   注意 **items 一直有这段保护、chars 漏了** —— 这是不对称导致的漏洞。
        for c in chars:
            if not c.get("keywords"):
                c["keywords"] = [str(c["name"])]
        return (chars or None), (items or None)
    return None, None


def split_same_name_cards(chars: list, log=print) -> list:
    """**同名多张卡**（典型：上游给主角写了「8 岁」和「13 岁」两张）⇒ 拆成各自独立的表。

    不拆的后果（2026-09-30 实测 `xiaoman-workshop-1030`）：`_register` 按 `name`
    upsert、图片文件名也是 `<name>.png` ⇒ **后一张把前一张覆盖掉**，全剧只剩一张表；
    而活下来的那张 identity 里只有"比 8 岁时更立体"这种**相对说法、没有绝对年龄**，
    于是第 1 集和第 2 集共用一张不知道几岁的脸。

    拆法：按出现顺序第一张留作基础条目，其余按卡里的年龄段改名成 `名（13岁版）`
    并标 `alias_of` —— 绑定层（`assets.apply_age_variants`）会按分镜括注里的
    年龄段选对应那张，所以**哪张在前都不影响正确性**。
    卡里连年龄段都没写 ⇒ 只能响亮报，不猜（猜出来的年龄会静默锁错脸）。
    """
    from . import variants as variants_mod

    seen: dict = {}
    out: list = []
    for c in chars:
        n = str(c.get("name") or "")
        if not n or n not in seen:
            seen[n] = True
            out.append(c)
            continue
        tag = variants_mod.card_age_tag(c)
        if not tag:
            log("[cast] !! %s 有同名多张卡，这张**没写绝对年龄** → 拆不开，"
                "后一张仍会覆盖前一张（修法：让上游每张卡都写「N 岁」）" % n)
            out.append(c)
            continue
        v = dict(c)
        v["name"] = "%s（%s版）" % (n, tag)
        v["alias_of"] = n
        v["age_tag"] = tag
        v["alias_names"] = [n]
        v["identity_scope"] = "本阶段（%s）" % tag
        v["derived"] = True
        out.append(v)
        log("[cast] 同名年龄卡拆开登记：%s → 另立「%s」（否则两张互相覆盖，全剧只剩一张脸）"
            % (n, v["name"]))
    return out


def covered_ages(chars: list) -> dict:
    """`{基础名: {已被独立表覆盖的年龄段}}` —— 判"这一集有没有自己的表"的依据。

    ️ 不能用"卡文本里出现过这个年龄段"来判（我第一版就是这么写的，实测判错）：
      上游写一张"比 8 岁时更立体"的 13 岁卡，文本里同时含 8 岁和 13 岁，
      于是 8 岁那集也被判成"已锚定"，而注册表里根本没有 8 岁那张表。
    """
    from . import variants as variants_mod

    out: dict = {}
    for c in chars:
        base = str(c.get("alias_of") or c.get("name") or "")
        if not base:
            continue
        tag = str(c.get("age_tag") or "") or variants_mod.card_age_tag(c)
        if tag:
            out.setdefault(base, set()).add(tag)
    return out


def _derive_variant_cards(root: Path, chars: list, items: list, ep, log=print) -> list:
    """从**本集分镜**派生「分龄变体 + 漏登记的新角色」卡（两个实测缺口见 `variants.py` 文件头）。

    任何一步不成立都**如实报一行**再返回空 —— 整条路径是加法：派生不出来就等于退回
    今天的行为，但"退回"必须是看得见的，不能静默（本项目最忌的失败不可见）。
    """
    from .. import guards
    from . import assets as assets_mod
    from . import storyboard as sb_mod
    from . import variants

    e = int(ep or guards.load_manifest(root).get("episode_index", 1) or 1)
    p = guards.resolve_path(root, "scenedesigner", e)
    if not p.exists():
        log("[variants] 第 %d 集分镜不在盘（%s）→ 不分龄、不补新角色" % (e, p))
        return []
    try:
        shots = sb_mod.parse(p.read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001 -- 解析不了就退回旧行为，但要报出来
        log("[variants] 分镜解析异常 → 不分龄：%s" % str(ex)[:90])
        return []
    if not shots:
        log("[variants] !! 第 %d 集分镜**解析出 0 镜** → 不分龄（这本身就是异常，"
            "查 `storyboard.parse` 与表格结构，别当成「这集没人物」）" % e)
        return []
    base = {str(c.get("name") or ""): str(c.get("appearance") or "")
            for c in chars if c.get("name")}
    known = set(base) | {str(a.get("name") or "") for a in items if a.get("name")}
    known |= {str(a.get("name") or "")
              for a in assets_mod.load_registry(root).get("assets", [])}
    spk = {s for st in shots
           for s in assets_mod._dialogue_speakers(st.get("dialogue") or "").split()}
    dflt = variants.default_ages(shots, known, spk, set(base), log=log)
    if dflt:
        log("[variants] 第 %d 集**默认年龄段**（后续拍的 `@名` 不带年龄时按它绑表）：%s"
            % (e, "、".join("%s→%s" % (k, v) for k, v in sorted(dflt.items()))))
    return variants.derive_cards(shots, base, known, spk,
                                 min_mentions=config.VARIANTS_MIN_SHOTS,
                                 max_new=config.VARIANTS_MAX_CARDS,
                                 covered=covered_ages(chars), log=log)


def contract_prose_divergence(prose_md: str, used_items: list) -> list[str]:
    """同名资产里**散文卡与契约卡出图文字不一致**的那些名字（契约卡优先）。

    存在的意义只为一件事：让人知道**自己改的那份不算**。抽成纯函数是为了可测
    （`ensure` 那条路径会生图，测试不该生图）。
    """
    used = {str(a.get("name") or ""): str(a.get("prompt") or "")
            for a in (used_items or [])}
    out = []
    for a in (parse_assets(prose_md or "") or []):
        n = str(a.get("name") or "")
        p = str(a.get("prompt") or "")
        if n in used and p and p != used[n]:
            out.append(n)
    return sorted(set(out))


def ensure(root: Path, *, log=print, force: bool = False,
           max_characters: int = 6, max_assets: int = 12, ep=None) -> dict:
    """为所有角色 / 资产生成权威参考图并登记注册表。返回统计。

    可续跑：图已在盘上且注册表有条目 → 跳过（生图是配额敏感操作）。

    `ep` = 本集集号（不给则取 manifest 的 `episode_index`）。多集连载靠它读到**本集**
    分镜来派生分龄变体与新角色卡；`SHORTDRAMA_AGE_VARIANTS=0` 时整段跳过。
    """
    ratio = config.STILL_RATIO
    wb = root / "worldbuilder" / "worldbuilder.md"
    ad = root / "assetdesigner" / "assets.md"
    made = {"characters": 0, "assets": 0, "skipped": 0, "failed": 0,
            "scenes": 0, "noimg": 0}

    # ★ **结构化资产清单优先**（2026-09-12）。理由：`assets.md` 是给人读的散文，
    # 机器解析靠正则（`parse_assets`）—— 契约一改（换措辞/加列）就可能**静默**
    # 解析失败，而失败不报错 → 参考图不生成、也没人知道（这就是"有卡没图"的根源）。
    # 现在：`assets.contract.json` 存在就用它；不存在再回退散文解析（向后兼容）。
    chars, items = _load_contract(root)
    _from_contract = items is not None
    if chars is None:
        chars = parse_characters(wb.read_text(encoding="utf-8")) if wb.exists() else []
    if items is None:
        items = parse_assets(ad.read_text(encoding="utf-8")) if ad.exists() else []
    if _from_contract and ad.exists():
        # ★ **改在没人读的那份上，必须当场说出来**（2026-10-06 我自己踩的）：
        #   `assets.contract.json` 在就**完全不看** `assets.md`，而两份长得很像、
        #   都能改。实测：我把"机位站在桥面上"写进散文卡，日志照打「跳过 5」、
        #   场景图一秒没重画 —— 没有任何一行说"你改的那份不算"。
        _diff = contract_prose_divergence(ad.read_text(encoding="utf-8"), items)
        if _diff:
            log("[cast] ⚠️ 出图文字取自 **assets.contract.json**（优先级高于 assets.md）"
                "→ 散文卡上 %d 张卡的改动**不会生效**（%s）。要改就改契约那份，"
                "或把契约归档让散文解析接管。"
                % (len(_diff), "、".join(sorted(_diff))[:120]))
    # ★ **场景卡静默丢失必须响亮**（2026-09-26，brawl 实测）：
    #   `assets.md` 里明明有 `### 场景 S1：名` 这类场景小节，但解析器不认 →
    #   `items` 里 0 个 location → `scene_lines()` 无场景锚点 → 每镜光源/陈设
    #   全靠模型自由发挥（用户反馈"场景没有锚定"），而**全程零报错**。
    #   **判据看不见的东西不能算通过** —— 这里按"文件里有场景小节 vs 解析出
    #   几个 location"对账，对不上就打红字（不阻断：场景缺失不该杀整条链）。
    if ad.exists():
        _src = ad.read_text(encoding="utf-8")
        _declared = len(re.findall(r"^#{2,4}\s*场景\s*(?:[A-Za-z]?\d+)?\s*[:：]",
                                   _src, re.M))
        _got = len([a for a in items
                    if str(a.get("type") or "") == "location"])
        if _declared and not _got:
            log("[cast] ⚠️ assets.md 有 %d 个场景小节，但**一个 location 都没解析出来** "
                "→ 场景锚点空、每镜场景由模型自由发挥。查 `### 场景…` 标题写法与 "
                "`cast._split_asset_blocks` 的识别规则。" % _declared)
        elif _declared > _got:
            log("[cast] ⚠️ assets.md 有 %d 个场景小节，只解析出 %d 个 location（丢了 %d 个）"
                % (_declared, _got, _declared - _got))
    # ★ 同名多张年龄卡先拆表（必须在 `char_names` 去重与生图循环**之前**）
    if config.AGE_VARIANTS:
        chars = split_same_name_cards(chars, log=log)
    # 角色卡的姓名若已出现在资产卡里，以角色卡为准（避免重复生成同一人）
    char_names = {c["name"] for c in chars}
    items = [a for a in items if a["name"] not in char_names]

    if not chars and not items:
        log("[cast] 无角色卡/资产卡可解析（worldbuilder.md=%s assets.md=%s）"
            % (wb.exists(), ad.exists()))
        return made

    # ★ **分龄变体 + 漏登记的新角色**（2026-09-30）。多集连载的两个实测缺口：
    #   主角第 2、3 集按剧本长大、但全剧只有一张 10 岁定妆照（冲突时图赢，人就不长了）；
    #   后面集新登场的人从没进过注册表（她的脸每镜自由发挥）。
    #   判据来自**本集分镜的 `@名（括注）`** —— 那是盘上已有的事实，不再要求模型写一遍。
    if config.AGE_VARIANTS:
        chars = list(chars) + _derive_variant_cards(root, chars, items, ep, log=log)

    reg = {}
    try:
        from . import assets as assets_mod
        reg = {a.get("id"): a for a in assets_mod.load_registry(root).get("assets", [])}
    except Exception:  # noqa: BLE001
        pass

    # ★ **反质量包（`pack.json` 的 `still-refs: false`）只登记、不生图**（2026-09-14）
    #
    # 与 location 那条同型，但影响面更大（角色 + 道具全都算）。为什么必须跳过生图：
    #   · `pipeline` 在 `still-refs=false` 时**根本不调 `assets.bind`** →
    #     `refs_by_shot = {}` → 生出来的图**没有任何消费方**；
    #   · 而 `cast.ensure` 原本无条件生图 → 牛来包里 3 角色 + N 道具
    #     = **每个项目白烧 3+N 次生图配额**（实测 paper-crane 的同类浪费是 4 次/location）。
    #   · 牛来包自己的说明也写着「绑参考图必被拉回精致低模」—— 这个开关是它刻意的设计。
    #
    # **但仍必须 `_register`**：`assets.identity_lines()` 的 docstring 明写
    # 「pack 关闭参考图（**如牛来风格**）后，身份靠 assets.json 的 `identity` 压成一句
    # 固定锚点逐镜贴上」→ **注册表条目是载荷路径，不是可选项**。
    # 所以这里只砍掉"生图"这一步，登记照做（`ref_image` 置空，语义诚实：没图、有描述）。
    refs_on = True
    try:
        from . import style as _style
        refs_on = _style.still_refs_enabled(root)
    except Exception:  # noqa: BLE001 -- 判定不了就按"要图"处理（保守，不改变旧行为）
        refs_on = True
    if not refs_on:
        log("[cast] pack 关闭参考图（still-refs=false）→ **只登记角色/道具、不生图**"
            "（图不会被绑定，生图=白烧配额；身份由注册表 identity 文字锚点承担）")

    # ★ `max_characters` 是**基础角色卡的生图预算**，不作用于派生卡（2026-09-30）。
    #   派生卡另有一份自己的上限（`config.VARIANTS_MAX_CARDS`），且它已在 `variants`
    #   侧按出场镜数排过序 —— 若把它们也塞进这个切片，"3 个基础角色 + 2 个年龄段"
    #   在 `max_characters=6` 下没事，但 6 个基础角色的项目会**把主角的 16 岁版挤掉**，
    #   而那正是这次要修的那个病。与下方 `max_assets` 的截断可见性同一条纪律。
    _base = [c for c in chars if not c.get("derived")]
    _derived = [c for c in chars if c.get("derived")]
    if len(_base) > max_characters:
        log("[cast] ⚠️ 基础角色卡 %d 个超过预算 %d → 只出前 %d 个的图（被截断：%s）"
            % (len(_base), max_characters, max_characters,
               "、".join(str(x.get("name")) for x in _base[max_characters:])[:140]))
    for c in _base[:max_characters] + _derived:
        dest = images_dir(root) / (c["name"] + ".png")
        if not refs_on:
            # 与下面的生成分支**设置完全相同的 identity/type**，只是不生图 ——
            # 保证 `identity_lines` 拿到的锚点文本与有图时一字不差。
            c["identity"] = _char_identity(c)
            c["type"] = "character"
            _register(root, c, c["name"], with_image=False)
            made["noimg"] += 1
            log("[cast] 登记角色（不生图）：%s —— 身份走注册表 identity 锚点" % c["name"])
            continue
        if (not force) and _ref_is_current(reg, c["name"], dest, _src_text(c), log=log):
            made["skipped"] += 1
            continue
        if c.get("alias_of"):
            # **分龄变体：以基础角色的定妆照做 img2img**，只改年龄/体型/服装。
            # 为什么不能纯文生图：那等于重新抽一个人 —— 与 `same_as` 那条同型的理由，
            # **脸的依据必须来自已有的那张脸**（实测 nightshift-45 的"分身"事故）。
            _alias_uri = _file_ref_uri(images_dir(root) / (str(c["alias_of"]) + ".png"))
            if not _alias_uri:
                log("[cast] ⚠️ %s 找不到基础定妆照 images/%s.png → 退回**纯文生图**"
                    "（这一版脸会漂；先确认基础角色出图成功）"
                    % (c["name"], c["alias_of"]))
            c["identity"] = _char_identity(c)
            c["type"] = "character"
            if _turnaround(root, c, ratio, log=log, source=_alias_uri):
                _register(root, c, c["name"])
                made["characters"] += 1
            else:
                _register(root, c, c["name"], with_image=False)
                made["noimg"] += 1
                made["failed"] += 1
                log("[cast] ⚠️ %s 分龄变体参考图生成失败 → **降级为文字身份锚点**"
                    % c["name"])
            continue
        log("[cast] 生成角色参考图：%s" % c["name"])
        c["identity"] = _char_identity(c)
        c["type"] = "character"
        # 「与某人同一张脸」→ **直接复用那个人的参考图**。
        #
        # 为什么不重新生成：实测（2026-09-09）即便用源图做 img2img，模型也锁不住
        # 脸——四格全重生时脸逐格漂移，单格重生仍被画成"另一张脸 + 浓眼影"。
        # 既然角色卡明说"同一张脸"，最可靠的做法就是不生成：把源角色的参考图
        # 复制过来。人物的区别（姿态/神态/动作延迟）是**分镜层面**的事，
        # 由每镜的画面描述承担，不该由参考图承担。
        src_name = ""
        sa = c.get("same_as") or ""
        if sa:
            src_char = next((x for x in chars if x["name"] == sa), None)
            cand = images_dir(root) / (sa + ".png")
            if not cand.exists() and sa == "主角":
                # 「主角」→ 取注册表里优先级最高的角色
                prot = [k for k, v in reg.items()
                        if (v or {}).get("type") == "character"]
                if prot:
                    src_char = src_char or next((x for x in chars if x["name"] == prot[0]), None)
                    cand = images_dir(root) / (prot[0] + ".png")
            if cand.exists():
                src_name = cand.stem
                try:
                    dest.write_bytes(cand.read_bytes())
                    urlf = cand.with_name(cand.name + ".url")
                    if urlf.exists():
                        dest.with_name(dest.name + ".url").write_text(
                            urlf.read_text(encoding="utf-8"), encoding="utf-8")
                    log("[cast] %s 复用 %s 的参考图（同一张脸，不重新生成）"
                        % (c["name"], src_name))
                    _register(root, c, c["name"])
                    made["characters"] += 1
                    continue
                except Exception as e:  # noqa: BLE001
                    log("[cast] %s 复用参考图失败：%s（退回独立生成）"
                        % (c["name"], str(e)[:80]))
            # 复用了图就不再改 appearance——保持角色卡原文，避免相对指代
            # （"与主角完全一致"）被模型理解成"画面里两个相同的人"。
        # ★ 用户提供的**人物源照片**（2026-09-15 补的接线）。
        #
        # 实测缺口（三处证据，缺一就导致"用户给了照片却没用上"）：
        #   ① `roles.TOOL_NOTE` 明写「技能文档里若提到 generate_image /
        #      generate_turnaround / ingest_reference 等**本系统未提供**的工具，
        #      直接跳过」→ 角色**无法**自己把照片注册进注册表；
        #   ② 而那句"用 `ingest_reference` 把该图注册为权威参考"**只存在于死文件**
        #      `packs/shortdrama/worldbuilder.md`（`_role_skill` 从不读平铺 .md），
        #      **live 契约 `worldbuilder/SKILL.md` 只字未提用户照片**；
        #   ③ 这里原先**硬传 `source=""`** → `_turnaround` 的 img2img 通路从未启用。
        # ⇒ 用户说"用我给你的人物做主角"时，照片**静默不生效**（照旧纯文生图）。
        #
        # 现在：`images/<角色名>.source.<ext>` 存在 → 传 `source=`（走 img2img 锁本尊长相）。
        # **没有该文件时行为与历史一字不变**（仍 `source=""`）—— 纯加法，可回退。
        # 源照片必须编成 **data URI**（接口不吃本地路径，2026-09-15 实测 400）。
        _src = _source_ref_uri(root, c["name"])
        if _src:
            log("[cast] 用**源照片**锁脸：%s ← %s（已编码为 data URI，%d KB）"
                % (c["name"], Path(_source_photo(root, c["name"])).name,
                   len(_src) // 1024))
        # ★ **四宫格设定表**（默认 auto：有源照片即走；`SHORTDRAMA_SOURCE_SHEET=0` 可关）：
        #   源照片 →（扩图式）白底头肩像 + 正/侧/背三视图 → 拼成一张表 ⇒ **绑这张表**。
        #   若用户自备 `images/<角色名>.sheet.<ext>` ⇒ **直接用它，不消耗生成**（见 `sheet.ensure`）。
        #   为什么比"直绑照片"强：表里有**多点视角**（正/侧/背 + 大头像），换机位时脸部依据更足；
        #   而每镜仍走普通 reference（引用该资产 + 分镜文字自由描述姿势）——**姿势不受照片限制**。
        #   纪律（白底/妆造跟角色卡外貌段/显式性别体态/4K/比例）全在 `sheet.py` 的提示词里，改前先读它的文件头。
        if _src and config.SOURCE_SHEET:
            from . import sheet as sheet_mod

            _sp = sheet_mod.ensure(root, c["name"], _source_photo(root, c["name"]),
                                   costume=c.get("appearance") or "", log=log)
            if _sp:
                c["identity"] = _char_identity(c)
                c["type"] = "character"
                _register(root, c, c["name"])
                made["characters"] += 1
                log("[cast] %s 用**四宫格设定表**当参考图：images/%s.png"
                    % (c["name"], c["name"]))
                continue
            log("[cast] ⚠️ %s 四宫格设定表生成失败 → 回落直绑源照片" % c["name"])
        # ★ **直绑**（默认）：把照片本身当参考图 —— 只经"照片→静帧"一跳，最接近原图。
        #   要走"先重画四视图再绑正面格"的老路，设 `SHORTDRAMA_SOURCE_TURNAROUND=1`
        #   （取舍见 `config.SOURCE_TURNAROUND`）。
        if _src and not config.SOURCE_TURNAROUND:
            if _bind_source_photo(root, c, _src):
                c["identity"] = _char_identity(c)
                c["type"] = "character"
                _register(root, c, c["name"])
                made["characters"] += 1
                log("[cast] 源照片**直绑**为参考图：%s ← %s（只经「照片→静帧」一跳）"
                    % (c["name"], Path(_source_photo(root, c["name"])).name))
                continue
            log("[cast] ⚠️ %s 源照片直绑失败 → 回落到四视图生成" % c["name"])
        if _turnaround(root, c, ratio, log=log, source=_src):
            _register(root, c, c["name"])
            made["characters"] += 1
        else:
            # ★ **生图失败 ≠ 没有身份锚点**（2026-09-15 实测事故 village-bees）。
            #
            # 原实现只在生图成功时才 `_register` —— 于是 12 次生成全 400 失败后
            # 注册表里**一个角色都没有** → `identity_lines` 返回空 →
            # **参考图没了、文字身份锚点也没了**（双重失效）→ 成片人物只是
            # "像那么回事"，不是用户给的那个人（LN03 实测：脸型像、服装全错）。
            # ⇒ 降级登记（`with_image=False`，与反质量包同一条路径）：**图没有、描述留着**。
            c["identity"] = _char_identity(c)
            c["type"] = "character"
            _register(root, c, c["name"], with_image=False)
            made["noimg"] += 1
            made["failed"] += 1
            log("[cast] ⚠️ %s 参考图生成失败 → **降级为文字身份锚点**"
                "（图没有、描述留着；避免身份双重失效）" % c["name"])

    # ★ **年龄表没落地必须单独喊一行**（2026-09-30，放在生图循环**之后**才可比盘）。
    #   绑定层（`assets.apply_age_variants`）找不到变体条目时**回落基础卡** ——
    #   那正是本次要修的病：第 2 集 18 次点名里 17 次不带年龄，全绑回 8 岁那张孩童表，
    #   而旁白在念"十三岁"。回落本身是必要的（没图就不能绑），但它不能是静默的。
    if config.AGE_VARIANTS:
        _miss = [str(c.get("name")) for c in chars if c.get("derived")
                 and c.get("age_tag")
                 and not (images_dir(root) / (str(c.get("name")) + ".png")).exists()]
        if _miss:
            log("[cast] !! 本集要用的**年龄表不在盘上**：%s → 绑定静默回落基础卡"
                "（同一张脸演所有年龄）。查上面这些卡为什么没出图，别把这次当成"
                "「分龄已生效」。" % "、".join(_miss))

    from . import assets as _assets_mod   # 函数内导入：类型口径只定义在一处
    # ★ `max_assets` 是**生图预算**，不是"资产条数上限"（2026-09-15 实测事故）。
    #
    # 事故：`for a in items[:max_assets]`（`max_assets=12`）对**整个**清单切片，而 `items`
    #   里混着**永不需要生图的 location** → village-tree 的 15 条（9 道具 + 6 场景）
    #   被切成 12 条 → **末尾 3 个场景被静默丢弃**（日志只报"场景（仅登记）3"，零告警）。
    #   后果：`assets.json` 里缺 3 个 location → `scene_lines` 只能靠"画面描述兜底"
    #   把锚点猜回来；**一旦兜底失效，那 3 个场景立刻失去锚点**（场景逐镜漂移），
    #   而且**没有任何一处会报错** —— 用户反馈的"场景变来变去"正是这一条的直接后果。
    #   与 `_chars_by_name(max_n=2)` **是同一类错误**：把「配额/计费上限」当成「数据收集上限」。
    #   ⇒ 上限只作用于**真正会生图的那部分**，且**截断必须可见**。
    _locs = [a for a in items
             if str(a.get("type") or "") in _assets_mod.LOCATION_TYPES]
    _rest = [a for a in items
             if str(a.get("type") or "") not in _assets_mod.LOCATION_TYPES]
    if len(_rest) > max_assets:
        log("[cast] ⚠️ 会生图的资产 %d 个超过预算 %d → 只登记前 %d 个"
            "（**这是配额上限、不是数据上限**；被截断的：%s）"
            % (len(_rest), max_assets, max_assets,
               "、".join(str(x.get("name")) for x in _rest[max_assets:])[:140]))
    for a in _locs + _rest[:max_assets]:
        # ★ **location 只登记、不生图**（2026-09-14）
        #
        # 为什么（两条都是既定事实，不是我新加的判断）：
        #   ① `bind()` **从不绑定 location** —— 场景参考图是**空镜内景**、自带机位，
        #      喂进"近景 @周平 手部特写"这类镜等于同时说"用一个空荡的全景"，
        #      会把构图拉回大 Wide，与分镜要求的景别打架（`qc.review_shot_type` 会判不符）。
        #   ② `validate_assets` **专门豁免** location 的"图在盘"检查
        #      （"location 不绑 → 不要求它在盘"）。
        #   → 生成它 = **每个项目白烧 N 次生图配额**（实测 paper-crane：4 个 location）。
        #
        # 场景的作用改由**文本锚点**承担：`assets.scene_lines()` 取该场景的描述
        # （光线/色温/陈设）注入提示词 —— **信息进来、机位不进来**。
        # 契约侧同步要求「**光源只有一个出处** = 场景资产描述」（原先光源归
        # 「视觉风格」列逐镜自由写，于是同一部片里"冷白荧光"和"暖黄灯圈"打架 →
        # 成片漂成完全不同的场所）。
        #
        # **仍要 `_register`**：注册表条目是 `scene_lines` 的关键词来源，也是
        # 资产契约门「注册表有条目」的判据。描述由 `_register` 一起写进去。
        if str(a.get("type") or "") == "location":
            # ★ 2026-09-23：场景升级为**视觉锚定**（用户要求：人物/场景/关键道具
            #   三类全部出图锚定）——location 也生图（照抄资产生图路径），参考图
            #   随静帧/视频传给生成端。此前场景只有文字锚点，实测灯下棋场景跨镜
            #   漂移（老虎机/灯光每镜凭文字想象）。refs_on=False 的包（牛来反
            #   质量）保持旧路径（仅登记）；生图失败 → 回落文字锚点，不挡链。
            rec = reg.get(a["name"]) or {}
            dest_s = images_dir(root) / (a["name"] + ".png")
            if (not force) and _ref_is_current(reg, a["name"], dest_s, _src_text(a), log=log):
                made["skipped"] += 1
                continue
            # ★ 2026-09-25：补回 `scenes` 计数 —— 09-23 新写的这个分支漏了它，
            #   导致 `scenes` 恒为 0（末尾统计日志与实际不符）。放在跳过判定**之后**：
            #   `scenes` 的语义是「本轮实际处理的场景数」，图已在盘的算 `skipped`、不重复计。
            made["scenes"] += 1
            if not refs_on:
                _register(root, a, a["name"], with_image=False)
                made["noimg"] += 1
                log("[cast] 登记场景（不生图）：%s —— pack 关闭参考图" % a["name"])
                continue
            log("[cast] 生成场景参考图：%s（location）" % a["name"])
            if _single(root, a["name"], _scene_prompt(a), ratio, log=log):
                _register(root, a, a["name"])
                made["scenes_img"] = made.get("scenes_img", 0) + 1
            else:
                _register(root, a, a["name"], with_image=False)
                made["failed"] += 1
                log("[cast] 场景生图失败：%s → 回落文字锚点" % a["name"])
            continue
        dest = images_dir(root) / (a["name"] + ".png")
        if not refs_on:
            # 与角色同一条规则：登记但不生图（`assets` 计数不增，计入 `noimg`）
            _register(root, a, a["name"], with_image=False)
            made["noimg"] += 1
            log("[cast] 登记资产（不生图）：%s（%s）—— pack 关闭参考图" % (a["name"], a["type"]))
            continue
        if (not force) and _ref_is_current(reg, a["name"], dest, _src_text(a), log=log):
            made["skipped"] += 1
            continue
        log("[cast] 生成资产参考图：%s（%s）" % (a["name"], a["type"]))
        if _single(root, a["name"], _asset_prompt(a), ratio, log=log):
            _register(root, a, a["name"])
            made["assets"] += 1
        else:
            made["failed"] += 1

    # ★ **定妆照 ↔ 角色卡 对账**（2026-09-29 补的缺口：卡片写玄黑高马尾、图画成酒红发，
    #   全片 18 镜忠实继承，而链上没有任何一环核得出来）。放在**所有图都落盘之后**跑，
    #   这样"本轮新生成"与"复用盘上旧图"两条路径一起覆盖 —— 内容指纹只保证
    #   "卡片没改就不重画"，它不保证"画的就是卡片写的"。
    chk = verify_character_sheets(root, chars, log=log)
    made["sheet_checked"] = chk["checked"]
    made["sheet_residual"] = chk["residual"]

    log("[cast] 完成：角色 %d / 资产 %d / 场景（仅登记）%d / 不生图登记 %d / 跳过 %d / 失败 %d"
        % (made["characters"], made["assets"], made.get("scenes", 0),
           made.get("noimg", 0), made["skipped"], made["failed"]))
    return made
