# -*- coding: utf-8 -*-
"""v5.config -- 自包含配置（只依赖标准库 + python-dotenv 可选）。

新增（相对旧架构）：静态画面先行的生产参数。
"""
import os
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent          # .../v5
PROJECT_ROOT = PACKAGE_ROOT.parent                      # 工作区根
PROJECTS_DIR = Path(os.environ.get("SHORTDRAMA_PROJECTS", PROJECT_ROOT / "projects"))
# Mutable runtime state: the dev-server checkpoint store (`.langgraph_api`,
# created in the server's cwd), its pid/port state file, logs, and the trash
# folder for deleted projects. Defaults to the repo root so a source checkout
# behaves exactly as before; the packaged app redirects it to a per-user
# writable directory, because the install tree may be read-only.
RUNTIME_ROOT = Path(os.environ.get("SHORTDRAMA_RUNTIME") or PROJECT_ROOT)
SKILLS_DIR = PACKAGE_ROOT / "skills"

# .env（gitignore；密钥不进源码）
try:
    _env = PROJECT_ROOT / ".env"
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _s = _line.strip()
            if not _s or _s.startswith("#") or "=" not in _s:
                continue
            _k, _v = _s.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v
except Exception:  # noqa: BLE001
    pass

# ─── Providers ───────────────────────────────────────────────────────────────
AGNES_BASE = os.environ.get("AGNES_BASE", "https://apihub.agnes-ai.com")


def _split_keys(*raw: str) -> list[str]:
    """把若干「逗号 / 分号 / 换行」分隔的 key 串拆成**去重有序**列表。

    多 key 的**唯一解析点**（2026-09-16）—— 别处不要再自己 `split(",")`，
    否则分隔符支持范围会在两处漂移（同型事故见 `topics/parse-robustness.md`）。
    """
    out: list[str] = []
    for v in raw:
        for part in str(v or "").replace(";", ",").replace("\n", ",").split(","):
            part = part.strip().strip('"').strip("'")
            if part and part not in out:
                out.append(part)
    return out


# ── 多 key 池（2026-09-16）────────────────────────────────────────────────────
#
# `AGNES_API_KEYS="k1,k2,k3"` 优先；未设则退回单 key 变量（兼容旧 .env）。
# **`AGNES_API_KEY` 的语义保持不变 = 池中第一条** —— 因此文本通道（`vendors.py`
# 的 agnes `chat` 段）与 `providers` 里那 3 处 Bearer 调用点**零改动、行为零变化**
# （单 key 时与改造前逐字节等价）。池的消费方（per-key 配速 / 并发提交）另行接线。
#
# ⚠️ **多 key ≠ 必然提速**：只在"供应商限速是按 key 而非按账号"时才有效。
#    该前提**尚未证实**，探测脚本：`scripts/probe_multikey.py`（前 3 阶段零配额）。
AGNES_API_KEYS = _split_keys(os.environ.get("AGNES_API_KEYS"),
                             os.environ.get("AGNES_API_KEY"))
AGNES_API_KEY = AGNES_API_KEYS[0] if AGNES_API_KEYS else ""

# 单条 key 的提交最小间隔（秒）——**多 key 并行时的闸门单位**。
#
# 为什么必须是**函数而不是常量**：它是 `VIDEO_SUBMIT_MIN_INTERVAL_S` 的派生值，
# 而那个常量在测试里被 `mock.patch.object(config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0)`
# 改过。若这里在 import 期就固化成一个 int，就会变成**第二份真相源** ——
# 测试把全局闸门调成 0，per-key 闸门却还是 65 → 单测凭空 sleep 65 秒。
# 故：调用时读 os.environ（若显式设了 per-key 就用它），否则**实时**跟随全局常量。
def video_submit_interval_per_key() -> int:
    v = os.environ.get("SHORTDRAMA_VIDEO_SUBMIT_MIN_INTERVAL_PER_KEY_S")
    return int(v) if v else int(VIDEO_SUBMIT_MIN_INTERVAL_S)

# 主模型（chat）：8 个角色节点 + QC 判据都走它。
#
# 2026-09-10 升级 agnes-2.5-flash → **agnes-3.0-flash**（官方文档
# https://www.agnes-ai.com/zh-Hans/docs/agnes-30-flash）。
# 关键点：3.0-flash 是**纯文本**模型（输入支持文本 + 图像 URL，输出只有文本）
# ——所以只有 `chat` 升级，`image`/`video` 仍留在 2.5 系（官方无 3.0 版）。
# 上下文 512K、最大输出 65,536、OpenAI 兼容 /v1/chat/completions。
# QC 要读图，所以"输入支持图像 URL"这条是它能接替 2.5 的前提。
MODELS = {
    "chat": os.environ.get("AGNES_CHAT_MODEL", "agnes-3.0-flash"),
    "image": os.environ.get("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash"),
    "video": os.environ.get("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash"),
}

# ★ 2026-09-18：`LLM_PROVIDERS` 与 `LLM_DEFAULT_PROVIDER` **已删除**。
#
# 它们是**与媒体侧并行的第二套注册表**（"同一件事写两份"，本项目最忌的一类）。
# 文本通道的厂商档与选择现已**统一到 `v5/vendors.py`**：
#   · 选择变量：`SHORTDRAMA_CHAT_VENDOR`（旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容回落）
#   · 取值入口：`vendors.chat_spec()` → `v5/llm.py::chat_for`
#   · 未注册 / 模型名为空 ⇒ **响亮报错**（旧实现是静默回退 agnes，已改掉）
#
# per-role 覆盖：值 = `v5/vendors.py` 里**已注册的厂商名**（不是模型名）。
# 能力已就位（`role_chat` 读它），但**至今无人填** ⇒ 8 角色 + supervisor 共用同一家。
ROLE_PROVIDER: dict[str, str] = {}
ORCH_PROVIDER = ""

# ─── Video / image ───────────────────────────────────────────────────────────
VIDEO_PROVIDER = os.environ.get("SHORTDRAMA_VIDEO_PROVIDER", "agnes")

# 视频生成模式（2026-09-13 切换为 reference，对齐官方示例）。
# 官方文档：`keyframe` 与 `reference` **互斥**，同一请求不能混用。
#   · reference（当前默认）—— 静帧当**参考图**（`images` 数组，提示词里用
#     `<Picture 1>` 指代）。官方示例（`@角色/@场景` 引用素材）走的就是这条路。
#     好处：可用 `audios`（keyframe 下拿不到）；**各镜完全独立**、无链式依赖。
#     代价：**没有首帧锁定**，构图只能靠 prompt 文本描述；且不允许
#           first_frame/last_frame → 原来的「上一镜真实尾帧承接下一镜」失效
#           （连带 `STILL_CHAIN` / `TAIL_PREGEN` 两个为"缓解链式串行"而生的
#            开关失去意义——没有链了，也就没有串行瓶颈了）。
#   · keyframe（回退档）—— 静帧当首帧，构图被锁死、支持镜间承接，但无 audios。
# 回退方式：设 `SHORTDRAMA_VIDEO_MODE=keyframe`。
VIDEO_MODE = os.environ.get("SHORTDRAMA_VIDEO_MODE", "reference").strip().lower()

# 单镜上限秒数。★ **2026-09-16 修：默认 10 → 12**。
#   事故形态：**静默压短**。`providers.submit_video` 会把单镜秒数钳到
#   `min(seconds, VIDEO_MAX_SECONDS)`，而它的注释自己写着「**API 硬约束 seconds ∈ [4, 12]**」
#   —— 但默认值给了 10 ⇒ **有效范围被压成 [4, 10]**，11/12 秒的镜被**静默渲成 10 秒**。
#   而契约侧（分镜 SKILL）写的是「单镜 **4-12 秒**」⇒ **两处判据不一致**。
#   实测影响面：全项目 620 镜中 **9 镜 >10 秒（1.5%）** 被静默压短
#   （morning-stall 2 / rainy-door 2 / six-winters 5；其中 rainy-door 还有一镜写 14s，
#    那是**上游契约违规**，见 `validate.check_storyboard` 的说明）。
#   依据：官方文档 `<https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25-flash>` ——
#   「`seconds` 支持字符串 `"4"`–`"12"`」+ 接入检查清单同项（2026-09-16 核对）。
VIDEO_MAX_SECONDS = int(os.environ.get("AGNES_VIDEO_MAX_SECONDS", "12"))
VIDEO_MAX_SHOTS = int(os.environ.get("AGNES_VIDEO_MAX_SHOTS", "20"))
# 平铺闸门：供应商 1rpm 的现实约束（换本地模型可设 0）
VIDEO_SUBMIT_MIN_INTERVAL_S = int(os.environ.get("SHORTDRAMA_VIDEO_SUBMIT_MIN_INTERVAL_S", "65"))

# ── 多 key：提交**轮转配速**（2026-09-16，**默认关**）────────────────────────
#
# 依据（实测，非推断；复现脚本 `scripts/probe_multikey.py`，五阶段）：
#   · 同一条 key 在 60s 内第二次提交 → **429**（闸门真实存在，不是"已经没限速了"）；
#   · **不同** key 间隔 2s 提交 → **全部通过** ⇒ 1rpm 是 **per-key**，不是 per-account；
#   · 被拒的 key 等过一个窗口后立即恢复 ⇒ 是**间隔闸门**，不是配额耗尽；
#   · 盘上真实 `video_id` 用任意一条 key 都能查到 ⇒ **账号维度**，
#     故轮询不必记 key_id（`video_jobs.json` 的 schema 不用动）。
#
# 为什么是**轮转**而不是线程池：提交调用本身只占 1–2 秒，墙钟瓶颈 100% 是
# per-key 闸门（65s）。让每镜轮流用不同 key，就能把这段按 key 数摊薄 ——
# 收益与多线程**完全相同**，却不必给 `video_jobs.json`（单写入者假设）加锁，
# 也不必处理线程间的日志/计数交错。**能不加线程就不加。**
#
# 效果：`submit_all` 的提交段从 65s×N 降到约 65s×N/K（40 镜 / 3 key：
# 43 分 → 约 15 分）。注意**上界**：只摊薄"提交节流"，不加快单镜渲染本身。
#
# 默认关：关掉时池里只有第一条 key ⇒ 闸门与改造前**逐字节等价**
# （唯一差异是闸门从"提交成功后 sleep"变成"提交前领 key"，间隔相同）。
# 开启：`SHORTDRAMA_VIDEO_KEY_ROTATE=1`（key 数 >1 才有意义）。
VIDEO_KEY_ROTATE = os.environ.get("SHORTDRAMA_VIDEO_KEY_ROTATE", "0") != "0"

# ─── 新架构：静态画面先行 ────────────────────────────────────────────────────
# STILL_FIRST=1：预演静帧即生产首帧（图生视频）；0 = 退回旧模式（文生视频 + 参考图）
STILL_FIRST = os.environ.get("SHORTDRAMA_STILL_FIRST", "1") != "0"
# 静帧尺寸（竖屏 9:16）
STILL_RATIO = os.environ.get("SHORTDRAMA_STILL_RATIO", "3:4")
# 全局 BGM 开关：niulai-movie-style 等类型包禁忌「无背景音乐」，
# 设 SHORTDRAMA_VIDEO_BGM=0 让组装器尾缀改成"不要背景音乐"。
VIDEO_BGM = os.environ.get("SHORTDRAMA_VIDEO_BGM", "1") != "0"
# 串行链式渲染：连续镜首帧 = 上一镜真实尾帧（画面不重复，但镜间串行更慢）。
# CHAIN=0 → 并铺式（各镜用自己的静帧，快，但 continuous 镜会画面重复）。
STILL_CHAIN = os.environ.get("SHORTDRAMA_STILL_CHAIN", "1") != "0"

# ─── 落幅帧预生成（解锁视频并行）────────────────────────────────────────────
#
# 背景（2026-09-10）：`submit_chain` 用**上一镜渲出来的真实尾帧**当下一镜
# first_frame → 连续镜必须等前一镜渲完 → 整条链串行。bootleg99-full 32 镜里
# 25 镜落在链上，86 分钟就是这么来的。而 cut 镜（maskparade 全 18 镜都是 cut）
# 完全不吃这个瓶颈，**开了也没收益**——它是给连续镜占比高的项目用的。
#
# 开启后：静帧阶段额外为"将被下一镜承接"的镜预生成**落幅帧图**，
# 视频阶段回到平铺提交（first=上一镜落幅图，last=本镜静帧），串行依赖消失。
#
# **代价（必须知道）**：预生成的落幅图 ≠ 上一镜实际渲出的尾帧——视频模型的
# 运动是随机的，两者必有偏差，承接从"事实"降级为"预期"。且要多花若干张生图。
# 故**默认关闭**：这是"速度换承接质量"的取舍，由人按项目决定。
TAIL_PREGEN = os.environ.get("SHORTDRAMA_TAIL_PREGEN", "0") != "0"

# ─── 人在环：显式审批门 ──────────────────────────────────────────────────────
#
# 2026-09-10 决策：把"人工验收"从**纪律**变成**机制**。
# 在此之前只有 `--stills-only` + 环境变量这类隐式约定，注释写着"静帧已人工验收"
# 却没有任何证据（谁、何时、依据哪一版）。审批记录见 `media/ep<N>/approvals.json`，
# 且**带产物指纹**——上游一变审批自动作废（详见 media/approvals.py）。
#
# 默认 False：现有流程与测试完全不受影响；要用的人显式开启。
REQUIRE_APPROVAL = os.environ.get("SHORTDRAMA_REQUIRE_APPROVAL", "0") != "0"

# 创作链的**步级 HITL**：每次派发子代理（`task` 工具）前 `interrupt`，等人 resume。
#
# 与上面三道审批门的区别（别混）：
#   · 审批门（`REQUIRE_APPROVAL`）：**阶段之间**的门，检查产物是否合格才放行；
#   · 本开关：**图运行中**的 `interrupt`（`create_deep_agent(interrupt_on=...)`），
#     粒度是"每一步派发之前"，靠 `Command(resume={"decisions": [...]})` 继续。
#
# **默认 False 是硬要求**：`scripts/drive_chain.py` 是**全自动**跑完整条链的，
# 默认开启会让每条链都在第一步挂起等人。交互式推进（Studio / 前端 / 人工盯片）
# 才设 `SHORTDRAMA_APPROVE_EACH_ROLE=1`。信道与用法见 `v5/hitl.py`。
# 规范出处：`langchain-dev-guide / middleware.md` Issue 3 —— resume 值是
# **复数数组** `{"decisions": [{"type": "approve"}]}`，不是 `{"decision": ...}`。
APPROVE_EACH_ROLE = os.environ.get("SHORTDRAMA_APPROVE_EACH_ROLE", "0") != "0"

# ── 人工模式：**人在看片**（2026-09-19，前端路径专用）─────────────────────────
#
# 语义：**判断权在人**。于是"机器替人把关"的那两道门降级成**报告**：
#   · `guards.media_gate` 的「必须 `reviewer.passed`」不生效 —— 只要 7 个角色
#     产物齐就放行（reviewer 照跑、照出报告，但不拦）；
#   · `series._storyboard_gate` 的契约硬伤**打印成警告**，不 `SystemExit`。
#
# 为什么不做成"全局删掉门"（那是最容易想到的做法，也是最贵的）：
#   `media_gate` 是**唯一入口**，CLI / 外部 agent（全自动、**没有人看**）走的是同一份。
#   全局放开 = 把全自动路径**唯一**的质量保护也一起拆掉。
#   ⇒ 所以它是一个**显式模式开关**，由**前端路径**打开（`media/runner.start`
#     给子进程设 env，见那里的注释），默认关 ⇒ CLI / 外部 agent 行为逐字节不变。
#
# 与上面两个开关的关系（别混）：
#   · `REQUIRE_APPROVAL` —— 阶段之间的**审批门**（产物指纹 + 人工批文）；
#   · `APPROVE_EACH_ROLE` —— 图运行中的**步级 HITL**（每次 `task` 前 interrupt）；
#   · 本开关 —— **门的处置方式**（拦 vs 只报）。三者可任意组合。
HUMAN_IN_CHARGE = os.environ.get("SHORTDRAMA_HUMAN_IN_CHARGE", "0") != "0"

# 上游产物是否**直接注入**下游角色的输入（而不是只给路径、让它自己 read_file）。
#
# 背景（2026-09-13 实测）：角色 = `create_agent` + FS 工具，任务是"读上游 → 思考 →
# 写产物"，实测 **8–10 轮 LLM 调用/角色**（dev.log 里每 30–60 秒一次）→ 单步 4–6 分钟。
# 而 `role_input` 原本**只给路径**，注释写着"上游产物可达数十 KB，全量注入会挤掉创作空间"。
# 但实际产物总共才 ~10 KB（约 3–5K token）—— **省的是几 KB，付出的是 10 倍轮次**。
#
# 打开后：上游全文进 prompt，角色不必再 read_file（**仍要 write_file 落产物**）。
# 默认 **True**；设 `SHORTDRAMA_INLINE_UPSTREAM=0` 回退旧行为（只给路径）。
INLINE_UPSTREAM = os.environ.get("SHORTDRAMA_INLINE_UPSTREAM", "1") != "0"

# 视频提示词是否走 **LEAN（精简）** 形态 —— 对齐官方示例的静默镜写法。
#
# 现状每个镜头的提示词 800~980 字，其中约 **500 字是"指令层"**：
#   反分屏前置(~107) / reference 用途声明(~47) / 类型包风格块(209) /
#   静默镜禁声标签(~50) / 禁字幕尾缀(~90)
# 官方示例的静默镜**只有内容**（景别机位+风格句+内容+落幅，约 200 字），
# 且经用户确认**官方平台的视频模型与本项目完全相同** → 差异只能在提示词。
#
# LEAN=1 时**删掉**：反分屏前置 / reference 用途声明 / 类型包风格块。
# **保留**：内容四段 + 台词 Audio: + 环境声 + **禁字幕**（有 4 次烧字事故史，不能删）。
#
# 为什么不做成"另一个拼装函数"：2026-09-13 我手写脚本拼提示词，
# **连漏两步**（`resolve_styles`、`audio_line`）→ 两轮数据作废、白烧配额。
# 生产路径的每一步都可能是别处踩坑换来的 —— 所以做成**函数内的条件分支**，
# 复用同一条链路，而不是另起一套。
#
# 默认 **0**（不改现状）；设 `SHORTDRAMA_LEAN_PROMPT=1` 启用。
LEAN_PROMPT = os.environ.get("SHORTDRAMA_LEAN_PROMPT", "0") != "0"

# 全链路是否对**外部调用方**开放（外部 Agent 本是系统的主入口）。
#
# 2026-09-10 决策：`--monitor` 只读铁律是**调试期纪律**，不是架构；长期锁死会
# 限制可用性。现改为"开放 + 三道门守输入"：
#   · 开（True / 设 SHORTDRAMA_OPEN_CHAIN=1）→ 可直接跑全链路，
#     由 brief 门 / 分镜契约门 / 资产契约门守住输入。
#   · 关（默认）→ 维持旧行为：--resume-media / --stills-only 需
#     显式 SHORTDRAMA_ALLOW_RESUME=1。
# 默认关是**稳妥的迁移策略**：先让机制就位，由人按项目逐个放开。
OPEN_CHAIN = os.environ.get("SHORTDRAMA_OPEN_CHAIN", "0") != "0"
# 资产生成（角色三视图 / 道具参考图）：静帧之前确定性产出 images/*.png。
# 关掉则退化为"无参考图"（人物一致性无保障），仅在复用已有 images/ 时设 0。
CAST_ENSURE = os.environ.get("SHORTDRAMA_CAST_ENSURE", "1") != "0"

# ── 源照片的处置方式（2026-09-15）────────────────────────────────────────────
# 默认 = **直绑**：把**源照片本身**作为参考图绑进每一镜。
# `SHORTDRAMA_SOURCE_TURNAROUND=1` = 先按源照片生成**四视图**、再绑正面单格。
#
# 取舍（实测依据）：四视图的好处是侧面/背面也有依据；但它是**照着照片重画**出来的一跳 ——
# 实测（village-bees 老周）**四个视角的脸彼此并不一致**（3/4 侧面与正侧面明显是另一张脸），
# 而绑定只用正面那一格。于是"照片 → 四视图 → 静帧"是**两次重画**，每次都在丢相似度。
# 直绑后只剩"照片 → 静帧"**一跳**，最接近用户给的照片。
# 用户反馈「为什么样子都变了，不是和我给的照片完全一样的」即由这两跳造成。
SOURCE_TURNAROUND = os.environ.get("SHORTDRAMA_SOURCE_TURNAROUND", "0") != "0"

# ── 源照片 → **四宫格设定表**（2026-09-19，用户定的方案）────────────────────────
# 触发规则（2026-09-19 晚定稿，用户要求"放了照片就该走新流程"）：
#   · **默认 auto**：项目里有 `images/<角色名>.source.<ext>` ⇒ 自动走"先做四宫格设定表、
#     再把**这张表**绑进每一镜"（见 `media/sheet.py`）；
#   · `SHORTDRAMA_SOURCE_SHEET=0` ⇒ **强制关闭**，回到历史的"源照片直绑"；
#   · `SHORTDRAMA_SOURCE_SHEET=1` ⇒ 强制开启（无源照片时无意义，`cast` 仍要求有照片）。
#   · **手供表优先**：`images/<角色名>.sheet.<ext>` 存在 ⇒ 直接拿它当表，**不消耗任何生成**
#     （用户自己有产能时最省；版式完全由用户控制）。
#
# 为什么（用户实测结论，2026-09-19 当晚逐步验证）：
#   · 四宫格给模型**多点视角**（正/侧/背 + 大头像）⇒ 换机位时脸部依据更足；
#   · 每镜仍走 **reference**（引用该资产）+ 分镜文字自由描述姿势动作 ——
#     **姿势不受照片限制**（扩图只用在"源照片→设定表"这一步做身份锚定）；
#   · 配套纪律（都写进 `sheet.py` 的提示词）：白底、头肩像与三视图同一人、
#     纯色干净服装（避免服装纹理误导视频模型）、显式声明性别体态
#     （源照片脸型柔和时模型会自作主张画成女性，实测踩过）、4K 档。
# 关掉 ⇒ 行为与历史一字不变（默认直绑照片）。
_SS = (os.environ.get("SHORTDRAMA_SOURCE_SHEET") or "auto").strip().lower()
SOURCE_SHEET = _SS not in ("0", "false", "no", "off", "disabled", "n")

# 生图尺寸档（`1K`/`2K`/`3K`/`4K`，厂商档位式取值）。空 = 用厂商档默认（agnes 档为 1K）。
# 为什么可调（2026-09-19 实测）：1K 的 9:16 出图只有 736px 宽，**脸部细节不足**；
# 4K 档下同一提示词的脸部纹理、发际线、毛孔量级明显更好（该厂商文档称各档当前均免费）。
IMAGE_SIZE = (os.environ.get("SHORTDRAMA_IMAGE_SIZE") or "").strip() or None

# ── 有源照片时，还要不要注入那 220 字「文本身份锚点」（2026-09-15）──────────────
# 默认 **0 = 不注入**。实测依据（用户反馈「差太远了」）：同一张照片、同一镜，
#   提示词 **545 字 → 75 字**（只留"以照片为准 + 机位景别 + 画面内容"）之后，
#   出图相似度**大幅提升** —— 发际线/眉/鼻/唇/下颌都对上，且是真实照片质感。
#   而 545 字里最大的一块就是**每个角色约 220 字的外貌描述**
#   （里面还带着"面部棱角清楚、面部线条硬朗"这类**与照片打架**的形容词）
#   ⇒ 有照片时那段文字是**净损失**，身份本来就该由照片承担。
# 设 1 = 恢复注入（照片质量差、想用文字纠偏时用）。
KEEP_IDENTITY_WITH_PHOTO = os.environ.get("SHORTDRAMA_KEEP_IDENTITY_WITH_PHOTO", "0") != "0"
# 静帧硬伤 QC（每镜 2 次视觉调用：硬伤 + 景别）。
# 续跑场景下**必须能关**：18 镜的一轮 QC 约 5-6 分钟，而进程常在中途被环境
# 回收（驱动脚本已归档到 .tmp/_cleaned-20260910/）→ 每轮重启都白跑一遍 QC，
# 视频阶段永远轮不到。
# 静帧已人工验收后可设 0，直取视频阶段。
STILL_QC = os.environ.get("SHORTDRAMA_STILL_QC", "1") != "0"
# 资产完整性**硬拦**开关（2026-09-12 新增，默认关）。
#
# 背景：资产契约门（`series._assets_gate`）跑在 `cast` **之前** —— 那时注册表
# 必然是空的 → 它只会误报（永远说"角色未命中"），而"有资产卡但图没生成"它看不见。
# 所以真正的完整性检查放在 `pipeline.run` 里 **cast 之后**（`assets.validate_assets`），
# 按磁盘事实检查：分镜 @ 引用的资产是否"注册表有条目 **且** 图在盘"。
#
# 默认**只强告警不拦**（现存项目多有部分缺图，直接拦会把它们全卡住）；
# 设 1 则缺失时 `return blocked`（适合作为对外服务的准入条件）。
ASSET_GATE_STRICT = os.environ.get("SHORTDRAMA_ASSET_GATE", "0") != "0"
# 对白**逐字门**（2026-09-14 新增）的严格开关。
#
# 背景：`dialogue` 角色的旧契约写着「负责对白润色与优化」「重写问题台词」，
# 产物标题是「对白优化报告」→ 它会**改写剧本台词**；而 `scriptwriter_ep1.md`
# 本身也带镜级台词 → 同一句台词两个版本 → 分镜配镜无从取舍，
# 下游配音/口型与画面对不上（paper-crane 实测：同一晚连续 3 次）。
#
# 契约已改成"逐字提取"（`packs/shortdrama/dialogue/SKILL.md`），但那只是提示词；
# 这里补的是**确定性判据**：清单里每句台词必须能在剧本里原样找到
# （`validate.check_dialogue_verbatim`，纯字符串比对，不花配额、不会误判）。
#
# 默认**只警告不拦**（存量项目的旧 dialogue 产物多为改写版，直接拦会把它们全卡住）；
# 设 1 则在 `_input_gates` 里阻断。沿用 `ASSET_GATE_STRICT` 的渐进策略。
DIALOGUE_VERBATIM_STRICT = os.environ.get("SHORTDRAMA_DIALOGUE_VERBATIM_STRICT",
                                          "0") != "0"
# 单镜**累计**重画上限（跨进程计数，记在 media/ep<N>/still_requeue_tally.json）。
#
# 为什么必须有（2026-09-10 实测）：复核 QC 走 LLM，**判据是概率性的**——
# 同一张图、同一分镜上下文连审 3 次，LN08 判 1/1/2 处硬伤、LN10 判 0/1/0。
# 因此"零硬伤"结构上不可达。而 `max_regen` 只在单进程内计数，外部续跑器
# 每重启一次就重置 → 静帧被无限重画（实测 4 轮硬伤数 9→7→10→9 震荡，
# 纯烧生图配额）。与 clip 侧的 CLIP_QC_MAX_REQUEUE 对称。
# 上限到了就保留现有静帧并如实记入 still_residual。
STILL_QC_MAX_REGEN = int(os.environ.get("SHORTDRAMA_STILL_QC_MAX_REGEN", "3"))
# 静帧 QC 的限速退避重试（2026-09-12）。
#
# 为什么需要：供应商是**免费额度**，每分钟窗口很窄——重生成一批静帧（图片调用）
# 与 QC 视觉调用叠在一起就会撞 429。实测 clockmaker 17:00 撞了一次，而事后单独
# 连发 5 次调用全部正常 → **突发限速，不是额度耗尽**，所以退避有效。
#
# **只能退避、不能兜底**：把 429 当成"无硬伤"会静默漏检（硬伤镜进成片）。
# 退避时长 = base * 2**i。attempts 次仍失败才抛，由上层如实记录。
QC_RETRY_ATTEMPTS = int(os.environ.get("SHORTDRAMA_QC_RETRY_ATTEMPTS", "5"))
QC_RETRY_BASE_S = float(os.environ.get("SHORTDRAMA_QC_RETRY_BASE_S", "5"))
# 静帧 QC：**负面判定复采确认**（2026-09-16）。
#
# 为什么需要：QC 判据是**概率性**的，同一张图连审会翻判（代码里已记两次实证：
# LN12 干净/干净/有硬伤、LN08 判 1/1/2 处硬伤）。而**每次负面判定都会触发一次重画**
# （一张图 + 下一轮复审），所以**翻判的成本全落在"误报"这一侧**。
# 实测到的错误方向也是误报多于漏报（felt-frog 确认 2 例误报：把「人+青蛙同框」
# 判成"同一人出现两次"；把「按本包契约要求的可见五官」判成"长出真人五官"）。
#
# 做法：**只对负面判定**复采一次；两次都判负面才算硬伤。正面判定不复采（成本可控：
# 大部分镜是通过的）。偏置方向刻意选"宽松"——用"多漏一点"换"少白烧一批重画"。
# 设 0 关闭（回到旧行为：单次采样说了算）。
QC_CONFIRM_NEGATIVE = os.environ.get("SHORTDRAMA_QC_CONFIRM_NEG", "1") != "0"
# 静帧 QC：**同类问题连续两轮 → 停止重画**（2026-09-16）。
#
# 为什么需要：硬伤类别靠 `desc` 里的关键词**再猜一次**，猜不到就「按原提示词重生成
# （换种子）」= **概率赌博**。当根因在提示词或结构层时，重画根本解决不了 ——
# 实测 felt-bach：LN08「两个相似老头」/ LN16「缺风箱工」连续两轮都落到"未归类"，
# 原样重画到撞上限，同类问题照旧（最终 6 镜带伤放行，那两轮重画**纯白做**）。
#
# 做法：同一镜**连续两轮报同一类**问题 → 第三轮不再重画，直接记 residual 并说明理由。
# 类别变化（说明上一次强化起了作用、只是又冒出别的）时仍允许重画。
# 设 0 关闭（回到旧行为：一直重画到上限）。
STILL_QC_STOP_REPEAT = os.environ.get("SHORTDRAMA_QC_STOP_REPEAT", "1") != "0"
# 成片抽帧复核：拼接前抽帧查烧字/构图跑偏，不合格镜回炉（0 = 跳过，省时）。
CLIP_QC = os.environ.get("SHORTDRAMA_CLIP_QC", "1") != "0"
# 复核重拍轮数。**必须 ≥1**：只审一轮时，重拍的 clip 不再复核就拼接——
# 实测（2026-09-10）LN06 重拍后仍烧着「影视效果 请勿模仿」字幕，因"只审一轮"
# 直接进了成片。自愈环路必须有收敛判定。
# **快速模式**（2026-09-13）：出片优先，把"昂贵的检查"交回给人。
#
# 语义：**只关 clipqc，保留静帧 QC**（理由见 `../docs-archive-20260918/spec-qc-modes-and-rerender.md`）：
#   · 静帧 QC —— 1 张图一次判定，**修一次只要 30–60 秒**（重画静帧）；
#     而且静帧是视频的输入，**在源头修掉的问题，视频层不会再犯**。
#   · clipqc —— 要**抽 3 帧**再判，**修一次 3 分钟+**（重渲视频）。
#     实测（rainy-door，6 镜 60 秒的片）：视频生成 **1.6 分**，而 clipqc **18 分且未完**。
#     → **检查比生成贵 10 倍**，且今天 3 次误判里至少 2 次是它造成的无谓重拍。
#
# 关掉 clipqc 后，"带伤出厂"就没有兜底了 —— 但实测那些"伤"里**误判占比不低**
# （`qc.is_hard_issue` 已修掉其中"否定语境"那一类；"判定依据错误"那类仍需人看）。
# 配套用 `--rerender <镜号>` 人工定向修（同一天加的）。
FAST = os.environ.get("SHORTDRAMA_FAST", "0") != "0"

# 成片复核的重拍轮数。**FAST 时归零**（不再自动复核）。
CLIP_QC_ROUNDS = 0 if FAST else int(os.environ.get("SHORTDRAMA_CLIP_QC_ROUNDS", "2"))
# 队列满（503 video_queue_full）时的退避重试次数。队列满是瞬时的：
# 实测（2026-09-10）批量重拍反复撞 503，不重试就会有镜直接缺席成片。
VIDEO_QUEUE_RETRIES = int(os.environ.get("SHORTDRAMA_VIDEO_QUEUE_RETRIES", "5"))
# 单镜**累计**重拍上限（跨进程轮次计数）。
#
# 2026-09-10 死锁实测：clipqc 每轮作废不合格 clip → 重渲又撞队列满/429 →
# 下轮重启再 audit 再作废 → **渲出来就被删，成片永远攒不齐**。
# 上限到了就**保留 clip 并记入 residual**——宁要"有瑕疵但完整"的成片，
# 不要"永远缺镜"的空转。
CLIP_QC_MAX_REQUEUE = int(os.environ.get("SHORTDRAMA_CLIP_QC_MAX_REQUEUE", "3"))

# ─── 媒体链独占锁（2026-09-13，spec §3.4 风险 #2/#3）──────────────────────────
#
# `video_jobs.json` 是**单写入者假设**的状态机，`episode_final.mp4` 也是。
# 单镜重渲（`--rerender`）会与"正在跑的全链路"或"另一个重渲"并发 —— 两个写入者
# 交错 save 会把状态机写坏（丢 job / 把已 completed 覆盖成 pending），
# 拼接重入则可能产出半截成片。
#
# 默认开（同一 (项目, 集) 同时只允许一条媒体链）；测试/一次性脚本可设 0 关掉。
MEDIA_LOCK = os.environ.get("SHORTDRAMA_MEDIA_LOCK", "1") != "0"
# 锁的**陈旧阈值**：持锁进程每 60s `touch()` 一次心跳，超过该秒数无心跳即视为
# 残留锁（进程被沙箱回收），后来者接管。
#
# **为什么不用 PID 探活**：CPython 在 Windows 上 `os.kill(pid, 0)` 的实现是
# `OpenProcess(PROCESS_ALL_ACCESS) + TerminateProcess(hProc, sig)` —— 拿它"探测"
# 一个正在跑的媒体链，会**真的把它杀掉**（本项目在 Windows 上跑）。故只认心跳。
MEDIA_LOCK_STALE_S = float(os.environ.get("SHORTDRAMA_MEDIA_LOCK_TTL", "300"))

# ─── 熔断（旧架构缺失的教训）────────────────────────────────────────────────
TOKEN_BUDGET_RUN = int(os.environ.get("SHORTDRAMA_TOKEN_BUDGET_RUN", str(3_000_000)))
TOKEN_BUDGET_ROLE = int(os.environ.get("SHORTDRAMA_TOKEN_BUDGET_ROLE", str(800_000)))
MAX_REVISIONS_PER_PHASE = int(os.environ.get("SHORTDRAMA_MAX_REVISIONS", "3"))

ASPECT_RATIO = os.environ.get("SHORTDRAMA_ASPECT", "9:16")

#: 前端可选的画幅。**取 agnes 两个接口接受值的交集**（2026-09-20 查官方文档）：
#:   · 生图 `ratio`        : 1:1 3:4 4:3 16:9 9:16 **2:3 3:2** 21:9   （默认 1:1）
#:   · 生视频 `aspect_ratio`: 21:9 16:9 4:3 1:1 3:4 9:16              （默认 16:9）
#: `2:3` / `3:2` 只有图像支持 —— 而项目比例会**同时**下发给静帧与视频
#: （`media/runner.py` 一次写 `SHORTDRAMA_STILL_RATIO` + `SHORTDRAMA_ASPECT`，
#: 只改一个会让静帧与成片画幅不一致），所以这两档**故意不列**。
#: 加档：确认两个接口都支持后往这里加（前端候选、后端校验、`/health` 回显都读这一份）。
RATIO_CHOICES = ("9:16", "16:9", "4:3", "3:4", "1:1", "21:9")
