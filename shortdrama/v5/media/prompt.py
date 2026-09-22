# -*- coding: utf-8 -*-
"""提示词组装器：分镜字段 → 生产模型提示词（六段式骨架）。

对照实测效果好的分镜（截图六张）总结出的骨架：

    {镜头语言}。{视觉风格}。{画面内容}。{镜间承接}。{落幅}。{声音}。{全局约束}

为什么必须组装而不是直接丢 `visual` 原文：
  分镜表解析出了 景别/角度/运镜/对白/音效，但旧实现只用画面描述一列——
  模型不知道"中景还是特写""固定还是推近""这一镜该接在哪、该落在哪"，
  于是镜间跳脱、落幅随机。组装器把这些**已有字段**显式喂给模型。

三个来源：
  1. 分镜表字段（景别/角度/运镜/视觉风格/落幅/对白/音效）
  2. 镜间关系（relations.plan_frames 的 frame_plan）→ 承接句
  3. 全局约束（每镜统一尾缀，不写进正文）

所有段落缺失时优雅回落——旧分镜（无新增列）照样能跑，只是提示词朴素些。
"""
from __future__ import annotations

import re

from .. import config
from . import video_plan

# ─── 全局尾缀（每镜统一，不写进画面描述正文）─────────────────────────────────

# **必须用英文短指令**（实测事故）：中文负面长句（「视频全程不要字幕、不要屏幕
# 文字；不要背景音乐、不要歌唱…」）会被模型当成**可显示内容**，逐字烧到屏幕上。
# nightshift-45 的 42s 帧底部就烧着「滴格林宇 / l'ryoym hmnell」这类乱码字幕。
# 英文短指令不含中文字形，模型无法把它当字幕渲染；这也正是旧架构的定稿做法
# （其注释：a single English short directive replaces the old Chinese long
# directives that the model burned onto the screen as subtitles）。
GLOBAL_VIDEO = (" Text-to-video directive: no on-screen text, no subtitles; "
                "keep a consistent global BGM with necessary ambient sound, "
                "never go silent.")
# 无台词镜专用（2026-09-11《热牛奶》实测）：GLOBAL_VIDEO 的 "never go silent"
# 对**无台词镜**会被模型理解成"必须有人声"——图生视频没有台词剧本，只能即兴
# 配音，于是每个无台词镜都冒出重复、含混的人声（用户实测："每个镜头都在说
# 重复的对白"）。对话极简片（如 4/10 镜有台词）受此伤害最大。
# 无台词镜的正解**不是"明确禁人声"**（2026-09-14 A/B/C 实测推翻，用户逐条听）：
#   尾指令里那串 `no speech, no talking, no voice-over, no spoken words` **一点用都没有**
#   （09-13 A/B 就发现"写与不写模型都安静"——那次结论不可外推），
#   而真正**诱发说话的是标签**（见下面 AMBIENT_LABEL_SILENT 的说明）。
#   所以这里**只留禁字幕**：禁字幕是**文字**约束（en 短句，模型服从），与音频无关。
GLOBAL_VIDEO_AMBIENT = " Text-to-video directive: no on-screen text, no subtitles."
# 环境声标签（2026-09-12 实测事故，clockmaker）。
#
# **有台词的镜绝不能带 "do not speak"**：原实现给**所有**非空音效列都拼
# `Ambient only, do not speak:`，于是有台词的镜同时收到两句互相矛盾的话：
#     Audio: 搁这儿吧   Ambient only, do not speak: 鞋底轻触木地板。
# 模型取解的是后者 —— 噤声、然后**自己即兴配音**，剧本台词一句都没念出来
# （用户实测："所有镜头说的对白都是'大家好，很高兴和大家分享今天的内容'"）。
#
# ★★ 2026-09-14 补：**同一条推理必须延伸到无台词镜**（A/B/C 三变体实测，用户逐条听）。
#   当时只对有台词镜去掉了 `do not speak`，**无台词镜保留了它** —— 而那正是残余缺陷的根因：
#     A 现状  `Ambient only, do not speak: 脚步滑地声。` + `…no speech, no talking…`
#             → 用户实听：**念「大家好 我们开始今天的训练」** ❌
#     B 正向  `Sound: continuous low hum…, faint rustle…, a dog barking far away.`（只禁字幕）
#             → **干净环境音** ✅
#     C 不写  无音频段（只禁字幕）→ **干净环境音** ✅
#   **机制**：`do not speak: X` 里 **"speak" 这个动作词与内容 X 挨在一起**，
#   形式与 `Audio: <台词>` 同构 → **模型把它当成"要念的内容"**；而否定的尾巴被忽略。
#   （旁证：本文档里 `never go silent` 会被理解成"必须出人声" —— **同一条规律：
#    音频通道只读"肯定式的动作 + 内容"，不读否定。**）
#   ⇒ **标签改成 `Sound: `** —— 只说"有什么声音"，**完全不提"说话"这个动作**。
#   而且**保留**了指定环境声的能力（比"整段不写"更好：静默镜本来就该有环境音质感）。
#   模型每次现编的"开场白"都不一样（听过「…很高兴和大家分享今天的内容」与「…我们开始今天的训练」）
#   ⇒ 别指望靠"屏蔽某一句话"解决，**必须从触发条件上下手**。
# 注意**前导空格**：这两个标签都是直接拼在上一个片段后面的（如 `Audio: 台词`
# 之后），漏了空格会粘成一串（实测 `了Background ambience:`）。
AMBIENT_LABEL_SPEAKING = " Background ambience: "
AMBIENT_LABEL_SILENT = " Sound: "


def ambient_label(has_dialogue_: bool) -> str:
    """环境声段的**标签**：有台词 → 只说"背景环境声"，不加禁声指令。"""
    return AMBIENT_LABEL_SPEAKING if has_dialogue_ else AMBIENT_LABEL_SILENT
# 类型包禁忌 BGM 时（如 niulai-movie-style「无背景音乐、无歌唱」）走这条
GLOBAL_VIDEO_NO_BGM = (" Text-to-video directive: no on-screen text, no subtitles, "
                       "no background music, no singing; ambient sound and spoken "
                       "dialogue only, never go silent.")
# 有台词的镜头：禁字幕指令**前置**（实测前置才不被念；尾部会被当台词尾音读出）
VIDEO_NO_SUB_PREFIX = "No on-screen text, no subtitles. "
# 普通话声明：不声明时模型可能念错语言/带口音。
# **不能带中文字形**（2026-09-10 maskparade）：这里原本写 "Mandarin Chinese (普通话)"，
# 于是每一镜有台词的提示词都多出两个汉字——和"老周"一样，只会被烧到画面上。
# 用 "Mandarin Chinese" 纯英文即可，语义无损。
LANG_SUFFIX = (" LANGUAGE: Mandarin Chinese spoken dialogue only — natural "
               "pacing, plain conversational tone, NO singing, NO accent.")

# ─── reference 模式的素材用途声明（2026-09-13）───────────────────────────────
# 官方文档明确要求："在 `reference` 模式中，应在提示词里明确写出素材占位符及其用途，
# 例如『以 <Picture 1> 为角色参考，并跟随 <Audio 1> 的节奏』。这比只上传素材但不
# 解释用途更容易获得可控结果。"
# 我们只传一张图：`<Picture 1>` = **本镜静帧**（见 `video.submit_chain` 的 images 组装）。
# 为什么静帧一张就够：静帧本身就是**带参考图生成**的成品图，角色外观/服装/场景
# 已经锁在它里面了；官方示例需要单独传角色图，是因为它没有静帧阶段。
# 放在提示词**最前面**——官方 reference 示例（"以 <Picture 1> 中的角色和美术风格
# 为参考，角色在花田中自然奔跑…"）就是占位符声明打头。
REF_USAGE_ZH = ("以 <Picture 1> 中的角色、服装与场景为参考，保持外观一致，"
                "并按分镜描述组织镜头与构图。")
REF_USAGE_EN = ("Using <Picture 1> as the reference for the character, costume and "
                "setting, keep their appearance consistent, and organize the shot and "
                "framing as the storyboard describes.")

# 静帧路径的参考图声明（2026-09-15 新增）。
#
# ★ 为什么需要：上面那句 `REF_USAGE_*` **一直只注入在视频提示词里**
#   （`build_video_prompt`），**静帧路径一个字都没有** —— 静帧把参考图交给接口时，
#   模型不知道那张图是什么、该不该以它为准，于是**只按文字走**：
#   实测（village-bees）"面部棱角清楚、线条硬朗"把脸改老改硬、
#   分镜写的 `头戴@草帽` 直接给加上帽子 —— 用户反馈「为什么样子都变了，
#   不是和我给的照片完全一样的」。
#
# ★ 为什么**必须另加一句**而不是直接复用：`REF_USAGE_*` 只说"为参考、保持外观一致"，
#   **没有规定"文字与照片冲突时谁优先"** —— 而那正是跑偏的机制（模型只能自己折中）。
#   本条把优先级写死：**人物与服装以图为准，文字只说明动作**。
#   措辞**纯正向**：不写"不要按文字改写"（本项目铁律：否定式提法会把概念引进提示词）。
STILL_REF_RULE = (REF_USAGE_ZH + " 其中人物、服装与配件**一律以 <Picture 1> 为准**，"
                  "文字只用于说明本镜的动作与画面内容。")


def still_ref_rule(names: list | None = None, types: list | None = None) -> str:
    """静帧的参考图使用规则。给了 `names` ⇒ **逐张点名角色**（官方多图合成结构）。

    为什么要点名（2026-09-19 实测，agnes-image-2.5-flash 官方文档）：
      多图合成的提示词结构是「[参考图角色] + [目标场景] + [图间关系]」，官方示例明写
      "preserves the character identity" —— 角色必须在提示词里**点名**。
      只说"以第一张图为准"时，模型不知道那张图是谁、该保什么；
      实测同一提示词下「点名角色 + 2K 档」的出图，发际线/脸颊/眉形与源照片明显更接近。
    没给 names 时**返回历史常量**（行为一字不变）。

    ★ 2026-09-21 类型分流：`types` 与 `names` 等长（来自 `assets.bind` 的
      `types_out`）。**道具绝不能套用"人物设定表"措辞**——画中人来 ep1 实测：
      残旧仕女图/白玉平安扣被声明成"角色的人设表（头肩像三视图）"，模型拿着
      一幅仕女画被告知"这是一个人"，道具外观彻底漂移（同一幅画 LN01 白描 /
      LN08 水墨山水）。道具措辞改为**定妆照**：形制/颜色/细节以图为准、
      逐次出现保持同一外观。
    """
    if not names:
        return STILL_REF_RULE
    roles = []
    for i, nm in enumerate(names, 1):
        nm = str(nm or "").strip()
        if not nm:
            continue
        typ = ""
        if types is not None and i - 1 < len(types):
            typ = str(types[i - 1] or "").strip().lower()
        if typ == "prop":
            roles.append("第 %d 张参考图=道具「%s」的定妆照——画面中该道具的形制、"
                         "颜色与细节以这张图为准；它在本镜出现时保持同一外观，"
                         "不得另画成别的物件" % (i, nm))
        elif typ == "location":
            roles.append("第 %d 张参考图=场景「%s」的环境参考——只取材质与陈设感觉"
                         % (i, nm))
        else:
            roles.append("第 %d 张参考图=角色「%s」的人物设定表（同一人的头肩像与正/侧/背三视图）"
                         "——以它锁定该角色的长相、发型、体型与服装形制，不得画成另一个人"
                         % (i, nm))
    if not roles:
        return STILL_REF_RULE
    return (REF_USAGE_ZH + " 参考图分工：" + "；".join(roles) + "。"
            "画面内容按下面的文字描述组织：")# 静帧（生图）尾缀：图不需要 BGM 说法，只压风格一致性。
# 注意：这里**不能提"文字/字幕/笔画"**——实测负面提法会诱发模型渲染文字。
# 也**不能提任何物件名词**（板子/布条/纸张/屏幕/墙面）：它们会被模型当成
# "画面里该有的东西"，于是自动补出招牌/贴纸并烧字。实测 LN29（夕阳全景）、
# LN32（U 盘特写）分镜完全没提店铺，仍烧字——载体词只可能来自风格块与尾缀。
#
# ── 2026-09-12 修复：尾缀改为**按 pack 选档**（此前只有一档，且无差别注入所有包）──
# 事故（noodle-night，pack=shortdrama 写实真人短剧）静帧提示词里同时出现两句：
#   「表面渲染：场景里所有表面都是真实材质的自然呈现，不附加任何图形图案。」
#                                                     ← style-block.md 第 3 段
#   「画面里所有表面都只是平涂纯色块或低分辨率重复贴图。」
#                                                     ← 本尾缀
# 两句直接对打 —— 等于**逐镜指令模型把真实材质降级成廉价平涂**。
# 根因：`global_tail` 参数**全仓无人传值**（grep 只在 prompt.py 内部出现），
# 于是这唯一一档"平涂"文本成了所有项目的默认。
# 现在：由 `pack.json` 的 "still-tail" 选档；"平涂色块"只留给反质量/风格化包。
STILL_TAIL_PRESETS = {
    # 写实/材质档：与 shortdrama 的 style-block.md 第 3 段同义（正向重述，不是否定）。
    # ★ 2026-09-17：**删掉原后半句「不附加任何图形图案」**。
    #   那句会连**场景固有**的真实图案一起压掉 —— 金属拉丝、墙纸、大理石纹、
    #   楼层显示、门牌，都是"图案"的形态。它是"画面不能有任何文字"这条
    #   **过度泛化**留下的尾巴（来龙去脉见 `qc.TEXT_RULE_ALLOW` 的历史注释）。
    #   「真实连续的材质」已足够表达"要质感"这件事；
    #   防**烧录字幕**由 `GLOBAL_VIDEO` 的 `no on-screen text, no subtitles` 独立承担。
    "material": "画面表面为真实连续的材质。",
    # 风格化/反质量档（低模、平涂）：表面本来就是纯色块，这句是**正向**描述。
    # 遗留副作用：后半句含"可辨认"（在 `_TEXT_WORDS_25` 里）→ 会被整句删掉，
    # 只剩前半句。对本档无害（"低分辨率重复贴图"已在风格块里说清），故保留原样。
    "flat": ("画面里所有表面都只是平涂纯色块或低分辨率重复贴图，"
             "没有任何可辨认的图形细节。"),
    # 不注入（风格块独自承担）。
    "none": "",
}
# 未注入 `_still_tail` 时的默认档：取 material 而非 flat。
# **风险不对称**：误用 material 最多少一句防烧字声明（风格块 + sign_replacements
# 仍在）；误用 flat 会让写实/3D 画面被要求"表面是平涂色块"，直接崩坏审美。
DEFAULT_STILL_TAIL = "material"


def still_tail(shot: dict) -> str:
    """本镜的静帧尾缀文本（按 pack 档位；未注入 `_still_tail` 时取安全默认）。"""
    kind = shot.get("_still_tail")
    if kind is None:
        kind = DEFAULT_STILL_TAIL
    return STILL_TAIL_PRESETS.get(str(kind).strip().lower(),
                                  STILL_TAIL_PRESETS[DEFAULT_STILL_TAIL])

# ─── per-model 怪癖档案（2026-09-10 隔离）────────────────────────────────────
# 规则**数据**已移入 `model_profile.py`：23 张表、37 处「实测」注释原本全堆在
# 本文件里，全是在补偿某一个具体模型的怪癖。换模型时它们会变成死代码，
# 甚至变成**有害代码**（例：按语言主体清中文，对能读中文的模型就是破坏）。
# 现在：组装器只认 `ModelProfile` 的开关，数据按模型归档。
# 新增模型 = 在 model_profile.PROFILES 加一条，**不必改本文件**。
from . import model_profile as _mp  # noqa: E402

_IMG_PROFILE = _mp.AGNES_IMAGE_25
_VID_PROFILE = _mp.AGNES_VIDEO_25

# 单人声明（正向）：参考图会诱发"画面里多个人"——实测（2026-09-09）
# 参考图含 2x2 拼图或"两个人的资产图"时，静帧画出 3 个穿同样制服的人。
# 除在资产层只绑一张人物图外，提示词层再声明一次单人，双保险。
# 只对**确为单人**的镜加（空镜/道具镜声明"只有一个人"反而会凭空造人）。
#
# ★ 2026-09-15 去掉尾句「不要出现第二个人」：
#   ① 它会被 `_drop_negative_clauses` 整段删掉，只留下悬空的 `画面中只有一个人物， `；
#   ② 按本项目铁律（概念出现即被渲染），负面提法本来就不该写。
SINGLE_PERSON = "画面中只有一个人物，且仅出现一次。"

# 多人声明（**正向**，2026-09-15 village-tractor 事故新增）。
# 事故：此前只判「有人 / 没人」，**只要有人就注 SINGLE_PERSON** —— 于是分镜明确要
#   3 个人的镜（LN17「老周坐驾驶座，小林与阿凯在车托里」）也收到"画面中只有一个人物"，
#   与画面内容要求出现的角色名直接冲突 → 模型**把同一个人复制满画布**同时满足两边：
#   LN17 实测 **9 张脸**（老周×4 + 小林×3）、LN21 多台拖拉机 + 多个老周、LN23 人物重影。
# 措辞纪律：**只能是正向句** —— 含"不要/不得"会被 `_drop_negative_clauses` 整段删掉
#   （同一条"概念出现即被渲染"的规律），所以这里不说"不要复制"，只说"各占一处位置"。
MULTI_PERSON_FMT = "画面中共有 %d 个人物，各自占一处位置，同时完整入画，每个人物只出现一次。"

# 空镜声明（正向）——与 SINGLE_PERSON 对称，但**绝不用否定词**。
# 背景（2026-09-12 实测事故 clockmaker LN01）：分镜 1-1 是「满墙挂钟空镜」，
# 无人物无参考图，但静帧被模型自由发挥画成**女性人物特写**（占满画面）。
# 根因：空镜路径此前**不加任何声明**（只对 _has_person 的镜加 SINGLE_PERSON），
# 模型于是补全了"一个人"。
# 为什么不用"不要出现人物"：同 L83「招牌」的教训——负面约束会把"人"概念
# 引入提示词，反而诱发造人。用正向的场景/静物描述替代。
EMPTY_SCENE = "空镜：画面内容为场景与道具本身，环境静物。"
# 英文主体项目必须用英文版：中文混进英文 prompt 会被图像模型烧成字形
# （见 strip_cjk 的分流逻辑），且会污染其"语言主体"判定。
EMPTY_SCENE_EN = "Empty scene: only the set and props in frame, still life."


def _empty_scene_for(shot: dict) -> str:
    """按本镜正文的语言选空镜声明（中文正文→中文版，英文正文→英文版）。"""
    text = (shot.get("visual") or "") + " " + (shot.get("style") or "")
    return EMPTY_SCENE if re.search(r"[\u4e00-\u9fff]", text) else EMPTY_SCENE_EN

# 招牌类镜头的定向压制。**关键教训（实测 26/32 镜烧字）**：
# 负面约束（"不要出现字形"）本身会在提示词里引入"招牌/文字"概念，
# 模型因此把招牌画出来并必然带字——越压越烧。
# 正确做法是**正向改写**：把招牌类名词换成"纯色装饰板/纯色布条"等纯材质描述，
# 并让"文字/字符/字形"这些词在提示词里彻底消失。
_SIGN_WORDS = _IMG_PROFILE.sign_replacements   # 兼容别名（数据在 model_profile）

# 文字概念词：出现即删掉所在**分句**（不是整句——整句会误伤风格块与尾缀）
# 实测（2026-09-09 nightshift-45 LN02）：分镜写「边缘有模糊英文与数字水印不可读」，
# 模型照着在墙上烧出乱码英文水印——**负面/中性提法都会诱发烧字**，只要概念出现
# 就会渲染。故「英文/水印/数字/字迹/可读」必须一并列入，整分句删掉。
_TEXT_WORDS = _IMG_PROFILE.text_words


def _drop_text_clauses(text: str, profile: "_mp.ModelProfile | None" = None) -> str:
    """删掉含文字概念词的**分句**（保留标点结构，不留碎片）。

    为什么删分句而不是整句：整句会连场景信息一起丢（LN02 的「六格黑白监控
    画面」必须留下，只该丢「边缘有模糊英文与数字水印不可读」）。
    为什么必须删而不是改写成负面约束：实测负面提法同样诱发渲染。

    **门控**：只在 `profile.burns_text` 时生效——能正确渲染文字的模型
    （或不需要压烧字的场景）不该被删掉场景描述。
    """
    prof = profile or _IMG_PROFILE
    if not prof.burns_text:
        return text
    words = prof.text_words or _TEXT_WORDS
    out = []
    for part in re.split(r"([。；，、])", text):
        if part and part not in "。；，、" and any(w in part for w in words):
            continue
        out.append(part)
    t = "".join(out)
    t = re.sub(r"[。；，、]{2,}", lambda m: m.group(0)[0], t)
    t = t.strip("。；，、")
    return t

# 时间性描述 → 分屏。实测：静帧提示词含"随后/逐渐/瞬间/坠落…"时，模型把
# "过程"理解成前后对比，画成上下两格。静帧只该描述**这一帧的样子**。
# 处理方式是**过程→状态**改写，而不是删句——删句会丢场景信息（W2 跑偏成漂浮人脸）。
_TIME_REPL = _IMG_PROFILE.time_replacements
_TIME_REPL_EN = _IMG_PROFILE.time_replacements_en
_VIDEO_SIGN_REPL = _VID_PROFILE.sign_replacements


def sanitize_video_signs(text: str, profile: "_mp.ModelProfile | None" = None) -> str:
    """视频提示词的招牌类改写（只去"牌"字联想，不动运镜与时间描述）。

    **门控**：`profile.burns_text` 关闭时原样返回。
    """
    prof = profile or _VID_PROFILE
    if not prof.burns_text:
        return text
    t = text
    for a, b in (prof.sign_replacements or _VIDEO_SIGN_REPL):
        t = t.replace(a, b)
    return t


# 单一画面声明（**纯正向**），压分屏/拼图。
#
# 2026-09-10 重写（maskparade 事故）：旧版写的是
#   "单一连续画面，单一视角，一整幅完整构图，**不要分屏、不要上下两格、
#     不要拼接、不要拼图、不要多视角排列**。"
# 后半句全是负面提法，违反本项目已反复验证的铁律——**负面提法会在提示词里
# 引入该概念，模型因此把它画出来**（与"越压越烧字"同一条规律）。
# 实测：18 镜里 14 镜被判上下两格分屏，且重画 2 轮仍有 12 镜——提示词里
# 那串"不要分屏/不要上下两格"极可能正是诱发源。
#
# 现在只描述**这是什么**（一张单幅照片），"分屏/格子/拼接/拼图/多视角"
# 这些概念词一个都不出现。
NOSPLIT = "这是一张单幅完整照片，一次曝光成像，取景框内是一个连续的完整空间："

# 视频版反分屏声明（**纯正向**，与 NOSPLIT 同一原则）。
# 2026-09-10 实测：静帧分屏修好后，**视频模型自己又把画面渲染成上下两格**
# （clipqc 判 LN03/LN04/LN05/LN06 全为"水平切成上下两格"）。视频提示词此前
# 完全没有反分屏约束——静帧干净不代表成片干净，同一条教训。
# 措辞用"一个连续镜头"而不是"单幅照片"：视频是动态的，说"照片"会诱发静帧感。
NOSPLIT_VIDEO = ("single continuous shot, one camera, one continuous space in frame, "
                 "one take from start to end. ")


# ─── 中文字形清除（2026-09-10 maskparade 事故）────────────────────────────────
#
# 事故：clipqc 判 LN01「衬衫口袋处有可读中文文字（疑似"老周"）」。查提示词发现
# **每一镜都带着 24-50 个中文字**进视频提示词：
#   camera_line  → 中景 / 平拍机位 / 固定镜头          （18/18 镜都有）
#   content_line → 老周 / 报站器 / 白色瓷面具          （场景词与角色名）
#   audio_line   → 环境声（引擎低频持续轰鸣…）          （18/18 镜都有）
#
# 为什么必须清掉：对文生视频模型而言，提示词里的每个**中文字形**都只有一种
# 表达方式——烧成屏幕文字。它读不出"老周"是一个人名，只会把这两个汉字画在
# 离它最近的载体上（衬衫口袋）。这与本项目"越压越烧字"是同一条规律的另一面：
# 概念既然出现在提示词里，模型就会把它渲染出来。**要它别画字，就别给字。**
#
# 处理分三类，**不能一刀切删**（否则中文项目的景别/运镜信息全丢）：
#   1. 结构性词汇（景别/机位/运镜）→ **译成英文**（信息保留）
#   2. 声音段（环境声/音效）→ **整段剔除**（视频提示词只管画面；
#      声音走 providers 的独立参数，写进画面描述只会被当字幕烧掉）
#   3. 其余中文字（角色名/场景物名）→ **删除字形**，因为英文正文
#      （`Rebuild as primitive folk CGI. Lock: single driver…`）已经
#      把同样的信息说了两遍：结构包的分镜本就是中英对照。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 结构性词汇 → 英文（景别 / 机位 / 运镜 / 承接 / 落幅）。**按长度降序匹配**，
# 避免 "后视镜倒影视角" 被 "后视镜倒影" 截断后留下"视角"。
#
# 设计原则（2026-09-10 修正）：这里**只译不删**——景别、运镜、承接、落幅都是
# 视频模型真正需要的信息，删掉等于丢指令（旧测试 test_camera_and_style_included
# 与 test_join_line_for_continuous 正是在守卫这一点）。真正该删的只有两类：
#   ① 角色名/场景物名等"只对中文读者有意义"的词（英文正文已重复表达）
#   ② 声音段（整段不进画面描述）
_CJK_GLOSSARY = _IMG_PROFILE.cjk_glossary


def _apply_cjk_glossary(text: str, profile: "_mp.ModelProfile | None" = None) -> str:
    """结构性中文词 → 英文（长词优先，避免前缀截断）。受 profile 控制。"""
    prof = profile or _IMG_PROFILE
    if prof.reads_chinese:
        return text          # 能读中文的模型：译了反而丢原文信息
    for a, b in (prof.cjk_glossary or _CJK_GLOSSARY):
        text = text.replace(a, b)
    return text


def _cjk_ratio(text: str) -> float:
    """中文字符占"有意义的字符"的比例（用于判断提示词的语言主体）。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = cjk + latin
    return (cjk / total) if total else 0.0


# 判定为"英文主体"的阈值：中文字占比低于此值时，中文字形视为**污染**，
# 一律清除；高于此值时视为**内容**（中文项目），原样保留。
#
# 为什么需要这个开关（2026-09-10 关键修正）：
#   · maskparade（类型包输出英文分镜）→ 正文 English，中文只是从
#     分镜表格的"景别/机位/声音/角色名"列漏进来的元数据 → 必须清，
#     否则模型把「老周」烧到衬衫上。
#   · 中文原创项目（nightshift-45 等）→ 正文本身就是中文，
#     `纸扎匠坐在柜台后搓着钱盒` 是**唯一的场景信息来源**，清掉等于
#     把整镜内容删空（实测：content_line 全空，视频模型只能自由发挥）。
#   一刀切会毁掉后者，所以按语言主体分流。
_CJK_EN_BODY_MAX = _IMG_PROFILE.cjk_en_body_max


def strip_cjk(text: str, *, keep_glossary: bool = True,
              force: bool | None = None,
              profile: "_mp.ModelProfile | None" = None) -> str:
    """清掉**英文提示词**里混入的中文字形（结构性词汇先译成英文）。

    只对英文主体的文本生效；中文主体的文本原样返回——那是内容，不是污染。
    详见 `_CJK_EN_BODY_MAX` 的说明。

    `force` 可显式指定"按英文主体处理"（True）或"按中文主体保留"（False）。
    **必须用到 force 的场景**：环境声这种**独立短片段**，它自己 100% 是中文，
    按自身比例判定会被当成"内容"而保留——但它其实是英文提示词里的一块污染。
    正确做法是由调用方用**整段提示词**的语言主体决定，再 force 下来。

    **门控**：`profile.reads_chinese=True` 时**整段跳过**。
    这是"补丁堆会变成有害代码"最典型的一处——按语言主体清中文，
    对一个能正确读中文的模型就是纯粹的破坏（实测教训的反面情形）。

    只用于**画面**提示词。台词是 providers 的契约字段，不经此函数。
    """
    prof = profile or _IMG_PROFILE
    if prof.reads_chinese:
        return text
    limit = prof.cjk_en_body_max
    if force is None:
        force = _cjk_ratio(text) < limit
    if not force:
        return text          # 中文主体：内容，保持原样
    t = _apply_cjk_glossary(text, prof) if keep_glossary else text
    # 中文字形连带紧邻的全角标点一起去掉，并修复英文侧的空格
    t = re.sub(r"\s*[\u4e00-\u9fff]+\s*", " ", t)
    t = re.sub(r"（\s*）|\(\s*\)|【\s*】", "", t)
    t = re.sub(r"[，、；：。]{2,}", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ，,;；。")


# 招牌名词 → 纯材质描述（正向改写表）
# 「摊位」不在 _SIGN_WORDS 里（它是必要场景词），但实测它是最强的招牌挂载点：
# 只要提示词出现"摊位/摊位群"，模型就在上面长招牌并烧字。因此改写为纯几何体。
_SIGN_REPL = _IMG_PROFILE.sign_replacements
# 承接句模板（按 frame_plan.relation）。
#
# 2026-09-10：视频路径改为**英文模板**（见 strip_cjk 的说明——中文字形会被
# 视频模型烧成屏幕文字）。中文模板保留给静帧路径与单元测试的历史断言。
_JOIN = {
    "continuous": "承接上一镜的落点，镜头在",
    "match": "承接上一镜的构图锚，镜头在",
    "cut": "",
}
# 英文对应（视频路径用）。语义与 _JOIN 一一对应。
_JOIN_EN = {
    "continuous": "the camera continues from where the previous shot ended, holding ",
    "match": "the camera holds the framing anchor of the previous shot on ",
    "cut": "",
}


# ─── 小工具 ─────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip()).strip("。；;，,")
    # 脚手架占位符不算内容（"待填"/"【…待填】"），否则会污染提示词
    if t in ("待填", "无", "空", "—", "-", "/"):
        return ""
    t = re.sub(r"^【[^】]*待填[^】]*】$", "", t).strip()
    t = t.replace("待填", "").strip("。；;，,、 ")
    return t


def _sentence(s: str) -> str:
    """规范成一句（尾部补句号，去重复标点）。"""
    t = _clean(s)
    if not t:
        return ""
    return t + "。"


def _has_text_intent(shot: dict) -> bool:
    """文字镜：本镜**故意**要出现可读文字（如黑场字幕、任务清单）。

    分镜可显式标 `文字镜=是`；否则从画面描述里识别（字幕/打字机/黑场+文字）。
    """
    if str(shot.get("text_shot") or "").strip() in ("是", "1", "true", "yes", "y"):
        return True
    v = shot.get("visual") or ""
    if "字幕" in v and any(k in v for k in ("黑场", "打字机", "清点", "名单", "片名")):
        return True
    return False


def _join_segs(segs: list[str]) -> str:
    """把各段用「。」连成一句：每段先去掉尾部标点，避免"主体。。全景"。

    风格块文本自带句号（源 md 结尾有「。」），直接 `"。".join` 会叠成双句号；
    模型对"。。"这种畸形标点敏感度低但会轻微影响断句，顺手修掉。
    """
    parts = []
    for s in segs:
        t = _clean(s).strip("。；;，, 、")
        if t:
            parts.append(t)
    return "。".join(parts)


def camera_line(shot: dict) -> str:
    """镜头语言段：景别 + 角度机位 + 运镜。"""
    parts = []
    st = _clean(shot.get("shot_type") or "")
    if st:
        parts.append(st)
    ang = _clean(shot.get("angle") or "")
    if ang:
        parts.append(ang + ("机位" if "机位" not in ang else ""))
    cam = _clean(shot.get("camera") or "")
    if cam and cam not in ("固定", "固定镜头", "锁定"):
        parts.append(cam if cam.endswith("镜头") else cam)
    elif cam:
        parts.append("固定镜头")
    return "，".join(parts)


def style_line(shot: dict) -> str:
    """视觉风格段。分镜有独立列就用；否则从画面描述里抽取风格关键词。"""
    s = _clean(shot.get("visual_style") or "")
    if s:
        return s
    v = shot.get("visual") or ""
    # 抽取"写实电影感…"这类风格前缀（分镜常写在句首）
    m = re.search(r"(写实|实拍|电影感|动画|3D|水墨|赛博|复古)[^。；]{0,40}", v)
    if m:
        return _clean(m.group(0))
    return ""


def scene_line(shot: dict) -> str:
    """**场景锚点段**：分镜的「场景」列必须进提示词（2026-09-14 补的缺口）。

    为什么要单独一段（实测事故，paper-crane）：
        分镜有「场景」列，41/49 镜写着「洗衣店内部」；assetdesigner 也认真登记了
        4 个 location 资产（描述完全正确：「冷白荧光灯偏青冷色调 / 天花板成排筒灯 /
        一侧洗衣机与烘干机阵列…整体空店氛围」）；cast 还花了配额生成 4 张场景参考图。
        **但场景没有进入任何提示词**：
          · `build_still_prompt` 的段列表里没有它；
          · `scene` 唯一被用到的 `join_line()` 只在**非 cut 且有承接**时才生效 ——
            而全 cut 的片根本不走那条路（本片 49 镜全是 cut）。
        于是**场景锚点彻底丢失**，模型只能靠逐镜自由写的「视觉风格」列定光线 ——
        而那列写的是「舱内**暖黄灯圈**为局部光源」「夜戏以**暖黄白炽灯与火光**为主」，
        与场景列的"冷白荧光"直接打架 → 成片中段漂成**暖光室内**（书架/吊灯/木桌），
        4 张 location 参考图沦为**死资产**。

    取值优先级：
        1. `_scene_line` —— 由上游（`pipeline._run_impl`）用**资产注册表里该场景的描述**
           注入。这是信息量最大的锚点（含材质/光线/陈设），也是把那份此前无人读取的
           场景描述接上来的地方。
        2. 分镜「场景」列名（裸名兜底，聊胜于无）。
    """
    s = _clean(shot.get("_scene_line") or "")
    if s:
        return s
    name = _clean(shot.get("scene") or "").lstrip("@")
    return ("场景：%s" % name) if name else ""


def style_block_line(shot: dict) -> str:
    """类型包风格块（style.md / packs/<pack>/style-block.md），注入每镜。

    风格列只描述单镜的光线质感，撑不起类型包的整体审美；风格块是包的
    「视觉命题」，必须逐镜出现，否则模型退回默认审美（写实/精致低模）。
    """
    return (shot.get("_style_block") or "").strip()


# 身份锚点的**源头整备**（2026-09-15 village-tractor 事故）。
#
# 事故：锚点文本里含负面从句（牛来包实测）——
#     `怀里常抱硬壳笔记本（记账本，字迹一律不得画成可辨认字符）。`
#   `sanitize_text` 的两道清洗都会咬它，而且是**咬碎结构**：
#     ① `_drop_negative_clauses` 从「不得」删到下一个「。」→ **连右括号一起吃掉**
#        → 实测括号变成 **5 开 4 闭**；
#     ② `_drop_text_clauses` 再按「字迹」（在 `_TEXT_WORDS` 里）删掉半句；
#     ③ 括号内若含「字样/logo」同理（`（胸前为纯色块、无任何字样或 logo）`）。
#   结果锚点碎成 `…怀里常抱硬壳笔记本（记账本，低清贴图；夹克抓绒质感粗糙…` ——
#   括号不闭合、语义断裂，而锚点正是**无参考图时的唯一身份来源**。
#
# 修法（在源头拆掉 sanitizer 会咬碎的**结构**，不是事后打补丁）：
#   1. 含否定词的**整个括号**先整块删掉 → 之后没有括号可咬；
#   2. 其余负面从句照 `_DO_NOT_RE` 删（与 `_drop_negative_clauses` **同一条规则**，
#      不另写一份判据）；
#   3. 元数据小标题「- 材质：」压平成「，材质上，」（它会把括号内外片段粘在一起）；
#   4. 括号一律降级为逗号 —— 括号内容（材质/补充说明）保留，但**结构依赖彻底消除**，
#      这样 `_drop_text_clauses` 无论删掉括号内哪一段都不会留下孤儿括号。
_ANCHOR_META_RE = re.compile(r"\s*[-—–]?\s*材质\s*[:：]\s*")
_ANCHOR_NEG_PAREN_RE = re.compile(
    r"[（(][^（()）]{0,120}?(?:不得|不要|禁止|避免)[^（()）]{0,120}?[）)]")


def _neg_paren_fix(m: "re.Match") -> str:
    """括号内**只删否定子句**、保留其余内容（比整块删更保信息）。"""
    inner = _DO_NOT_RE.sub(" ", m.group(0).strip("（）()"))
    inner = inner.strip("，,。；;、 ")
    return ("，" + inner + "，") if inner else " "


def _tidy_anchor(text: str) -> str:
    """把身份锚点整备成**不会被 sanitizer 咬碎**的形态。详见上方事故记录。"""
    t = text or ""
    if not t:
        return ""
    # ① 含否定词的括号：只摘掉否定子句、留下「记账本」这类真信息，并**连括号一起去掉**
    #    （括号结构留着就会被下游的 _drop_text_clauses 咬出孤儿）。
    t = _ANCHOR_NEG_PAREN_RE.sub(_neg_paren_fix, t)
    # ② 其余负面从句照 `_DO_NOT_RE` 删（与 `_drop_negative_clauses` 同一条规则）。
    t = _DO_NOT_RE.sub(" ", t)
    # ③ 元数据小标题压平（它会把括号内外的片段粘在一起）。
    t = _ANCHOR_META_RE.sub("，材质上，", t)
    # ④ 括号一律降级为逗号：括号内容保留，但**结构依赖彻底消除** ——
    #    这样 `_drop_text_clauses` 无论删掉括号内哪一段，都不会留下孤儿括号。
    t = t.replace("（", "，").replace("）", "，")
    for a, b in (("。，", "。"), ("；，", "；"), ("，；", "；"), ("，，", "，"),
                 ("，。", "。"), ("。；", "；"), ("；。", "。"), ("  ", " ")):
        t = t.replace(a, b)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"；{2,}", "；", t)
    t = re.sub(r"。{2,}", "。", t)
    return t.strip("，；。 ")


def identity_line(shot: dict) -> str:
    """文本身份锚点：无参考图时替代参考图，锁定角色固定形象。

    pack 关闭参考图（反质量风格）后，身份只能靠文字——分镜常只写"陈默"，
    模型会自行编服装。锚点由 assets.json 的 identity/ref_line 生成。

    出参**先过 `_tidy_anchor`**：锚点来自产物文本、措辞不可控，直接进提示词会被
    `sanitize_text` 咬碎（见 `_tidy_anchor` 的事故记录）。
    """
    return _tidy_anchor(shot.get("_identity_line") or "")


# 人物出场判据：与 `assets.person_in_text` **同一套规则**（避免两处漂移）。
# 资产层用它决定是否兜底绑主角图，提示词层用它决定是否加「单人声明」。
_PERSON_HINTS = ("主角", "男主", "女主", "本人",
                 "店员", "顾客", "男人", "女人", "老人", "孩子")
_SHADOW_WORDS = ("人影", "背影", "倒影", "影子", "剪影")
_SENT_SPLIT = re.compile(r"[。；，、\n]")


def _has_person(shot: dict) -> bool:
    """本镜是否有**真人出场**（空镜/纯屏幕镜返回 False）。

    按分句判定：「人影/背影/倒影」所在分句不算人物出场（否则模型会把影子
    **实体化成真人**——实测 LN02 监控特写被画成两个站着的店员），但同镜其他
    分句写了主角时照常算（LN05 既有林宇又有屏幕人影）。

    角色姓名由 `_names` 字段注入（pipeline 从注册表/角色卡取）。
    """
    if (shot.get("_identity_line") or "").strip():
        return True
    hints = tuple(_PERSON_HINTS) + tuple(shot.get("_names") or [])
    text = (shot.get("visual") or "") + " " + (shot.get("dialogue") or "")
    for sent in _SENT_SPLIT.split(text):
        if not sent or any(w in sent for w in _SHADOW_WORDS):
            continue
        if any(k in sent for k in hints) or "他" in sent or "她" in sent:
            return True
    return False


def person_directive(shot: dict) -> str:
    """本镜的**人物数量声明**：0 → 空镜；1 → 单人；≥2 → 多人。

    ★ 为什么必须按**人数**分流（2026-09-15 实测事故）：见 `MULTI_PERSON_FMT` 的
      事故记录 —— "有人就注单人"会把多人镜推向**主体复制/拼贴**。

    人数来源：`_cast_n`（`assets.cast_counts` 注入；与身份锚点**同源同判据**）。
    `_cast_n` 缺失（老分镜 / 单测 / 非媒体链调用）→ **回落旧行为**（布尔
    `_has_person`），保证既有测试与非媒体链路径行为不变。
    """
    n = shot.get("_cast_n")
    if n is None:
        return SINGLE_PERSON if _has_person(shot) else _empty_scene_for(shot)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return _empty_scene_for(shot)
    if n == 1:
        return SINGLE_PERSON
    return MULTI_PERSON_FMT % n


# ─── 负面禁令（DO NOT / 不要 / 禁止）整段删除 ────────────────────────────────
#
# 2026-09-10 maskparade LN16 事故：分镜写了
#   `DO NOT: smooth mask appearance, clear facial features on mask`
# 结果模型**照着把面具画成了带完整真人五官的脸**（眼睛/鼻子/嘴唇/牙齿）。
#
# 这是本项目第三次撞上同一条规律——**负面提法会在提示词里引入该概念，
# 模型于是把它渲染出来**：
#   ① 写"不要出现文字" → 烧字（越压越烧）
#   ② 写"不要分屏、不要上下两格" → 分屏（18 镜 14 镜）
#   ③ 写"脸上不要有清晰五官" → 长出清晰五官
# 三者的解法一致：**让概念词彻底消失**，只用正向描述说"这是什么"。
# 正向替代品已由 pipeline 的 ANTI_FACELESS_HARD / ANTI_SPLIT_HARD 提供
# （"该载体表面是连续完整的平涂色块…"），负面句没有存在价值。
#
# 注意：只删**整段禁令**，不动"DO NOT"以外的正文。分句以 `.` `;` `。` 切。
#
# 修饰词前缀（2026-09-12 修复）：`绝对不要：卡通…柔光。` 若只从"不要"起删，
# 会留下孤儿「绝对」——实测 noodle-night 静帧提示词里出现残句
# `…真实光学瑕疵。绝对 表面渲染：场景里所有表面都是真实材质…`
# （风格块第 2 段被删、第 3 段留下，中间夹一个悬空的"绝对"）。
# 故把否定词前常见的程度/强调副词一并吃掉。
# 为什么**不**写成 `[\u4e00-\u9fff]{0,4}`：那会误吃正文——`他不要命地冲上去。`
# 会连"他"一起删掉，丢掉画面内容。枚举副词可控且可测。
_DO_NOT_PREFIX = (r"(?:绝对|一定|千万|务必|切记|严格|明确|彻底|完全|永远|从来"
                  r"|索性|干脆)\s*")
_DO_NOT_RE = re.compile(
    r"(?i)(?:" + _DO_NOT_PREFIX + r")?"
    r"(?:\bdo\s+not\b|\bdon't\b|\bavoid\b|不要|不得|禁止|避免)[^.;；。]*[.;；。]?")


def _drop_negative_clauses(text: str, profile: "_mp.ModelProfile | None" = None) -> str:
    """删掉整段负面禁令（DO NOT / 不要 / 不得 / 禁止 / 避免）。

    为什么必须删而不是改写：见 `_DO_NOT_RE` 上方的事故记录——负面提法本身
    就在引入该概念，模型会把概念渲染出来。唯一有效的做法是让概念消失，
    由正向描述（"表面是连续完整的平涂色块…"）取而代之。

    **门控**：只在 `profile.negative_induces` 时生效。
    不受负面提法影响的模型，删掉禁令等于白丢一条约束。
    """
    prof = profile or _IMG_PROFILE
    if not prof.negative_induces:
        return text
    t = _DO_NOT_RE.sub(" ", text)
    t = re.sub(r"\s{2,}", " ", t)
    return t


# ─── 内部交叉引用清除（2026-09-10 maskparade 事故）────────────────────────────
#
# 事故：clipqc 判 LN15 有"$5 霓虹招牌"。查提示词发现分镜写了
#   `orange glowing rectangle (same asset as S5, reused)`
# ——`S5` 是**场景编号**（内部交叉引用），模型把它当字面文字渲染，
# 在 LED 屏上画出了 "S5"（看起来像 `$5`）。LN02 同病（`same asset as S1`）。
#
# 同类泄漏还有 `same seat model` / `reuse` / `see assets.md` 这些
# **资产复用记账**：它们描述的是"怎么复用模型"，对画面没有任何指令价值，
# 却会引入具体物件名词（`mask`/`face`/`seat`），按铁律"提什么长什么"。
# 特别是 `same face model`（LN16）与"面具长出真人脸"直接相关。
#
# 处理：**整段删除**这些记账短语。它们只服务于流水线的资产管理，
# 不该出现在给模型的画面描述里。
#
# 实测（maskparade 18 镜全量扫描）共 8 类形态，逐条覆盖：
#   A `Asset reuse: heavy — one seat model reused 6+ times...`  整个"资产复用"小节
#   B `(same asset as S5, reused)` / `(same asset as \`白色瓷面具\`, ...)`  交叉引用括号
#   C `(reused dark blue-green block)` / `(reused from S1/S5)`   裸过去分词
#   D `(reuse same dark blue-green block texture across all seats)`  裸 reuse 动词
#   E `— same mask model, same seat model`                       破折号引导的 same-X-model 清单
#   F `(dark blue-green, same asset 6+ times)` / `same asset throughout`  反复用
#   G `— see assets.md \`白色瓷面具\``                            资产清单指针
#   H `\`asset_reuse=heavy\``                                    反引号键值对
#
# 关键点：**E 必须整段吃**才能顺带删掉 `same face model`（LN16 面具长出真人脸的
# 直接诱因）。`Asset reuse:` 小节（A）出现在 `Texture:` 之后、`Background:` 之前，
# 所以用"到句号/下一个字段名"做右边界。
_ASSET_BOOKKEEPING_RE = re.compile(
    r"(?i)(?:"
    # A. 整个 Asset reuse 小节：`Asset reuse: heavy — ... .`（到句末或下一字段）
    r"[,;.，；。]?\s*Asset\s+reuse\s*[:=][^.。]{0,160}?(?=\.\s|\.$|$|Background:|背景)"
    # B. same asset as <ref>（场景编号/反引号物件名），含尾随 reused
    r"|\(?\s*same\s+asset\s+as\s+[^)]{0,70}?\)?"
    # F. 裸 same asset (+ 频次/throughout)
    r"|[,;，；]?\s*same\s+asset(?:\s+[A-Za-z0-9+×]+){0,3}(?=[),;，；.]|$)"
    # E. 破折号/逗号引导的 same-X-model 清单
    r"|[,;，；]?\s*(?:[—–-]\s*)?same\s+[a-z\u4e00-\u9fff][a-z\u4e00-\u9fff\- ]{0,24}\s+model"
    r"(?:\s*(?:,|、|\band\b)\s*same\s+[a-z\u4e00-\u9fff][a-z\u4e00-\u9fff\- ]{0,24}\s+model)*"
    r"(?:\s*\([^)]*\))?"
    # D. 裸 reuse 动词短语 `(reuse ... )`
    r"|\(?\s*reuse\s+[^)]{0,70}?\)"
    # C. 裸 reused（含 `(reused from S1/S5)`、`(reused ... , same asset throughout)`）
    r"|\(?\s*reused\b[^)]{0,60}\)"
    # C2. 兜底：任何剩余的裸 reused（如 `Seat: reused dark blue-green block texture.`）
    r"|[.,;:，；：]\s*(?:\w+\s*:\s*)?reused\s+[A-Za-z][^.]{0,60}?(?=\.|,|;|$)"
    # G. see assets.md `...`
    r"|[—–-]?\s*see\s+assets\.md\s*`?[^`)]{0,60}`?\)?"
    # H. `asset_reuse=heavy`
    r"|`?\basset[_\s]reuse\s*[:=]\s*[`\w]+`?"
    r")"
)

# 删除后残留的连接符/空括号（如 "Texture: very_low. , same ..." → 收拾尾部；
# "seat fabric )" → "seat fabric"）
_ASSET_DEBRIS_RE = re.compile(r"\s*[,;，；]\s*(?=[,;，；])|\(\s*\)|\[\s*\]|\s+\)|\s*[,;，；]\s*\)")

# 孤儿场景引用：括号被删后剩下的 `S5` / `S1/S5`（LN15 实测 `rectangle S5, screen...`，
# LN02 实测 `seat fabric S1).`）。这些编号只对流水线有意义，对模型是**字面文字**
# ——已被证明会被画到屏幕上（形似 `$5`）。
# 匹配条件：`S<数字>` 前后是词边界或空白，且**右侧紧跟标点/右括号/空白**。
# 用 `(?<![\w])` 防止误伤 `USB3`/`MP4` 这类正常词里的字符。
_ORPHAN_SCENE_REF_RE = re.compile(r"(?<![\w])S\d+(?:\s*/\s*S\d+)*(?=\s*[),;:.，；：。]|\s|$)")


def drop_asset_bookkeeping(text: str,
                           profile: "_mp.ModelProfile | None" = None) -> str:
    """删掉资产复用记账（`same asset as S5` / `same mask model` / `reused` 等）。

    这些短语是流水线的内部记账，对画面没有指令价值，却会把场景编号
    （`S5`）与物件名词（`mask`/`face`）喂给模型——前者被画成字面文字，
    后者按"提什么长什么"被凭空生成。**整段删除。**
    """
    prof = profile or _IMG_PROFILE
    if not prof.leaks_asset_bookkeeping:
        return text
    t = _ASSET_BOOKKEEPING_RE.sub(" ", text)
    t = _ORPHAN_SCENE_REF_RE.sub(" ", t)
    t = _ASSET_DEBRIS_RE.sub(" ", t)
    t = t.replace(" )", ")")
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"[.。]\s*[,;，；]", ".", t)
    return t.strip(" ,;，；")


def sanitize_text(prompt: str, allow_text: bool = False,
                  profile: "_mp.ModelProfile | None" = None) -> str:
    """反烧字 + 反分屏后处理（正向改写，而非负面禁止）。

    实测教训（26/32 镜烧字 + 多镜上下分屏）：
      1. 负面约束"不要出现文字/字形"**反而诱发**文字——它在提示词里引入了
         文字概念，模型于是把招牌画出来并必然带字。越压越烧。
         解法：源头（风格块/尾缀/画面描述）全部改为**纯材质正向描述**，
         名词层面把招牌类换成"纯色装饰板/纯色布条"。
      2. 静帧含时间性描述（随后/逐渐/坠落/摊平）→ 模型画成上下两格分屏；
         改写成状态（悬停/平铺）并加 NOSPLIT 正向声明即可。
      3. **文字概念词一律删分句**（`_drop_text_clauses`）。曾经的实现只做替换
         不做删除，`_TEXT_WORDS` 成了死代码——于是分镜里的「模糊英文与数字水印」
         原样进提示词，模型照着在墙上烧出乱码英文（LN02 实测）。
         注意：改写/负面化都不行，必须让概念词**彻底消失**。
      4. **负面禁令整段删除**（`_drop_negative_clauses`）。这是同一条规律的
         第三次现形：分镜写 `DO NOT: clear facial features on mask` →
         面具长出完整真人五官（LN16 实测）。写禁令＝把概念写进提示词，
         **概念出现即被渲染**。故 DO NOT / 不要 / 不得 / 禁止 / 避免 一律整段砍掉，
         由正向描述（ANTI_FACELESS_HARD「表面是连续完整的平涂色块…」）替代。

    allow_text=True 时跳过第 3 步（文字镜故意要可读文字，如黑场字幕）。

    **profile**：所有怪癖补偿都由 `ModelProfile` 开关控制（默认 = 图 2.5 档，
    行为与历史完全一致）。换到"乖模型"时传 `model_profile.CAPABLE` 即可让
    本函数整体退化为恒等变换——这就是把补丁堆与核心组装器解耦的意义。
    """
    prof = profile or _IMG_PROFILE
    t = prompt
    if prof.burns_text:
        for a, b in (prof.sign_replacements or _SIGN_REPL):
            t = t.replace(a, b)
    if prof.splits_frames:
        for a, b in (prof.time_replacements or _TIME_REPL):
            t = t.replace(a, b)
        # 英文时间词（大小写不敏感）：类型包分镜可能是英文整段，中文表够不到。
        for a, b in (prof.time_replacements_en or _TIME_REPL_EN):
            t = re.sub(re.escape(a), b, t, flags=re.IGNORECASE)
    # 分镜正文自带的画幅声明（`Ratio: 16:9` / `Aspect: 9:16`）必须清掉。
    # 理由：真实画幅由 config.STILL_RATIO / ASPECT_RATIO 决定并以 API 参数下发，
    # 正文里写死一个**与之不符**的比例会让模型自相矛盾——它可能用"画两格、
    # 每格一个比例"来同时满足（maskparade 实测 18 镜 14 镜上下两格）。
    t = re.sub(r"(?i)\b(?:ratio|aspect(?:_ratio)?)\s*[:：]\s*\d+\s*[:：]\s*\d+\s*[。;；]?",
               " ", t)
    # 负面禁令整段删除（第三次撞上同一条规律，见 _DO_NOT_RE 的事故记录）。
    # 放在 _drop_text_clauses 之前：先砍掉禁令，剩下才是"要画什么"的正向描述。
    t = _drop_negative_clauses(t, prof)
    # 资产复用记账整段删除（`same asset as S5` → 模型把它画成字面 "$5"）。
    t = drop_asset_bookkeeping(t, prof)
    if not allow_text:
        t = _drop_text_clauses(t, prof)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"，\s*，", "，", t)
    t = t.strip("。 ，")
    if not t:
        return t
    # NOSPLIT 是"反分屏"正向声明 —— 只有会分屏的模型才需要它。
    if prof.splits_frames:
        return NOSPLIT + t + "。"
    return t + "。"


# 剧本性状态词 → 可拍摄视觉短语（2026-09-11《回声·6:00》实测）：
# 「她贴墙屏息」「她发现男人」里的「屏息」「发现」是心理/状态词，视频模型拍不出
# "屏息"，转而把它们**渲染成画面字幕**（实测成片 22s 烧「发现」、27s 烧「屏息」）。
# 替换为**可拍摄的视觉动作**：语义保留、字数变长（4 字以上的短语几乎不会被当
# 字幕烧）。只替换不删除——删词破坏语法（"她意识到危险"删掉 → "她危险"）。
_STATE_WORDS = {
    "惊恐地": "瞪大眼睛、脸色发白地",
    "警惕地": "绷紧肩膀环顾四周，",
    "不安地": "手指无意识地攥紧，",
    "屏息": "掩住口鼻不敢出声",
    "怔住": "僵在原地",
    "愣住": "僵在原地",
    "发现": "目光猛地锁向",
    "惊恐": "瞪大眼睛、脸色发白",
    "犹豫": "抬起的手停在半空，",
    "迟疑": "脚步顿了半拍，",
    "松了口气": "肩膀垮了下来",
    "下定决心": "咬紧牙关",
    "惊讶": "瞪大眼睛",
    "慌张": "动作乱了节奏",
}


def _visualize_state_words(text: str) -> str:
    """把剧本性状态词替换为可拍摄的视觉动作（见 _STATE_WORDS 注释）。"""
    for w, vis in _STATE_WORDS.items():
        if w in text:
            text = text.replace(w, vis)
    return text


def content_line(shot: dict) -> str:
    """画面内容段：剥掉风格前缀与全局约束后剩下的动作描述。"""
    v = _clean(shot.get("visual") or "")
    # 去掉【镜N】标记
    v = re.sub(r"^【[^】]*】", "", v).strip()
    # 去掉风格前缀（已在 style_line 里单独给）
    v = re.sub(r"^(写实|实拍|电影感|动画|3D|水墨|赛博|复古)[^。；]{0,40}[。；]?", "", v).strip()
    # 去掉尾部全局约束（含"不得出现…文字"类）
    v = re.sub(r"[。；]?画面(中)?(不得|不要|禁止)[^。]*$", "", v).strip()
    v = re.sub(r"[。；]?画面中不得出现[^。]*$", "", v).strip()
    # 状态词视觉化（防字幕化，见 _STATE_WORDS）——放在清理之后，只处理正文
    v = _visualize_state_words(v)
    v = _clean(v)
    return v


def join_line(shot: dict, plan: dict | None, english: bool = False) -> str:
    """镜间承接段：由 frame_plan 决定，或分镜自己写的「承接…」。

    截图范本：「承接上一镜指尖停在盒边的落点，镜头切入…」——承接要**具体**，
    说清接的是上一镜的什么。因此优先引用上一镜落幅，其次场景，最后泛化。

    english=True（视频路径）：模板与连线词全部用英文——中文字形会被视频模型
    烧成屏幕文字（见 strip_cjk）。分镜自写的 join_note 若本身是中文，交由
    strip_cjk 译/删。
    """
    own = _clean(shot.get("join_note") or "")
    if own:
        return own
    fp = (plan or {}).get("frame_plan") or {}
    rel = fp.get("relation") or "cut"
    prefix = (_JOIN_EN if english else _JOIN).get(rel, "")
    if not prefix:
        return ""
    anchor = _clean((plan or {}).get("prev_tail_note") or "")
    if not anchor:
        anchor = _clean(shot.get("scene") or "")
    if not anchor:
        heading = shot.get("heading") or ""
        parts = [p.strip() for p in re.split(r"[｜|]", heading) if p.strip()]
        parts = [p for p in parts
                 if not re.match(r"^第\s*[一二三四五六七八九十\d]+\s*幕", p)
                 and not re.fullmatch(r"S\d+.*", p)
                 and not re.match(r"^#+", p)]
        anchor = parts[0] if parts else ""
    if english:
        return prefix + (anchor if anchor else "the same space") + " in view"
    return prefix + (anchor if anchor else "画面") + "继续"


def tail_line(shot: dict, english: bool = False) -> str:
    """落幅段：本镜收在什么画面。分镜没写就从画面描述尾部推。

    english=True（视频路径）：套壳前缀用英文。
    """
    t = _clean(shot.get("tail") or "")
    if not t:
        return ""
    # 分镜**已自带落幅表述**时整段原样用，不再套壳。两类都要认：
    #   ① 带主语：「镜头缓缓推向…」「画面定格在…」「落幅停在…」「摄影机…」
    #   ② 裸动词：「落在…」「停在…」「定格在…」「收在…」「定在…」
    # 实测事故（noodle-night LN02）：落幅原文「落在女孩停在门外的侧脸与微张的
    # 嘴唇」。旧实现只认 `^(镜头|画面|摄影机)` 开头 + 一条 `^停在` 特例，
    # 不认识"落在" → 原样拼上前缀，产出
    #     「镜头最终停在落在女孩停在门外的侧脸与微张的嘴唇」
    # 三个"停/落"叠在一起。裸动词开头一律按"自带落幅"处理，覆盖才完整。
    if re.match(r"^(镜头|画面|摄影机|落幅|落在|停在|收在|定在|定格)", t):
        return t
    t = re.sub(r"^(落幅|镜头)(最终)?(停|收)(在)?", "", t).strip("，,。 ")
    t = re.sub(r"^停在", "", t).strip("，,。 ")   # 裸"停在"开头同样去掉
    if not t:
        return ""
    return ("the shot settles on " + t) if english else ("镜头最终停在" + t)


_NO_DIALOGUE = ("（无声）", "(无声)", "无声", "（无声，环境音）", "（无对白）",
                "无对白", "none", "-", "—", "无")

# 对白清洗：分镜/剧本里常见的非口语成分，进 Audio 之前必须剥掉。
# 实测事故（nightshift-45）：台词裸拼进 prompt 正文 → 模型既当旁白念、又当字幕烧。
# 旧架构的定稿做法是抽成 `Audio: <纯台词>` 并清洗，这里照搬。
_SPEAKER = re.compile(r"^\s*[\*]*[^：:]{1,12}[\*]*\s*[：:]\s*")      # 「林宇：」/「**林宇**：」
_PAREN = re.compile(r"[（(][^）)]{0,40}[）)]")                        # 「（轻声，冷笑）」
_MARK = re.compile(r"[\*_`#]+")                                       # markdown 标记


def clean_dialogue(cell: str, limit: int = 70) -> str:
    """对白列 → 可直接进 `Audio:` 的纯台词。

    剥掉：语气标注（导演提示，不是台词）、角色名前缀、markdown 加粗。
    裁剪：单镜台词 ≤limit 字（实测超长台词口型对不上、且易被当旁白念）。

    **顺序很重要**：必须先剥括号再剥说话人。实测「监控分身（用他自己的声音，
    平静）：下班了。」——括号让前缀超过 12 字，先剥说话人会漏掉整个「监控分身」，
    结果把说话人当台词念出来。
    """
    t = (cell or "").strip()
    if not t:
        return ""
    if t in _NO_DIALOGUE:
        return ""
    t = _MARK.sub("", t)
    t = _PAREN.sub("", t)
    t = _SPEAKER.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" 。；;，,")
    # **剥完括号要再判一次空台词**（2026-09-10 maskparade LN17/LN18 实测）：
    # 分镜写 `无（只有报站器最后一次极轻的提示音）`——开头判 `无` 时还没剥括号，
    # 匹配不上；剥完剩下 `无` 却不再判 → 模型把"无"这个字念出来。
    if not t or t in _NO_DIALOGUE:
        return ""
    if len(t) > limit:
        t = t[:limit].rstrip("，。、；： ")
    return t


def audio_parts(shot: dict) -> tuple[str, str]:
    """(台词, 环境声) 两段分开返回——两者清洗策略不同：

      · 台词：必须保留中文字形（TTS 要念），且走 `Audio:` 契约字段；
      · 环境声：在**英文主体**提示词里要清掉中文字形（只留英文标签），
        在中文主体里保留原文。

    合并成一个字符串会让 `strip_cjk` 分不清哪段该留、哪段该删——
    台词会被一起清掉，模型就无话可说了。
    """
    d = clean_dialogue(shot.get("dialogue") or "")
    s = clean_dialogue(shot.get("sfx") or "", limit=60)   # 同法清洗（剥括号/角色名）
    return d, s


def audio_line(shot: dict) -> str:
    """声音段：`Audio: <纯台词>` + 环境声。

    **不要**把台词当普通描述写进正文——那是"对白乱念 + 烧字幕"的直接原因。
    `Audio:` 是音画同步模型的契约字段：模型读它做配音与口型，不当屏幕文字。

    2026-09-10（maskparade）：`sfx` 列有两个坑，都实测过：
      1. 它常写**导演提示**而非音效本身，如 `老周台词（疲惫，朝后喊）`——
         括号被剥掉后剩下 `老周台词`，没有冒号，`_SPEAKER` 匹配不到，于是
         模型把它**当台词念出来**，还把 `老周` 两个汉字烧到画面上。
      2. 它整段是中文，在英文提示词里只是待烧/待念的字形。
    处理：剥掉导演提示/角色名前缀，再用英文标签 `Ambient only, do not speak:`
    包住，明确它不是台词。中文字形去留给调用方按语言主体决定（见 audio_parts）。
    """
    d, s = audio_parts(shot)
    parts = []
    if d:
        parts.append("Audio: %s" % d)
    if s:
        # **有台词就不带 "do not speak"**（否则与 Audio: 台词自相矛盾，见
        # ambient_label 的实测说明）
        parts.append(ambient_label(bool(d)) + s)
    return " ".join(parts)


def has_dialogue(shot: dict) -> bool:
    """本镜是否真的要说台词（决定禁字幕指令是否前置）。"""
    return bool(clean_dialogue(shot.get("dialogue") or ""))


# ─── 组装 ───────────────────────────────────────────────────────────────────

def build_still_prompt(shot: dict, plan: dict | None = None,
                       global_tail: str | None = None,
                       profile: "_mp.ModelProfile | None" = None) -> str:
    """静帧（生图）提示词：构图 + 风格 + 内容 + 身份锚点。

    静帧是生产首帧，构图要一次说清（景别/机位/运镜起点），
    但**不带声音**（图没有声音）、**不带承接**（承接是视频阶段的事，
    连续镜的 first_frame 由上一镜尾帧提供）、**不带落幅**。

    落幅为什么必须去掉（实测）：落幅句是「镜头最终停在…」，
    "最终"会让图像模型理解成"结尾画面"→ 画出上下两格（首帧+尾帧）。
    实测 LN19：带落幅必分屏，去掉落幅即单幅。落幅只用于视频阶段。

    2026-09-10：与视频路径一样做**中文字形清除**（strip_cjk）——类型包的
    分镜是英文正文，中文只是从"景别/机位/场景物名"列漏进来的元数据。
    实测 LN05 的 `报站器`（中文）在成图里表现为橙色 LED 屏上的方块字；
    LN16 的 `脸部` 也与"面具长出真人五官"相关。**要它别画字，就别给字。**
    中文主体项目不受影响（strip_cjk 按语言主体分流）。
    """
    prof = profile or _mp.resolve(config.MODELS.get("image", ""), kind="image")
    # 段序：**保持既有段的相对次序不动**（官方实例的次序是实测换来的，见
    # `fix(prompt): 段序改回官方真实分镜实例的顺序`），场景锚点插在
    # 「机位」之后、「视觉风格」之前 —— 空间与取景连在一起读最自然。
    segs = [style_block_line(shot), camera_line(shot), scene_line(shot),
            style_line(shot), content_line(shot), identity_line(shot)]
    # 人物数量声明：0 → 空镜 / 1 → 单人 / ≥2 → 多人（2026-09-15 按人数分流）。
    # 修的是"有人就注单人"——那会把多人镜推向主体复制/拼贴（LN17 出现 9 张脸）。
    segs.append(person_directive(shot))
    head = _join_segs(segs)
    # 中文字形清除：放在 sanitize_text **之前**——NOSPLIT 是中文（刻意如此，
    # 它压分屏有效），若在之后清会把 NOSPLIT 一起清掉。
    head = strip_cjk(head, profile=prof)
    tail = global_tail if global_tail is not None else still_tail(shot)
    text_shot = _has_text_intent(shot)
    if text_shot:
        tail = ""   # 文字镜不压文字
    out = _sentence(head)
    if tail:
        out += tail
    # 反烧字 + 反分屏：正向改写（详见 sanitize_text 的实测记录）。
    # 放在最外层，确保所有来源（分镜字段/风格块/尾缀）都被清洗。
    return sanitize_text(out, allow_text=text_shot, profile=prof)


def tail_content(shot: dict) -> str:
    """落幅的**裸画面内容**（剥掉运镜套壳），供预生成尾帧图使用。

    与 `tail_line` 的取向**相反**：
      · `tail_line` 是给视频模型的"承接指令"，**必须保留原文**——以「镜头」开头
        的落幅若再套壳，会拼成「镜头最终停在镜头缓缓推向…」（LN08 实测）。
      · 这里是给生图模型的"这一帧长什么样"，运镜描述（镜头/缓缓/推向/定格在）
        对静帧没有意义，**要剥干净**，只留下画面主体。
    """
    t = _clean(shot.get("tail") or "")
    if not t:
        return ""
    t = re.sub(r"^(落幅|镜头|画面|摄影机)\s*", "", t).strip("，,。 ")
    t = re.sub(r"^(最终|最后|缓缓|慢慢|逐渐|慢慢)\s*", "", t).strip("，,。 ")
    t = re.sub(r"^(停|收)(在|到)\s*", "", t).strip("，,。 ")
    t = re.sub(r"^(推向|推进|定格在|移向|摇向|切到)\s*", "", t).strip("，,。 ")
    return t


def build_tail_prompt(shot: dict, plan: dict | None = None,
                      global_tail: str | None = None,
                      profile: "_mp.ModelProfile | None" = None) -> str:
    """**尾帧图**（落幅帧）提示词：以本镜落幅为主体的单帧画面。

    为什么需要它（2026-09-10，解锁视频并行的关键）：
        `submit_chain` 用**上一镜渲出来的真实尾帧**当下一镜 first_frame，
        于是连续镜必须等前一镜渲完 → 整条链串行。
        bootleg99-full 32 镜里 25 镜落在链上，86 分钟就是这么来的。
        若在**静帧阶段**就把"落幅"预生成成图，视频阶段就能回到平铺提交
        （first=上一镜的落幅图，last=本镜静帧），串行依赖消失。

    **取舍（必须知道）**：预生成的落幅图 ≠ 上一镜实际渲出的尾帧
    （视频模型的运动是随机的），两者必有偏差，承接从"事实"降级为"预期"。
    故这是**速度换承接质量**的开关，默认关闭（`SHORTDRAMA_TAIL_PREGEN`）。

    与 `build_still_prompt` 的差别：静帧路径**刻意去掉落幅**（带落幅必分屏），
    而这里**以落幅为主体**——正相反。
    """
    prof = profile or _mp.resolve(config.MODELS.get("image", ""), kind="image")
    core = tail_content(shot)
    if not core:
        # 分镜没写落幅：退化成用本镜静帧描述（等价于"停在开场构图"）
        core = content_line(shot)
    segs = [style_block_line(shot), camera_line(shot), style_line(shot),
            core, identity_line(shot)]
    # 人物数量声明：0 → 空镜 / 1 → 单人 / ≥2 → 多人（2026-09-15 按人数分流）。
    # 修的是"有人就注单人"——那会把多人镜推向主体复制/拼贴（LN17 出现 9 张脸）。
    segs.append(person_directive(shot))
    head = strip_cjk(_join_segs(segs), profile=prof)
    tail = global_tail if global_tail is not None else still_tail(shot)
    out = _sentence(head)
    if tail:
        out = out + tail
    return sanitize_text(out, allow_text=_has_text_intent(shot), profile=prof)


def build_video_prompt(shot: dict, plan: dict | None = None,
                       global_tail: str | None = None,
                       profile: "_mp.ModelProfile | None" = None,
                       mode: str | None = None) -> str:
    """视频（图生视频）提示词：六段式骨架。

    镜头语言。视觉风格。画面内容。镜间承接。落幅。声音。全局约束。

    两条硬规矩（都是实测事故换来的）：
    1. **台词只走 `Audio:`**，绝不写进正文——写进正文会被当旁白念、并烧成字幕。
    2. **有台词时禁字幕指令前置**（`VIDEO_NO_SUB_PREFIX`）——尾部会被当台词尾音读出；
       前置的英文短句既不被念、又能压掉烧字。

    **mode**（2026-09-13）：缺省读 `config.VIDEO_MODE`。
      · `reference` —— 最前面加 `<Picture 1>` **用途声明**（官方要求 reference 模式
        必须写清占位符用途，见 `REF_USAGE_*` 的注释）。
      · `keyframe` —— 不加（该模式下没有 `images`，写占位符是无源之水）。
    """
    # 「模式」的唯一决策点（见 media/video_plan.py）。提示词层只关心其中一件事：
    # 要不要在开头写 `<Picture 1>` 的用途声明（reference 模式要求，见 REF_USAGE_*）。
    vp = video_plan.VideoPlan.of(mode)
    # 先拼**不含承接/落幅**的画面正文，用它判定语言主体——
    # 承接/落幅的套壳措辞要跟着正文语言走（中文项目别混英文外壳）。
    body_for_lang = _join_segs([
        style_block_line(shot), camera_line(shot), scene_line(shot),
        style_line(shot), content_line(shot), identity_line(shot),
    ])
    prof = profile or _mp.resolve(config.MODELS.get("video", ""), kind="video")
    english_body = _cjk_ratio(body_for_lang) < prof.cjk_en_body_max
    # 段序以**官方真实分镜实例**为准（7 张实例截图，见
    # `../docs-archive-20260918/official-shot-prompt-pattern.md`）：
    #    景别机位 → 画面风格 → 承接 → 内容与运镜 → 落幅
    # 实例是「真实的生成案例、效果被用户认可」，且与我们长期运行的顺序一致。
    #
    # ⚠ 2026-09-12 我曾仅凭官方文档「提示词建议」那一节（它建议
    # ①主体与场景 ②动作 ③镜头语言 ④视觉风格…）把顺序改成"内容打头"——
    # **被实例推翻**：实例是"景别机位打头、内容排在风格之后"。两个都来自官方，
    # 但文档的分节建议 ≠ 官方产品的真实产出。以实例为准，故此处为
    # `camera_line` 打头（旧实现是 `style_block_line` 打头，风格块让位到其后）。
    # LEAN（`config.LEAN_PROMPT`，2026-09-13）：对齐官方示例的静默镜写法。
    # 删掉三样**指令层**，其余（台词 / 环境声 / 禁字幕 / 字形清除）**一律保留**：
    #   · 类型包风格块（209 字）—— 官方没有，只用本镜风格句
    #   · reference `<Picture 1>` 用途声明 —— 官方示例也没有（它靠 `@角色` 引用）
    #   · 反分屏前置 —— 官方没有
    # **禁字幕不删**：有 4 次真实烧字事故（maskparade / nightshift-45 …），
    # 省 90 字换整片带字，不划算。
    lean = config.LEAN_PROMPT
    segs = [camera_line(shot)]         # 景别/角度机位/运镜——实例每镜都以它打头
    # 场景锚点：紧跟机位（空间与取景连读），**LEAN 也保留** ——
    # 它是信息不是指令层，且正是 LEAN 之前最缺的那块（见 `scene_line` 的实测记录）。
    segs.append(scene_line(shot))
    if not lean:
        segs.append(style_block_line(shot))   # 类型包视觉命题（LEAN 删）
    segs += [
        style_line(shot),          # 本镜光线/色调质感
        join_line(shot, plan, english=english_body),   # 承接上一镜落点（实例在内容之前）
        content_line(shot),        # 内容与动作节拍
        identity_line(shot),       # 角色外观锚点（实例里以 @角色资产 内嵌在内容中）
        tail_line(shot, english=english_body),         # 落幅
    ]
    if vp.announce_picture and not lean:
        # 素材用途声明**打头**（官方 reference 示例就是这个位置）。
        # 语言跟随提示词主体：英文主体的声明用英文版，否则会被 strip_cjk 清成空。
        segs.insert(0, REF_USAGE_EN if english_body else REF_USAGE_ZH)
    # 中文字形清除（2026-09-10 maskparade 事故：衬衫被烧上「老周」二字）。
    # 只对**英文主体**生效；中文正文原样保留（见 strip_cjk / _CJK_EN_BODY_MAX）。
    #
    # ⚠ 2026-09-13 修复：这里**漏了 `_drop_negative_clauses`**。
    # 静帧路径（`build_still_prompt`）一直调它，视频路径没调 —— 后果是
    # 类型包风格块里的「绝对不要：卡通、插画、3D动画、CG渲染…电影级棚拍柔光。」
    # 只出现在**视频**提示词里（实测 morning-stall LN01 的提示词里整串都在）。
    # 这违反了本项目"负面提法会引入该概念"的铁律（4 次事故），也和静帧行为不一致。
    # 顺序：先删禁令（整段），再清字形/反分屏 —— 与静帧路径保持一致。
    head = strip_cjk(
        sanitize_video_signs(_drop_negative_clauses(_join_segs(segs), prof),
                             profile=prof),
        profile=prof)
    # 反分屏前置（英文短句，与禁字幕前缀同一位置逻辑：前置比尾部更容易被遵守，
    # 且不会被当成台词尾音）。见 NOSPLIT_VIDEO 的实测说明。
    # LEAN 下不注入 —— 官方示例没有这条（若出现分屏，说明这条不能省）。
    if prof.splits_frames and not lean:
        head = NOSPLIT_VIDEO + head
    # 声音段挂末尾。两段策略不同，**必须分开处理**：
    #   · 台词 `Audio: …` 保留中文字形（TTS 要念，清掉就没内容了）；
    #   · 环境声描述只对英文主体清字形（中文主体保留原文）。
    #
    # **LEAN 下不注入环境声段**（2026-09-13）：那段的标签 `Ambient only, do not speak:`
    # 本身就是**无效的音频否定词**（A/B 实测：写与不写模型都安静）。
    # 官方示例的静默镜**一句音频都不写** —— LEAN 对齐它。
    d, amb = audio_parts(shot)
    if d:
        head = head + " Audio: " + d
    if amb and not lean:
        # 环境声自身是 100% 中文的短片段，必须用**提示词主体**的语言判定（force），
        # 否则它会被误判成"内容"而保留下来（实测踩过）。
        amb_clean = strip_cjk(amb, keep_glossary=False, force=english_body,
                              profile=prof)
        if amb_clean:
            # 有台词 → 只当背景环境声；无台词 → 明确禁人声（见 ambient_label）
            head = head + ambient_label(bool(d)) + amb_clean
    if lean:
        # LEAN：**只保留一处禁字幕**（前置）—— 尾部那串 `Text-to-video directive:`
        # 与它重复，且带 `no speech/talking/voice-over/spoken words` 这批无效否定词。
        out = _sentence(head)
        if has_dialogue(shot) and not _has_text_intent(shot):
            out = VIDEO_NO_SUB_PREFIX + out
        return out
    if global_tail is not None:
        tail = global_tail
    elif has_dialogue(shot):
        # 有台词镜：维持原行为——模型需要念 Audio: 剧本，不许静默是对的
        tail = GLOBAL_VIDEO if config.VIDEO_BGM else GLOBAL_VIDEO_NO_BGM
    else:
        # 无台词镜（2026-09-11 分路）："never go silent" 会被理解成"必须出人声"，
        # 模型即兴配音 → 每镜都有重复含混的人声。明确禁声，只留环境音/BGM。
        tail = GLOBAL_VIDEO_AMBIENT
    if _has_text_intent(shot):
        # 文字镜：保留必要文字，只压"多余字幕"（英文短句，避免被当字幕渲染）
        tail = (" On-screen text only where the scene itself requires it; "
                "no extra captions or explanatory text."
                + ("" if config.VIDEO_BGM else " No background music."))
    out = _sentence(head)
    if tail:
        out += tail
    # 有台词的镜头：普通话声明 + 前置禁字幕
    if has_dialogue(shot) and not _has_text_intent(shot):
        out = VIDEO_NO_SUB_PREFIX + out + LANG_SUFFIX
    return out


_SAME = ("同上", "同前", "同一", "如上", "同上。")


def resolve_styles(shots: list[dict]) -> list[dict]:
    """把「同上」类风格占位替换为上一镜的实际风格（返回新列表，不改输入）。"""
    out = []
    last = ""
    for s in shots:
        st = _clean(s.get("visual_style") or "")
        if st in _SAME:
            s = {**s, "visual_style": last}
        elif st:
            last = st
        out.append(s)
    return out


def build_all(shots: list[dict], planned: list[dict] | None = None) -> list[dict]:
    """为每镜产出 {name, still, video}。planned 缺省时按 cut 处理。"""
    pl = planned or [{} for _ in shots]
    shots = resolve_styles(shots)
    return [{
        "name": s.get("name"),
        "still": build_still_prompt(s, p),
        "video": build_video_prompt(s, p),
    } for s, p in zip(shots, pl)]


# ─── pack 档：打包 prompt（2026-09-22，自 scripts/pack_render.py 搬入）────────
#
# 12s 打包法（三项目 37 次提交零拒绝 + 捕梦师 213s 成片闭环）的 prompt 骨架：
# 参考图逐拍点名 + 时间段边界（±1s 弹性）+ 逐拍完整内容（场景/镜头语言/画面/
# 台词/音效/落幅）。与单镜六段式（build_video_prompt）的区别：
#   · `<Picture i>` 不是"素材分工"而是"节拍锚点" —— 第 i 张图 = 第 i 拍的
#     画面参考，模型按时间边界自然衔接；
#   · 全局块声明"这是同一条连续素材"—— 人物/服装/道具跨节拍完全一致，
#     光线色调随场景自然过渡（这是逐镜 reference 做不到的：它不知道相邻镜存在）。
# 旁路脚本 pack_render.py 保留为独立验证入口；prompt 文本两处必须同步。

def _pack_fmt_dialogue(d: str) -> str:
    d = (d or "").strip()
    if not d or "无声" in d:
        return "无台词（环境音）"
    return d


def build_pack_prompt(group: list[dict], declared: list[int], total: int) -> str:
    """打包 prompt：参考图逐拍点名 + 时间段边界 + 逐拍完整内容。

    `group` = 同场景相邻镜列表；`declared` = 每镜分配秒（≤12s 合计）；
    `total` = sum(declared)。
    """
    n = len(group)
    bounds, maps = [], []
    left = 0
    for i, s in enumerate(group):
        right = left + declared[i]
        maps.append("<Picture %d> 为第 %d-%d 秒节拍的画面参考" % (i + 1, left, right))
        bounds.append((left, right))
        left = right
    segs = [
        "、".join(maps)
        + "；共 %d 张参考图对应同一条 %d 秒片段的 %d 个节拍，"
          "人物、服装、道具与场景一律以对应参考图为准。" % (n, total, n),
        "本片段总长 %d 秒，由连续发生的 %d 个节拍组成，各节拍按下列时间分配自然衔接，"
        "节拍边界允许 ±1 秒弹性：" % (total, n),
    ]
    for (l, r), s in zip(bounds, group):
        scene = (s.get("scene") or "").strip()
        head = "【第 %d-%d 秒" % (l, r)
        if scene:
            head += "｜%s" % scene
        head += "｜%s·%s·%s】" % (s.get("shot_type"), s.get("angle"), s.get("camera"))
        join = (s.get("join_note") or "").strip()
        join_line = "\n转场承接：%s。" % join if join else ""
        style = (s.get("visual_style") or "").strip()
        style_line = "\n视觉风格：%s" % style if style else ""
        segs.append(
            "%s\n%s%s%s\n台词：%s\n音效：%s\n落幅：%s"
            % (head, (s.get("visual") or "").strip(), join_line, style_line,
               _pack_fmt_dialogue(s.get("dialogue")),
               (s.get("sfx") or "").strip() or "无",
               (s.get("tail") or "").strip() or "自然收在该拍动作结束处"))
    segs.append(
        "画面风格：电影级国风古装剧照质感；这是同一条连续素材，"
        "各节拍光线与色调随场景自然过渡，转场干脆利落，人物造型跨节拍完全一致。")
    segs.append("全片不得出现任何文字、字幕、水印；不得分屏；竖屏构图。")
    # 声音指令（2026-09-22，对标官方出片拍板）：同款模型实测能原生执行
    # 「全程 BGM+环境音、禁止静音段」（官方 12s 示例音轨零静音）。
    # 我方成片声音干巴巴是最大廉价感来源之一。brief 明确禁 BGM 的项目
    # 用 `SHORTDRAMA_PACK_BGM=0` 关掉（config.PACK_BGM）。
    if config.PACK_BGM:
        segs.append(
            "音频：保留协调统一的全程背景音乐与必要环境音，"
            "台词清晰不被音乐盖过，全程禁止静音段。")
    return "\n\n".join(segs)
