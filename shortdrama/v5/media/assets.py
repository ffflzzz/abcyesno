# -*- coding: utf-8 -*-
"""资产层：把角色三视图 / 道具图解析为每镜的参考图（public URL）。

语义与旧架构一致（照搬，不自造）：
  - 参考图必须是 **public URL 或 base64 data URI**（Agnes 拒绝裸本地路径 → 400）
  - 解析顺序：public_url → url → 同名 .url 伴侣文件 → 内联 base64
  - 匹配：资产 keywords 出现在镜头文本里即命中；**每镜最多 1 张人物参考图**
    （2026-09-09 A/B 实测：多绑一张人物图就多画一个人）
  - **主角兜底**：agent 常把主角叫成别的名字导致关键词漏匹配 → 主角肖像强制
    绑到每个**确实有人物出场**的镜头（空镜不绑，否则凭空造人）
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path

REGISTRY_NAME = "assets.json"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def load_registry(root: Path) -> dict:
    p = root / REGISTRY_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"assets": []}
    return {"assets": []}


def auto_sync(root: Path, max_assets: int = 24) -> dict:
    """images/ 里没登记的图直接补进注册表（名字即关键词）。

    **只扫 images/ 根层，不递归**：`images/_sheets/` 存的是角色四视图拼图，
    仅作人工查看。拼图若被收编成资产并绑进镜头，模型会把四个格子读成
    "画面里该有多个同款人"（2026-09-09 实测 LN01 画出 3 个人）。
    """
    reg = load_registry(root)
    known = {a.get("ref_image") or a.get("public_url") or a.get("url", "")
             for a in reg.get("assets", [])}
    images = root / "images"
    if not images.exists():
        return reg
    for f in sorted(images.iterdir())[:max_assets]:
        if not f.is_file():
            continue          # 子目录（_sheets/）跳过
        if f.suffix.lower() not in IMAGE_SUFFIXES or f.name in known:
            continue
        # ★ **用户提供的源照片是"输入"，不是资产**（2026-09-15 实测事故 village-bees）：
        #   `images/老周.source.jpg` 被无条件收编 → 注册表里冒出 `老周.source` 这个
        #   **伪角色**（`_infer_type` 见到 "source" 就判 character），关键词是
        #   "老周.source" —— 既不匹配任何镜（绑不上角色），又把注册表搅浑。
        #   源照片的作用是喂 `_turnaround(source=...)` 的 img2img，不该有自己的资产条目。
        if ".source" in f.stem:
            continue
        reg.setdefault("assets", []).append({
            "id": f.stem, "name": f.stem, "type": _infer_type(f.stem),
            "keywords": [f.stem], "priority": 8,
            "public_url": "", "url": "", "ref_image": f.name,
        })
    (root / REGISTRY_NAME).write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return reg


def _infer_type(name: str) -> str:
    lowered = name.lower()
    if any(k in name for k in ("front", "side", "back", "正面", "三视")):
        return "character"
    if any(k in lowered for k in ("source", "主角", "主角.source")):
        return "character"
    return "prop"


def _safe_ref_urls(a: dict, root: Path) -> list[str]:
    """一个资产可绑多张参考图（如角色正视图 + 四分之三视图）。

    单张 ref_image 只能锁定一个视角，模型对"侧面/背面"会自行脑补出更
    平滑的造型——牛来风格下这直接导致风格漂移。ref_images 列表优先，
    回落到 ref_image；两者都按同一套解析规则转成 public URL / data URI。
    """
    refs = a.get("ref_images") or []
    if not isinstance(refs, list):
        refs = []
    single = str(a.get("ref_image") or "").strip()
    if single:
        refs = [single] + [r for r in refs if r != single]
    out = []
    for r in refs:
        u = _resolve_one({**a, "ref_image": r, "public_url": "", "url": ""}, root)
        if u and u not in out:
            out.append(u)
    if out:
        return out
    return [u for u in [_resolve_one(a, root)] if u]


def _resolve_one(a: dict, root: Path) -> str | None:
    """单个参考项 → public URL / data URI（原 _safe_ref_url 逻辑）。

    优先级：**`.url` 伴侣里的 http 地址优先，本地文件兜底**。
    2026-09-13 曾短暂反转成"本地优先"，实测后**已回滚**：反转的动机（"CDN 地址
    可能失效导致绑了参考图仍换脸"）被证伪——那些地址当时全部 HTTP 200 有效，
    而反转会让每镜请求体 +175KB（两张参考图转 data URI）。**别重复这个尝试**，
    除非真遇到"URL 不可达"的证据（如离线归档需求）。
    """
    for key in ("public_url", "url"):
        u = str(a.get(key) or "").strip()
        if not u:
            continue
        if u.startswith(("http://", "https://", "data:")):
            return u
        # 本地路径：优先同名 .url 伴侣文件（里面存 public URL）
        cand = root / u.lstrip("/")
        url_file = cand.with_name(cand.name + ".url")
        if url_file.exists():
            try:
                t = url_file.read_text(encoding="utf-8").strip()
                if t.startswith(("http://", "https://")):
                    return t
            except Exception:  # noqa: BLE001
                pass
        if cand.exists():
            return _data_uri(cand)
    ref = str(a.get("ref_image") or "").strip()
    if ref:
        cand = root / "images" / ref
        url_file = cand.with_name(cand.name + ".url")
        if url_file.exists():
            try:
                t = url_file.read_text(encoding="utf-8").strip()
                if t.startswith(("http://", "https://")):
                    return t
            except Exception:  # noqa: BLE001
                pass
        if cand.exists():
            return _data_uri(cand)
    return None


def _safe_ref_url(a: dict, root: Path) -> str | None:
    """兼容旧调用：取该资产的第一张参考图。"""
    urls = _safe_ref_urls(a, root)
    return urls[0] if urls else None


MAX_REF_PX = 768   # 参考图长边上限：原图 130KB × 5 张 ≈ 900KB/请求，降采样后 ~1/6


def _data_uri(path: Path) -> str | None:
    try:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        raw = path.read_bytes()
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(raw))
            im.load()
            if max(im.size) > MAX_REF_PX:
                ratio = MAX_REF_PX / float(max(im.size))
                im = im.resize((int(im.width * ratio), int(im.height * ratio)))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=85)
                raw = buf.getvalue()
                mime = "image/jpeg"
        except Exception:  # noqa: BLE001 -- 没装 PIL 或不是图片：原样内联
            pass
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
    except Exception:  # noqa: BLE001
        return None


# 人物出场的判据：分镜画面描述/对白里出现角色名、别名或人称代词。
#
# 两个用途（必须**共用同一判据**，否则会打架）：
#   1. `bind` 的主角兜底：只有本镜真有人物时才强制绑主角肖像。
#   2. `prompt.build_still_prompt` 的 SINGLE_PERSON 声明。
#
# 实测事故（nightshift-45 LN02）：分镜是「监控屏幕特写…第六格画面里站着一道
# 人影背影」——画面里**不该有真人**，但 `bind` 的兜底不看内容，硬绑了主角
# 肖像 → 模型把"屏幕上的人影"画成两个站着的真人（一正一背）。
#
# 「人影/背影/倒影」是**画面元素**，不是出场人物。但注意不能整镜一刀切：
# LN05「林宇…指尖按在监控屏幕边缘…屏幕里那道人影仍在原地」**同时**有真人
# 主角和屏幕人影。因此按**分句**判：含影子词的分句不算人物出场，其余分句照常。
_PERSON_HINTS = ("主角", "男主", "女主", "本人",
                 "店员", "顾客", "男人", "女人", "老人", "孩子")
_SHADOW_WORDS = ("人影", "背影", "倒影", "影子", "剪影")
_SENT_SPLIT = re.compile(r"[。；，、\n]")


def person_in_text(text: str, names: list[str] | None = None) -> bool:
    """本镜是否有**真人出场**（用于主角兜底与单人声明）。

    按分句判定：含影子词（人影/背影/倒影）的分句跳过，其余分句照常匹配。
    names：角色姓名（来自注册表/角色卡）——分镜写"林宇"时靠它命中，
    不能只靠写死的别名表。
    """
    hints = tuple(_PERSON_HINTS) + tuple(n for n in (names or []) if n)
    for sent in _SENT_SPLIT.split(text or ""):
        if not sent:
            continue
        if any(w in sent for w in _SHADOW_WORDS):
            continue
        if any(k in sent for k in hints) or "他" in sent or "她" in sent:
            return True
    return False


#: 群体量词的**人数兜底**（2026-09-15）。分镜常写「三人」「两个人」而**不点姓名**，
#: 此时按姓名匹配只算出 0~1 人 → 人物数量声明与实际冲突。实测 village-tractor LN24：
#: 正文写「三人喘着气站着」，姓名只认到主角 1 人 → 注入「画面中只有一个人物」，
#: 结果模型画出**三个陌生人**（既违风格又违身份）。
#: 这是"归一化 + 兜底"里的**兜底**：仅在正文**显式写出**群体人数时生效，
#: 且调用方取 `max(姓名数, 量词数)` —— 只会把人数抬到事实值，不会凭空造人。
#: 顺带覆盖「三三两两」这类叠词吗？不覆盖：那类写法没有确定人数，不该拿来当判据。
_CAST_NUM_RE = re.compile(r"([一二两俩三四五六七八九十]|\d{1,2})\s*个?\s*(?:人物|人)")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def cast_numeral(text: str) -> int:
    """正文里**显式写出**的人物数量（取最大值；没写则 0）。"""
    best = 0
    for m in _CAST_NUM_RE.finditer(text or ""):
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)
        if n > best:
            best = n
    return best


def hits_for_text(reg: dict, text: str, max_n: int = 5) -> list:
    hits = [a for a in reg.get("assets", [])
            if a.get("keywords") and any(k in text for k in a["keywords"])]
    # 角色优先于道具（同优先级时）
    hits.sort(key=lambda a: (0 if a.get("type") == "character" else 1,
                             -int(a.get("priority", 8))))
    return hits[:max_n]


# ── @资产引用（2026-09-12 新契约）────────────────────────────────────────────
# 分镜的正文里用 `@资产名` 点名本镜要用的资产。官方真实实例里场景/角色/道具
# **全部**带 @ 引用，我们的 scenedesigner 契约也已要求这么写。
# 有 @ 就能做**精确匹配** —— 不再靠 keywords 子串去猜（后者绑不上还不报错）。
_AT = re.compile(r"@([^\s，。；、,;：:！？!?（）()\[\]【】「」\"'|/]+)")

# 参与匹配的镜字段（与分镜契约一致；不含时长/景别等结构化列）
_SHOT_TEXT_KEYS = ("visual", "dialogue", "sfx", "tail", "join_note")


def _shot_text(shot: dict) -> str:
    return " ".join(str(shot.get(k) or "") for k in _SHOT_TEXT_KEYS)


# 判定"**本镜是谁**"用的键：少了 `join_note`。
# 为什么排除它（2026-09-13 实测 noodle-night LN09）：join_note 描述的是**上一镜**
# 的落幅 —— 原文「承接上一镜老陈擦灶台的落点」，于是"老陈"被算成本镜人物，
# 抢在真正的「女孩」前面（同 priority 按注册表序）→ 又绑错了脸。
# 注意 `@` 匹配仍用 `_shot_text`（含 join_note）：那里识别的是"引用了哪个资产"，
# 与本镜主体是谁是两回事。
#
# ★ 2026-09-15 再把 `dialogue` 从**整格**降级为**只取说话人**（同一类问题的第二例）：
#   实测 village-bees LN01 的对白是 `阿凯（喘着气，急）：老周，我那三排蜂箱一夜之间全没了！`
#   —— 句中**喊的是老周**。整格文本参与匹配 → `老周` 被判为在场 →
#   **老周的照片也被绑进这一镜**（人物图上限 2）→ `<Picture 1>` 落到老周 →
#   静帧把主角画成老周（而分镜要的是阿凯，老周到 LN02 才出场）。
#   ⇒ **对白里提到谁 ≠ 谁在画面里。**
#   说话人**要留**：契约里对白列写作 `角色：台词`，说话人通常就在画面里
#   （老分镜没有 `@` 标记时靠它兜底）。
_SHOT_CHAR_KEYS = ("visual", "sfx", "tail")

#: 「对白」格里的说话人前缀（含情绪括注）：`老陈（拍胸脯，笃定）：…`
_DLG_SPEAKER_RE = re.compile(r"^\s*([^：:（(]{1,12})\s*(?:[（(][^）)]{0,24}[）)])?\s*[：:]")


def _dialogue_speakers(dialogue: str) -> str:
    """从「对白」格里**只取说话人**，丢掉说出来的话（见 `_SHOT_CHAR_KEYS` 的事故记录）。"""
    t = str(dialogue or "")
    if not t or ("：" not in t and ":" not in t):
        return ""
    out: list = []
    for seg in re.split(r"[；;。！？\n]", t):
        m = _DLG_SPEAKER_RE.match(seg)
        if m:
            out.append(m.group(1).strip())
    return " ".join(out)


def _char_text(shot: dict) -> str:
    base = " ".join(str(shot.get(k) or "") for k in _SHOT_CHAR_KEYS)
    spk = _dialogue_speakers(shot.get("dialogue") or "")
    return (base + " " + spk).strip()


def at_mentions(*texts: str) -> list:
    """**粗略**抽出 `@xxx` 串（去重保序）。仅用于没有注册表时的提示/调试。

    注意：中文没有词边界，`@老魏上车` 无法判断该切成 `@老魏` 还是整串，
    所以真正做匹配必须用 `resolve_mentions`（**注册表驱动、长名优先**）。
    """
    out: list = []
    for t in texts:
        for m in _AT.findall(t or ""):
            m = m.strip("、。，,.；;：:")
            if m and m not in out:
                out.append(m)
    return out


def resolve_mentions(text: str, reg: dict) -> tuple:
    """把文本里的 `@资产名` 解析成 `(matched_names, leftover_tokens)`。

    **为什么必须注册表驱动**（2026-09-12）：中文无词边界，正则无法知道
    `@雪夜街道空镜` 里的资产名到哪结束。所以做法是**拿注册表里的名字
    （长名优先）去文本里找 `@名字`** —— 这既是精确匹配，又能正确切词。

    - `matched_names`：文本里 @ 到、且**注册表确有**的资产名
    - `leftover_tokens`：剩下的 `@xxx`（没匹配上任何已知资产）→ 交给调用方
      **如实上报**，不许静默（"有引用无资产"正是过去漏检、到成片才发现的那类问题）
    """
    names = sorted(
        {str(a.get("name") or "") for a in (reg or {}).get("assets", [])
         if a.get("name")}, key=len, reverse=True)
    matched: list = []
    rest = text or ""
    for n in names:
        tok = "@" + n
        if tok in rest:
            matched.append(n)
            rest = rest.replace(tok, " ")      # 抠掉已匹配的，避免重复计数
    return matched, at_mentions(rest)


#: 「本镜出场角色」的识别上限 —— **不是参考图绑定上限**。
#: 2026-09-15 实测事故（village-tractor）：`_chars_by_name` 的默认 `max_n=2` 本是给
#: **参考图绑定**用的成本约束（绑了人物图就 ≤2 张，见 `bind()` 的 A/B 表），却被
#: `hits_for_shot` 拿来当"本镜有谁"的判据 → **三人物镜永远只认出 2 个**
#: （26 镜里 阿凯 几乎全程缺席）→ 连带 ① 身份锚点漏掉他 ② 人数统计少 1。
#: 放宽是安全的：**绑定的限流在 `bind()` 里另有一份**（`cap = min(max_n, 2/3)`），
#: 所以这里放宽**不会多绑图**。
MAX_CAST_PER_SHOT = 5


def _asset_name_spans(text: str, reg: dict) -> list:
    """**已注册的 location / prop 名**在文本里占的区间（用于排除"名字被包含"）。

    ★ 2026-09-15 实测事故（village-bees LN01，真根因）：
      画面描述写 `@阿凯站在@老周家院的青砖院中央` —— **场所名 `老周家院` 里含角色名 `老周`**，
      而 `_chars_by_name` 用的是**裸子串**判据 → 认为"老周也在场" → **老周的照片也被绑进这一镜**
      （人物图上限 2，两张都进，见 `bind()`）→ `<Picture 1>` 落到老周
      → **静帧把主角画成了老周**，而分镜要的是阿凯。
      这类命名（`老周家院` / `阿凯家院`）在乡土题材里极其自然 ——
      **不能指望"名字别这么起"，必须从判据上解决。**
    """
    spans: list = []
    for a in reg.get("assets", []):
        t = str(a.get("type") or "")
        if t not in LOCATION_TYPES and t != "prop":
            continue
        n = str(a.get("name") or "")
        if len(n) < 2:
            continue
        spans.extend((m.start(), m.end()) for m in re.finditer(re.escape(n), text))
    return spans


def _name_hits_outside(text: str, name: str, spans: list) -> bool:
    """角色名在文本里，且**至少有一处不在场所/道具名内部**。"""
    if not name:
        return False
    for m in re.finditer(re.escape(name), text):
        if not any(s <= m.start() and m.end() <= e for s, e in spans):
            return True
    return False


def _chars_by_name(reg: dict, text: str, max_n: int = 2) -> list:
    """只按**角色名本身**在文本中出现来补角色（不用 keywords 泛词）。

    为什么需要（2026-09-13 实测 noodle-night）：
      `hits_for_shot` 的 `@` 精确匹配一旦命中（哪怕命中的是场景/道具），
      就会跳过下面的关键词兜底 —— 于是"没被 @ 的角色"彻底丢失，随后
      `bind()` 的**主角兜底**拿主角去顶替它。实测 12 镜里 4 镜
      （LN02/07/09/11）绑错脸：镜里明明是「女孩」，绑的却是「老陈」的图。

    为什么只认名字、不认 keywords：
      角色 keyword 表含 `他/她/主角` 这类泛词（`cast._ROLE_ALIASES`），
      命中过宽 —— 实测两个角色的表**完全重合**，等于零区分力。

    ★ 2026-09-15 再加一道：**排除"名字被场所/道具名包含"** 的误命中
      （`老周` 命中 `老周家院`）—— 详见 `_asset_name_spans` 的事故记录。
      **判据仍是"名字本身"，只是要求它"独立出现过一次"。**

    排序与 `hits_for_text` **保持一致**（priority 降序、同级保持注册表序）：
    同一件事只该有一种排序规则，否则"补漏路径"与"兜底路径"对同一镜会给出
    不同答案（双人镜取谁）。priority 由资产卡显式给出，可预测、可调。
    """
    spans = _asset_name_spans(text, reg)
    found: list = []
    for a in reg.get("assets", []):
        if a.get("type") != "character":
            continue
        if _name_hits_outside(text, str(a.get("name") or ""), spans):
            found.append(a)
    found.sort(key=lambda a: -int(a.get("priority", 8)))
    return found[:max_n]


def hits_for_shot(reg: dict, shot: dict, max_n: int = 5) -> tuple:
    """按镜匹配参考图。返回 `(hits, unresolved)`。

    **优先 `@资产名` 精确匹配**（注册表驱动，见 `resolve_mentions`）；
    **没有 @ 或一个都没匹配上** → 回退旧的 keywords 子串匹配（兼容旧分镜）。
    最后再补一道**角色补漏**（`_chars_by_name`）——`@` 只命中场景/道具时，
    也要保证本镜的角色有着落，否则会退化成"主角顶替"。
    """
    text = _shot_text(shot)
    by_name = {str(a.get("name") or ""): a for a in reg.get("assets", [])}
    matched, leftover = resolve_mentions(text, reg)
    hits = [by_name[n] for n in matched if n in by_name]
    if not hits:
        hits = hits_for_text(reg, text, max_n=max_n)
        # 走了 keywords 兜底 ≠ "@ 引用已被解决"。若文本里**确实有 @** 却没匹配上
        # 任何已知资产 → 仍要如实上报 unresolved（否则"引用了不存在的资产"会静默溜过）。
        if "@" not in text:
            leftover = []
    # 角色校正（2026-09-13，放在**兜底之后**）：
    #   **文本里明确出现的角色名，优先于关键词匹配出的角色。**
    # 两种失效都必须处理，少一样就出事故：
    #   · `@` 只命中场景/道具时，上面的 `if not hits` 不触发 → 未 @ 的角色静默
    #     丢失，再被 `bind()` 的主角兜底顶替（noodle-night 12 镜里 4 镜绑错脸）。
    #   · keyword 表里含 `他/她/主角` 泛词时（**旧项目数据**不会自动清理），
    #     别的角色会被误命中 —— 实测 LN09 镜里是「女孩」，却因文本含「她」
    #     命中「老陈」，于是绑错脸。按下标名字校正，这类误命中被剔除。
    # 但补漏**不能写在兜底之前**：补出的角色会让 `hits` 变非空，反过来把兜底
    # 整个跳过，同镜的道具/场景全丢（测试当场抓到）。
    named = _chars_by_name(reg, _char_text(shot), max_n=MAX_CAST_PER_SHOT)
    if named:
        hits = named + [h for h in hits if h.get("type") != "character"]
    return hits[:max_n], leftover


def validate_assets(root: Path, shots: list, refs_by_shot: dict) -> list:
    """**cast 之后的资产完整性校验**（2026-09-12 新增），返回问题清单（空=全有着落）。

    为什么必须放在 cast **之后**：资产契约门（`series._assets_gate`）跑在 cast
    **之前**，读到的是空注册表 → 只会误报/漏报；而**全流程没有任何一步**检查
    "分镜点名的资产是否真的拿到了图"。于是"资产卡写了、图没生成"会静默降级成
    文字身份锚点，问题到成片才暴露（返工最贵）。

    检查四件事：① 分镜 @ 了但注册表没有该资产；② 注册表有条目但图不在盘；
    ③ 该镜 @ 了**可绑**资产却一张参考图都没绑上；④ location 类豁免（见下）。
    """
    reg = auto_sync(root)
    by_name = {str(a.get("name") or ""): a for a in reg.get("assets", [])}
    problems: list = []
    unbound: list = []
    for s in shots:
        nm = str(s.get("name") or "")
        matched, leftover = resolve_mentions(_shot_text(s), reg)
        for u in leftover:
            problems.append("%s 引用了 @%s，但注册表没有该资产（缺资产卡或名字不一致）"
                            % (nm, u))
        # 只有"引用了**可绑**资产"的镜才要求绑上。`location` 类**设计上就不绑**
        # （场景图自带机位，会覆盖分镜的景别/机位 —— 见 `bind()` 的 docstring），
        # 所以只 @ 了场景的镜不该算漏绑，否则纯空镜/场景镜会全是误报。
        bindable = [n for n in matched
                    if (by_name.get(n) or {}).get("type") != "location"]
        if bindable and not (refs_by_shot or {}).get(nm):
            unbound.append(nm)
    for a in reg.get("assets", []):
        an = str(a.get("name") or "")
        # location 不绑 → 不要求它在盘（但仍要求注册表有条目，上面已查）
        if an and a.get("type") != "location" and not _safe_ref_urls(a, root):
            problems.append("注册表有「%s」，但参考图不在盘（images/%s.png 缺失）"
                            % (an, an))
    if unbound:
        problems.append("以下镜 @ 引用了可绑资产却没绑到任何参考图：%s"
                        % "、".join(unbound[:8]))
    return problems


def protagonist(root: Path, reg: dict) -> dict | None:
    chars = [a for a in reg.get("assets", []) if a.get("type") == "character"]
    if not chars:
        return None
    chars.sort(key=lambda a: -int(a.get("priority", 8)))
    return chars[0]


#: 场景锚点的长度上限（字符）。
#:
#: ★ 2026-09-15 从 **80 提到 260**（实测事故：village-tree 场景逐镜漂移）。
#:   事故机制：源描述的结构是「**布局正文** + `**光源**：…` + `**色温**：…` + `**陈设**：…`」，
#:   而旧实现是**从头截断到 80 字** → 恰好把后三段**整段砍掉**，注入到提示词的只剩"布局/色块"。
#:   实测 6 个场景（源 302–369 字）**每个都丢掉 ~80%**。
#:   对照同片的人物身份锚点：**722 字、无截断** → 场景与身份的信息投入差 **8 倍**，
#:   用户反馈正是「人物一致性已经可以…就是场景变来变去的」。
#:   260 字仍远小于人物锚点（722 字），不会把提示词撑爆。
SCENE_ANCHOR_MAX = 260
#: 场景描述里属于"参考图空镜"的措辞 —— 注入到**有人物**的镜里会诱导模型画成空店
#: （assetdesigner 是按"空镜头参考图"写的，那是给生图用的，不是给本镜用的）。
_EMPTY_SHOT_WORDS = ("空镜头", "空镜", "空店氛围", "空店", "无人物")
#: **跨镜一致性的载体**：这三段是"同一场戏不漂"的关键，**必须优先保留**。
#: 它们在源描述里以 `**光源**：` / `**色温**：` / `**陈设**：` 形式出现。
_SCENE_KEY_SEGS = ("光源", "色温", "陈设")
_SCENE_SEG_RE = re.compile(r"\*\*\s*(光源|色温|陈设)\s*\*\*\s*[:：]")


def _cut_at_punct(s: str, min_len: int = 12) -> str:
    """在标点处收尾（避免留半句话）；短于 `min_len` 就原样返回。"""
    s = s.strip("，。；、 ")
    if len(s) <= min_len:
        return s
    for ch in ("，", "；", "、", "。"):
        i = s.rfind(ch)
        if i >= min_len:
            return s[:i].strip("，。；、 ")
    return s


def _scene_anchor_text(desc: str) -> str:
    """把场景参考图的描述整理成**可注入本镜**的锚点文本。

    ★ 2026-09-15 改为**按语义段挑选**，不再"从头截断"（事故记录见 `SCENE_ANCHOR_MAX`）：
      源描述 = 「布局正文 + **光源**：… + **色温**：… + **陈设**：…」，而"从头截断"
      恰好丢掉后三段 —— 那正是"这一场戏的光长什么样、陈设是什么"的**唯一出处**。
      新做法：先切出带标签的段，按 **光源 → 色温 → 陈设 → 布局** 的优先级装进预算；
      布局只保开头一段（它是"这场戏长什么样"的概述，不是一致性载体）。
    """
    t = str(desc or "")
    for w in _EMPTY_SHOT_WORDS:
        t = t.replace(w, "")
    t = re.sub(r"[，、]{2,}", "，", t).strip("，。 ")
    if not t:
        return ""
    parts = _SCENE_SEG_RE.split(t)
    head = parts[0].strip("，。 ")
    labeled: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        labeled[parts[i]] = parts[i + 1].strip("，。 ")

    budget = SCENE_ANCHOR_MAX
    segs: list[str] = []
    if head:
        keep = _cut_at_punct(head[: max(40, budget // 3)])
        if keep:
            segs.append(keep)
            budget -= len(keep)
    for k in _SCENE_KEY_SEGS:
        v = labeled.get(k, "")
        if not v or budget <= 24:
            continue
        keep = _cut_at_punct(v[:budget], min_len=20)
        if keep:
            segs.append("%s：%s" % (k, keep))
            budget -= len(keep) + len(k) + 1
    if not segs:   # 源描述没有标签段（旧产物）→ 回落简单截断，行为与历史一致
        return _cut_at_punct(t[:SCENE_ANCHOR_MAX], min_len=20)
    return "，".join(s.strip("，。 ") for s in segs if s).strip("，。 ")


# 场景资产的**类型口径（唯一真相源）**：assetdesigner 契约把场景写成 `scene`，
# 而注册表 / `bind()` / `scene_lines()` 的口径是 `location`。
# `_location_entries` 的**两处读**（注册表 + assets.contract.json）都要用它判断 ——
# 只认 `location` 会让契约里写 `scene` 的场景整条漏掉（那正是"切镜换场景"的一环）。
LOCATION_TYPES = ("location", "scene")


def _location_entries(root: Path) -> list[tuple[str, list[str], str]]:
    """场景资产 → `[(名字, keywords, 描述)]`，**两处都读**。

    实测（paper-crane，2026-09-14）：`assets.json` 里的 location 条目**只有
    `ref_image`，没有描述**（字段就 9 个：id/name/type/keywords/priority/
    public_url/url/ref_image/ref_ver）—— 描述写在 assetdesigner 的结构化契约
    `assetdesigner/assets.contract.json` 的 `prompt` 字段里。
    只读注册表 → 锚点命中 **0/49**（而描述恰恰是锚点的全部价值）。
    → 两处都读，注册表优先（它可能被人工修订过）。
    """
    out: dict[str, dict] = {}

    def _add(nm: str, kws, desc: str) -> None:
        if not nm:
            return
        e = out.setdefault(nm, {"kw": [], "desc": ""})
        for k in kws:
            if k and k not in e["kw"]:
                e["kw"].append(k)
        # 描述取**第一处非空**：注册表优先，契约补缺
        # （实测：注册表的 location 条目没有描述，描述只在契约里 ——
        #  早期版本用 `seen` 去重，同名的契约条目被整条跳过 → 命中 0/49）
        if desc and not e["desc"]:
            e["desc"] = desc

    for a in (load_registry(root).get("assets") or []):
        if str(a.get("type") or "").strip().lower() in LOCATION_TYPES:
            _add(str(a.get("name") or ""),
                 [str(k) for k in (a.get("keywords") or []) if k],
                 str(a.get("prompt") or a.get("appearance") or "").strip())
    c = root / "assetdesigner" / "assets.contract.json"
    if c.exists():
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {}
        for a in (d.get("assets") or []):
            if str(a.get("type") or "").strip().lower() in LOCATION_TYPES:
                _add(str(a.get("name") or ""),
                     [str(k) for k in (a.get("keywords") or []) if k],
                     str(a.get("prompt") or a.get("appearance") or "").strip())

    # ③ `assetdesigner/assets.md`（散文）—— **补前两处拿不到的描述**。
    #
    # 为什么必须有这第三处（2026-09-14 实测，`shortdrama` 包）：
    #   · 注册表的 location 条目**没有描述字段**是 09-14 之前的常态 ——
    #     `cast._register` 直到那天才开始写 `prompt`（此前只写 ref_image）。
    #   · 而 `assets.contract.json` **很多项目根本没产出**（契约要求了，模型漏了）。
    #   → 前两处都拿不到描述 → `if e["desc"]` 把条目全部滤掉 → **锚点 0 命中**，
    #     **尽管 `assets.md` 里明明写着光源/色温/陈设**（它们是锚点的全部价值）。
    #   实测受影响的 9 个项目：clockmaker / noodle-night / snow-taxi / midnight-parcel /
    #   nightshift-45 / lane-erhu / last-bus / echo-6am / warm-milk
    #   （分镜「场景」列都写了，锚点却全是 0）。
    #   `_add` 只做**补缺**（已有描述不覆盖），所以放在最后不会改变前两处的既有行为。
    try:
        from . import cast as _cast          # 函数内导入：`cast` 也反向引用本模块
        ad = root / "assetdesigner" / "assets.md"
        if ad.exists():
            for a in _cast.parse_assets(ad.read_text(encoding="utf-8")):
                if str(a.get("type") or "").strip().lower() in LOCATION_TYPES:
                    _add(str(a.get("name") or ""),
                         [str(k) for k in (a.get("keywords") or []) if k],
                         str(a.get("prompt") or "").strip())
    except Exception:  # noqa: BLE001 -- 散文解析失败不该影响前两处的结果
        pass

    return [(n, e["kw"], e["desc"]) for n, e in out.items() if e["desc"]]


# 画面描述兜底匹配的**最低分**：场景名整串出现记 2 分、每个关键词记 1 分。
# 门槛 2 的意思是「要么写出了场景全名、要么命中≥2 个关键词」——
# 单个通用词（"土黄墙""深灰地面"这类会在多个场景出现的元素）不足以定场景。
_SCENE_MIN_SCORE = 2


def _scene_scores_in_text(entries: list, text: str) -> list:
    """画面描述里各场景资产的匹配强度 → `[(分数, 首次位置, 条目)]`，按强度降序。

    为什么用**打分**而不是"唯一命中"（2026-09-14 实测）：场景卡的关键词里混着
    **通用环境元素**（"土黄墙""暗绿屋顶""深灰地面"），它们会在多个场景的描述里
    同时出现 —— 实测 dawn-broadcast 10 镜里 **7 镜**因"唯一命中"判据被判"歧义"
    而全部跳过。打分后取**最强者**，只有**并列**才算歧义。

    只认**名字或关键词的完整子串**（不做模糊/近义）；`keywords` 里的 name 自身
    不重复计分（名字已按整串单独计）。
    """
    if not text:
        return []
    scored: list = []
    for e in entries:
        name = e[0]
        keys = [k for k in e[1] if k and k != name]
        s = 2 if (name and name in text) else 0
        s += sum(1 for k in keys if k in text)
        if s <= 0:
            continue
        cand = [x for x in [name] + keys if x and x in text]
        scored.append((s, min(text.find(x) for x in cand), e))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def scene_lines(root: Path, shots: list[dict], log=None) -> dict[str, str]:
    """逐镜的**场景锚点文本**：`{镜名: "场景「洗衣店内部」：冷白荧光灯偏青冷色调…"}`。

    为什么用它、而不是分镜的裸场景名（2026-09-14 实测事故，paper-crane）：
        assetdesigner 为每个场景写过描述（光线/色温/陈设）——那正是**能压住逐镜
        漂移的锚点**，但它此前是**死数据**：场景既不进提示词（`build_still_prompt`
        没有这一段），也不参与参考图绑定（场景列无 `@`，keywords 兜底又因
        `hits` 非空而不跑）。结果成片漂到暖光室内，4 张 location 参考图白生成。

    匹配顺序（**取不到就不注入** —— 宁可这一镜没有场景锚点，也不注入一个错的）：
        ① 分镜「场景」列 ↔ 场景资产名（精确相等）
        ② 场景列的 keywords 子串兜底
        ③ **画面描述兜底**：**「场景」列缺失、或写了但匹配不上**时，从本镜画面描述里
           认场景资产 —— 取**匹配最强者**（场景名整串 2 分 + 每个关键词 1 分）；
           **并列最强**才判歧义、跳过；低于 `_SCENE_MIN_SCORE` 不采用（单个通用词
           不足以定场景）。适用两种情形：契约不要求「场景」列（牛来 / 3D 包），
           以及分镜师把**道具名**填进「场景」列（shortdrama 包实测，见 ③ 处注释）。

    为什么要有 ③（2026-09-14 用户报告"切镜就换场景"后定位）：
        牛来包的 scenedesigner 契约历史上只有 8 列、**没有「场景」列**，①② 都无从
        谈起 → 锚点恒为 0。③ 让**存量项目**（分镜已生成、不便重跑创作链）也能
        拿回场景锚点；它也是新契约生效前的过渡路径。

    `log`：给定时，**锚点为零、或存在未命中/歧义时必须出声** —— 这类失败原先
    完全静默（返回 `{}`、上游 `if sl:` 不打任何日志），用户只看到"切镜换场景"。
    """
    def _warn(msg: str) -> None:
        if log is None:
            return
        try:
            log(msg)
        except Exception:  # noqa: BLE001 -- 日志失败不该影响锚点
            pass

    entries = _location_entries(root)
    if not entries:
        _warn("[scene] 注册表无 location 条目 → **场景锚点无法注入**（切镜将换场景）。"
              "查 assetdesigner 是否产出场景卡、其类型是否被识别为 location/scene。")
        return {}

    out: dict[str, str] = {}
    n_miss = n_ambig = 0
    for s in shots:
        name = str(s.get("scene") or "").strip().lstrip("@")
        hit = None
        if hit is None:
            hit = next(((n, k, t) for n, k, t in entries
                        if any(kw and kw in name for kw in k)), None)
        # 场景列缺失、**或写了但匹配不上** → 描述兜底（最后一道）。
        # 后半句是实测补的：分镜师常把**道具名**填进「场景」列（clockmaker 写了
        # "绿罩台灯""钟表工作台"这类非场景资产），此时不该直接放弃 ——
        # 那一镜的画面描述里通常写着真正的场景（"钟表铺中央"），认得出。
        if hit is None:
            sc = _scene_scores_in_text(entries, str(s.get("visual") or ""))
            if sc and sc[0][0] >= _SCENE_MIN_SCORE:
                if len(sc) > 1 and sc[1][0] == sc[0][0]:
                    n_ambig += 1      # 并列最强 → 歧义，宁可无锚点
                else:
                    hit = sc[0][2]
            elif name:
                n_miss += 1           # 有场景列、两种匹配 + 描述兜底都没中
        if hit is None:
            continue
        anchor = _scene_anchor_text(hit[2])
        if not anchor:
            continue
        out[str(s.get("name") or "")] = "场景「%s」：%s" % (hit[0], anchor)

    # 告警：**失败必须可见** —— 这类失败原先完全静默（返回 `{}`、上游 `if sl:` 不打
    # 任何日志），用户只看到"切镜换场景"这个末端症状。"0 镜"时也要带上**具体原因**
    # （歧义 / 名字未匹配），否则排查时只知道"没有锚点"、不知道卡在哪一步。
    detail = ""
    if n_miss:
        detail += "「场景」列未匹配到资产名 %d 镜；" % n_miss
    if n_ambig:
        detail += "画面描述歧义（多个场景并列最强）跳过 %d 镜；" % n_ambig
    if not out:
        _warn("[scene] 场景锚点 **0/%d 镜** → 切镜会换场景。%s"
              "（资产侧有 %d 个 location；分镜既无可用「场景」列、"
              "画面描述里也认不出场景）" % (len(shots), detail, len(entries)))
    elif detail:
        _warn("[scene] 场景锚点 %d/%d 镜；%s" % (len(out), len(shots), detail))
    return out


def _photo_bound_names(root: Path, reg: dict) -> set:
    """有**源照片**（`images/<名>.source.*`）的角色名集合 —— 它们**不需要文字身份锚点**。

    ★ 2026-09-15 实测（用户反馈「差太远了」）：同一张照片、同一镜，
      提示词 **545 字 → 75 字**（只留"以照片为准 + 机位景别 + 画面内容"）之后，
      出图相似度**大幅提升**（发际线/眉/鼻/唇/下颌全对上，且是真实照片质感）。
      545 字里最大的一块正是**每个角色约 220 字的外貌描述**，里面还带着
      「面部棱角清楚、面部线条硬朗」这类**与照片打架**的形容词
      ⇒ **有照片时那段文字是净损失**：身份本来就该由照片承担。

    `SHORTDRAMA_KEEP_IDENTITY_WITH_PHOTO=1` 可恢复注入（照片质量差、想靠文字纠偏时）。
    """
    try:
        from .. import config
        if config.KEEP_IDENTITY_WITH_PHOTO:
            return set()
        import v5.media.cast as _cast
    except Exception:  # noqa: BLE001 -- 判定不了就按"要锚点"处理（保守，不改旧行为）
        return set()
    out: set = set()
    for a in reg.get("assets", []):
        if a.get("type") != "character":
            continue
        n = str(a.get("name") or "")
        if n and _cast._source_photo(root, n):
            out.add(n)
    return out


def identity_lines(root: Path, shots: list[dict], max_n: int = 5) -> dict[str, str]:
    """无参考图时的**文本身份锚点**：{shot_name: "主角固定形象：…"}。

    为什么需要：pack 关闭参考图（如牛来风格，参考图会污染审美）后，身份靠
    提示词里的分镜描述——但分镜常只写"陈默"，模型就自行编（实测服装变灰、
    出现杂牌招牌）。把 assets.json 的 ref_line / identity 压成一句固定锚点，
    逐镜贴在画面内容之后，身份才稳。

    只对有角色出镜的镜头生成（道具/场景不需要身份锚点）。

    注册表为空时**回落角色卡**：nightshift-45 事故里注册表 0 个资产 →
    protagonist() 返回 None → 这里返回 {} → 静帧连文字锚点都没有。
    角色卡（worldbuilder.md）始终存在，把它当最后一道身份防线。

    ★ 2026-09-15：**有源照片的角色跳过锚点**（见 `_photo_bound_names`）——
      照片已经承担身份，再叠 220 字外貌描述只会**把照片稀释掉**（实测证据在那条注释里）。
      **过滤只发生在这里**，不动 `_shot_cast_lines`：`cast_counts`（人数声明）还要照常数人。
    """
    reg, prot, fallback = _cast_ctx(root)
    skip = _photo_bound_names(root, reg)
    out: dict[str, str] = {}
    for s in shots:
        lines = _shot_cast_lines(s, reg, prot, fallback, max_n=max_n)
        if skip:
            lines = [ln for ln in lines
                     if not any(ln.startswith(n + "的固定形象") for n in skip)]
        if lines:
            out[s["name"]] = "；".join(lines)
    return out


def _cast_ctx(root: Path):
    """一次装配「本镜有谁出场」的判定上下文（注册表 + 主角 + 角色卡兜底）。"""
    reg = load_registry(root)
    prot = protagonist(root, reg)
    fallback: list[dict] = [] if prot else _chars_from_worldbuilder(root)
    return reg, prot, fallback


def _shot_cast_lines(shot: dict, reg: dict, prot: dict | None,
                     fallback: list[dict], max_n: int = 5) -> list[str]:
    """本镜的**身份锚点文本**（每人一条）。

    ★ 这是「本镜有谁出场」的**唯一判据** —— `identity_lines`（写锚点）与
      `cast_counts`（给 prompt 报人数）都调它。**绝不各写一份过滤逻辑**。

    匹配规则（原 `identity_lines` 主体，逐字搬来）：
      优先 `@资产名` **精确匹配**（2026-09-12）：分镜契约要求写 @资产名，
      有 @ 就按名字取注册表条目；没有则回退 keywords 子串（兼容旧分镜）。
      本镜没有任何角色命中时**强制带上主角**（否则纯道具/空景镜会没有身份约束）。
      注册表空 → 回落角色卡里的外貌描述（命中姓名/别名才算本镜出场）。
    """
    text = (shot.get("visual") or "") + " " + (shot.get("dialogue") or "")
    hits, _unresolved = hits_for_shot(reg, shot, max_n=max_n)
    if not any(h.get("type") == "character" for h in hits) and prot:
        hits = [prot] + [h for h in hits if h is not prot]
    lines: list[str] = []
    for c in [h for h in hits if h.get("type") == "character"]:
        ln = str(c.get("identity") or c.get("ref_line") or "").strip()
        if not ln:
            ln = "以 %s 为固定形象，全片每镜保持一致" % (c.get("name") or c.get("id"))
        lines.append(ln)
    if not lines and fallback:
        for c in fallback:
            if any(k and k in text for k in (c.get("keywords") or [])):
                lines.append("以 %s 为固定形象，全片每镜保持一致：%s"
                             % (c["name"], c["appearance"][:200]))
    return lines


def cast_counts(root: Path, shots: list[dict], max_n: int = 5) -> dict[str, int]:
    """每镜的**出场角色数**（0 = 空镜/纯道具镜）→ 注入 `prompt` 的 `_cast_n`。

    为什么需要（2026-09-15 实测事故，village-tractor）：
        `prompt` 此前只判「有人 / 没人」，**只要有人就追加
        「画面中只有一个人物」** —— 于是分镜明确要 3 个人的镜（LN17 老周+小林+阿凯）
        也收到「只能有 1 个人」，叠加"画面里要出现某某"的要求后，模型**把同一个人
        复制满画布**同时满足两边：LN17 实测画出 **9 张脸**（老周×4 + 小林×3）、
        LN21 多台拖拉机、LN23 人物重影。
    ⇒ 人数必须与身份锚点同源，故与 `identity_lines` 共用 `_shot_cast_lines`。
    """
    reg, prot, fallback = _cast_ctx(root)
    out: dict[str, int] = {}
    for s in shots:
        n = len(_shot_cast_lines(s, reg, prot, fallback, max_n=max_n))
        # 兜底：正文**显式写了群体人数**却没点姓名时（实测 LN24「三人喘着气站着」
        # 只按姓名认到 1 人）→ 取名册数与量词数的**较大值**，只会抬到事实值。
        out[s["name"]] = max(n, cast_numeral(_char_text(s)))
    return out


def _chars_from_worldbuilder(root: Path) -> list[dict]:
    """注册表为空时的兜底角色表（解析 worldbuilder.md 角色卡）。"""
    p = root / "worldbuilder" / "worldbuilder.md"
    if not p.exists():
        return []
    try:
        from . import cast
        return cast.parse_characters(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def bind(root: Path, shots: list[dict], max_n: int = 5,
         names_out: dict | None = None,
         types_out: dict | None = None) -> dict[str, list[str]]:
    """返回 {shot_name: [public_url, ...]}，供 stills.ensure 的 refs_by_shot 使用。

    **有人物图时参考图总数 ≤ 2**（2026-09-09 A/B 实测，同一分镜同一提示词）：

      | 绑定的参考图                    | 静帧人数 |
      |--------------------------------|---------|
      | 无参考图                        | 1       |
      | 人物图 ×1                       | 1       |
      | 人物 + 1 道具                    | 1       |
      | 人物 + 2 道具（共 3 张）         | **2**   |
      | 3 张纯道具（无人物图）            | 1       |

    规律：**只要绑了人物图，第 3 张参考图就会让模型画出第二个人**——多图输入
    被读成"多主体"。纯道具图不受此限（没有可复制的人形）。
    同脸角色（监控分身）的参考图与主角是同一张，绑两张等于说"该有两个同款人"，
    故人物图只留匹配分最高的一张，且总数封顶 2。

    **location 类一律不绑**（2026-09-09 实测）：场景图自带固定机位，
    会覆盖分镜的景别/机位。LN01 是「店内全景」，只因正文提到"透过玻璃门
    洒入"就绑了「玻璃门入口」（店外视角图）→ 静帧直接画成站在店门外。
    机位由分镜字段决定，场景只该以道具（货架/收银台）形式提供材质参考。
    """
    reg = auto_sync(root)
    prot = protagonist(root, reg)
    # 角色姓名（注册表 + 角色卡）：分镜写"林宇"时人物判据才命中
    names = [str(a.get("name") or "") for a in reg.get("assets", [])
             if a.get("type") == "character"]
    if not names:
        names = [c.get("name") or "" for c in _chars_from_worldbuilder(root)]
    out: dict[str, list[str]] = {}
    for s in shots:
        text = (s.get("visual") or "") + " " + (s.get("dialogue") or "")
        # 优先 `@资产名` **精确匹配**（2026-09-12）：分镜契约要求写 @资产名，
        # 有 @ 就按名字取注册表条目；没有则回退 keywords 子串（兼容旧分镜）。
        hits, _unresolved = hits_for_shot(reg, s, max_n=max_n)
        # 主角兜底：一个角色都没命中 **且本镜确实有人物出场** → 强制绑主角肖像。
        # 空镜/道具镜/纯屏幕镜不兜底——绑了会把物件画成真人（实测 LN02）。
        if (not any(h.get("type") == "character" for h in hits)
                and prot and person_in_text(text, names)):
            hits = [prot] + [h for h in hits if h is not prot]
        # 人物图：**本镜 @ 到几个角色就绑几个**（上限 2），不再"只留一张"（2026-09-13 改）。
        #
        # 旧规则「人物图只留一张」的依据是 2026-09-09 的 A/B：绑第 3 张参考图会让
        # 模型画出第二个人。**但那次实验的前提已经变了** —— 当时还没有 `@角色名`
        # 机制（09-12 才加），模型不知道多出来的那张图是谁，只能瞎画。
        # 官方示例（2026-09-13 核实）**每个镜头都传 2 个角色的图，且正文用
        # `@林小夏-基础形象` / `@李晓雅-基础形象` 明确指代**，同屏两人画得正确。
        #
        # 旧规则的实测代价：双人镜只绑一张 → 第二个角色对模型"不存在"
        # （rainy-door LN02 因此被 clipqc 判「缺少本镜应有的人物：周奶奶」）。
        chars = [h for h in hits if h.get("type") == "character"]
        # **location 类不绑图片**（明确记录，2026-09-14 补：原注释只写"见函数
        # docstring"，而 docstring 里并没有这条 —— 指针是断的）：
        #   场景参考图是**空镜内景**，自带机位与景别。把它喂进"近景 @周平 手部特写"
        #   这类镜，等于同时告诉模型"用一个空荡的全景"，容易把构图拉回大 Wide，
        #   与分镜要求的景别直接打架（`qc.review_shot_type` 会把这类判成景别不符）。
        #   场景的作用改由**文本锚点**承担 —— 见 `assets.scene_lines` 与
        #   `prompt.scene_line`：把 assetdesigner 写的场景描述（光线/色温/陈设）
        #   注入提示词。**信息进来了，机位没进来。**
        others = [h for h in hits
                  if h.get("type") not in ("character", "location")]
        # 总数上限**按本镜角色数分档**（2026-09-13 精细化）：
        #
        #   本镜角色数 0  → 纯道具镜，不受限（旧 A/B：3 张纯道具仍只 1 个人）
        #   本镜角色数 1  → 封顶 2（旧 A/B 实测：1 人物 + 2 道具 = 3 张 → 多画 1 个人）
        #   本镜角色数 2  → 封顶 3（= 2 角色图 + 1 道具图；**场景不参与**，
        #                   见上方 location 说明 —— 原文这里写"2 角色 + 1 场景"
        #                   与代码矛盾，2026-09-14 订正）
        #
        # 真正的规律不是"3 张就出错"，而是「**图的数量超过分镜要求的人数就会多画**」。
        # 单人镜喂 3 张 → 多一个人；双人镜喂 3 张 → 正好两个人。
        #
        # ★★ 2026-09-15 定稿：**人脸优先**（每张脸各绑一张，最多 3 张），余量才给道具。
        #
        #   改动经过（两次纠正，记录下来免得再绕）：
        #   ① 原实现是 `chars[:2] + others` —— **3 人镜只绑 2 张脸 + 1 张道具**，
        #      于是**第三个人永远拿不到参考图**（village-bees 里阿凯全程没被锁脸）。
        #   ② 我一度把上限压到 2（依据：LN02 单样本看起来"主角脸变淡"）——
        #      **该依据不可靠**：同一天 `_probe-3faces-LN03.jpg` 实测 **3 张人脸同喂 →
        #      三个人都对得上、不混脸、不复制**，直接反驳"第 3 张会稀释"。
        #      按"不留没证据的改动"原则**撤回**。
        #   ③ 定稿规则（本条）：
        #        纯道具镜 → 不受限（`max_n`）
        #        1 个人物 → 封顶 2（= 1 脸 + 1 道具；2026-09-09 A/B：再多会多画一个人）
        #        ≥2 个人物 → 封顶 3，且**先满足人脸**（三个人物就绑三张脸）
        n_char = len(chars)
        if not chars:
            cap = max_n
        elif n_char == 1:
            cap = min(max_n, 2)
        else:
            cap = min(max_n, 3)
        picks = (chars + others)[:cap]
        urls = []
        # ★ `names_out`（2026-09-19 新增，**可选**）：把每张参考图**对应哪个资产**记下来，
        #   供静帧提示词**逐张点名角色**用 —— 官方多图合成文档要求
        #   [参考图角色] + [目标场景] + [图间关系]，而"第一张图是谁"只有这里知道。
        #   默认 None ⇒ 行为与历史一字不变（不改任何既有调用点）。
        names: list[str] = []
        for h in picks:
            for u in _safe_ref_urls(h, root):
                if u and u not in urls:
                    urls.append(u)
                    names.append(str(h.get("name") or ""))
        if urls:
            out[s["name"]] = urls
            if names_out is not None:
                names_out[s["name"]] = names
            if types_out is not None:
                # ★ 2026-09-21：与 `names_out` 平行输出每张参考图的**资产类型**
                #   （character/prop/location）。静帧提示词的逐张点名据此分流措辞——
                #   旧实现把**道具**也声明成"人物设定表（头肩像三视图）"（画中人来
                #   ep1 实测：残旧仕女图/白玉平安扣都被说成"角色"，模型困惑）。
                types_out[s["name"]] = [str(h.get("type") or "prop") for h in picks]
    return out
