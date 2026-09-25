# -*- coding: utf-8 -*-
"""**源照片 → 四宫格设定表**（2026-09-19，用户定的方案；唯一入口 `ensure`）。

它做什么
--------
把一个角色的用户源照片，做成一张**四格设定表**并落成该角色的参考图：

    ┌──────────┬────────┬────────┬────────┐
    │ 白底头肩像 │  正视图  │ 侧视图  │ 背视图  │
    └──────────┴────────┴────────┴────────┘
     （脸大、只留人像）  （站姿半身三视图，取景头顶→大腿中部）

之后每一镜仍走**普通的 reference 生成**（`stills` 拿这张表当参考图 + 分镜文字自由描述
姿势动作）——**扩图只用在"源照片→设定表"这一步做身份锚定，不用在每一镜上**
（用户 2026-09-19 明确纠正：姿势不能由照片决定，四宫格是为了让视频模型保持人物一致性）。

为什么每一格那样做（都是当晚实测换来的，别随手改）
--------------------------------------------------
1. **头肩像用"背景变换"而不是重新画人**：提示词只允许改背景与**服装/配饰**，
   脸与头发像素不动，所以左格的脸就是本尊；实测相似度 NCC≈0.76（若是抠图复制会 >0.97，
   见 `review/proof_no_matting.png`）。⚠️ 放开"发型也重画"会连带把脸美颜重画
   （实测踩过，见纪律 4）——脸与头发的锁不许再松。
2. **三视图以"头肩像/正视图"为参考去转身**，而不是各自从照片重新编一个人 —— 保证三格同一人。
3. **必须显式声明性别体态**：源照片脸型柔和时（清秀的年轻男性）模型会自作主张画成女性
   （实测踩过：三视图全部长出胸部）。提示词里写死"成年男性/胸部平坦"。
   性别从角色卡外貌段嗅探（少女/女子/她 ⇒ 女），**嗅不到一律按男**（历史默认，
   防"清秀男漂成女"那个实测坑）⇒ 女性角色卡请确保外貌段含女性用词。
4. **服装与配饰跟项目**（2026-09-19 用户定稿，取代旧的"统一黑T恤"）：只有**服装/配饰**按
   **角色卡外貌段**画；**脸与头发像素锁死原图**（回到 NCC≈0.76 的实测档位）。
   旧结论「参考图里的花哨纹理会被当成制服照搬」在**现代装项目**依然成立，但国风/古装包里它反过来咬人——
   黑T恤锚点会漏进双人镜（实测 osmanthus-vow LN09：两人穿黑T恤站在竹林前，
   单人镜靠分镜文字才纠回汉服）。无卡片文本时**回落旧的黑T恤行为**（一字不变）。
   ⚠️ v1 曾把**发型**也交给卡片重画 ⇒ 实测脸被连带美颜重画、"完全不像原图"
   （osmanthus-vow 2026-09-19 晚，用户打回）⇒ 发型一律留原照，古风发髻/头饰交给
   静帧文字（LN12 实测单人镜能纠回来）。
5. **取景**：三视图**头顶 → 大腿中部**（不要拍到小腿与脚）；左格是**头肩像**（颈肩入画，
   不是只剩一个头）——按用户给的官方示例版式。
6. **比例**：源照片是方图时，白底转换必须用 **1:1**（用 3:4 会让模型把人挤窄，实测踩过）。

开关：`config.SOURCE_SHEET`（默认关；关掉时 `cast` 走历史路径，行为一字不变）。
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

from .. import config
from . import providers

SHEET_DIR = "_sheets"          # 与 `cast.SHEET_DIR` 同义：人工查看用，不被 auto_sync 收编
DEFAULT_SIZE = "4K"            # 设定表是一次性产物，用最高档（该厂商文档称各档当前免费）

# ── 提示词（逐条对应文件头的"为什么"）─────────────────────────────────────────
_KEEP_FACE = ("只做一件事：把人物**以外**的背景区域改成纯白色。人物的任何部分都不要重新绘制——"
              "人脸、五官、头发、肤色、表情、每一个像素都保持原样不动，不要美化、不要磨皮、"
              "不要改变表情，**不要改变人物比例（不要拉长或挤窄）**。"
              "除了把背景涂白、把衣服换成下面指定的纯色衣物，画面其余部分必须与输入图完全一致。"
              "不要任何文字、水印。")
_CLOTH_TEE = ("把他的衣服换成**干净的纯色衣物**：纯黑色圆领短袖 T 恤（纯色、颜色均匀，"
              "无图案、无文字、无 logo、无印花、无口袋、无杂色）。")
_FRAME_FACE = ("取景：**头肩像**——头部、两侧肩膀与上胸完整入画，不要裁成只剩一个头。")

_MALE = ("这**是成年男性**：男性骨架与体态——肩膀较宽、**胸部完全平坦、绝对不要出现乳房或"
         "女性胸部隆起**、腰臀比为男性、颈部可见喉结；禁止一切女性化特征"
         "（不要女性胸型、不要细腰翘臀）。")
_CLOTH_FULL = ("身穿**纯黑色圆领短袖 T 恤**与纯黑色长裤：颜色均匀、干净的纯色，"
               "无图案、无文字、无 logo、无印花、无口袋、无杂色。")
_FRAME_VIEW = ("取景：从头顶到大腿中部——头顶完整入画，画面下沿切在大腿中段，"
               "**不要出现小腿与脚**；人物居中、占画面高度约 80%，两侧留白；"
               "站姿端正、双手自然垂在身侧。")
_KEEP_ID = ("脸部五官、发型、发际线、年龄必须与参考图是同一个人，**脸不许改变**；"
            "不要美化、不要改变人物比例。")
_STYLE = "纯白背景、写实照片质感、均匀柔光、4K 超清。不要任何文字、水印、边框。"

P_FACE = _KEEP_FACE + _CLOTH_TEE + _FRAME_FACE
P_FRONT = ("参考图中是这个男性。把他做成标准角色参考图。角度：**正面朝镜头**。"
           + _FRAME_VIEW + _MALE + _CLOTH_FULL + _KEEP_ID + _STYLE)
P_SIDE = ("参考图中是这个男性。把他**原地转向**：身体与头部一起转向画面左侧，呈现"
          "**90 度正侧脸**（能看清鼻梁与耳朵侧面轮廓），不要正面、不要回头看镜头。"
          + _FRAME_VIEW + _MALE + _CLOTH_FULL + _KEEP_ID + _STYLE)
P_BACK = ("参考图中是这个男性。把他**原地转过身去**：完全背对镜头，只能看到后脑勺、后颈、"
          "背部与衣服背面，不要出现脸部。"
          + _FRAME_VIEW + _MALE + _CLOTH_FULL + _KEEP_ID + _STYLE)

# ── 妆造跟项目（2026-09-19 用户定稿）：服装与配饰按角色卡；脸与头发像素锁死原图 ──
_FEMALE = ("这**是成年女性**：女性骨架与体态——胸部有女性曲线、腰臀比为女性；"
           "禁止一切男性化特征。")
_KEEP_FACE_LOOK = ("只做两件事：① 把人物**以外**的背景区域改成纯白色；"
                   "② 把衣服换成下面造型描述所写的**服装与配饰**。"
                   "**人脸、五官、脸型轮廓、头发（发型/发际线/发长）、肤色一个像素都不要重新绘制**——"
                   "不许美化、不许磨皮、不许换发型、不许改变表情、"
                   "**不要改变人物比例（不要拉长或挤窄）**。"
                   "画面其余部分必须与输入图完全一致。不要任何文字、水印。")
_KEEP_ID_LOOK = ("脸部五官、脸型轮廓、发型、发际线必须与参考图完全一致，是同一个人，"
                 "**脸与头发不许改变、不许照造型文字重画**；不要美化、不要改变人物比例。"
                 "服装与配饰以下方造型描述为准。")


def _gender_of(costume: str) -> str:
    """从角色卡外貌段嗅探性别用词；**嗅不到按男**（历史默认，防清秀男漂成女）。"""
    return "女" if any(w in costume for w in ("少女", "女子", "女性", "她", "妇")) else "男"


def _prompts(costume: str):
    """返回 (脸, 正, 侧, 背) 四条提示词。无卡片妆造文本 ⇒ 回落历史黑T恤行为。"""
    look = (costume or "").strip()
    if not look:
        return P_FACE, P_FRONT, P_SIDE, P_BACK
    g = _gender_of(look)
    he = "她" if g == "女" else "他"
    body = _FEMALE if g == "女" else _MALE
    cloth = ("造型描述（**只取其中的服装与配饰**来画；头发与脸一律以参考图为准，"
             "不许照文字重画）：" + look[:600] + "\n")
    face = _KEEP_FACE_LOOK + cloth + _FRAME_FACE
    front = ("参考图中是这个%s性。把%s做成标准角色参考图。角度：**正面朝镜头**。" % (g, he)
             + _FRAME_VIEW + body + cloth + _KEEP_ID_LOOK + _STYLE)
    side = ("参考图中是这个%s性。把%s**原地转向**：身体与头部一起转向画面左侧，呈现"
            "**90 度正侧脸**（能看清鼻梁与耳朵侧面轮廓），不要正面、不要回头看镜头。" % (g, he)
            + _FRAME_VIEW + body + cloth + _KEEP_ID_LOOK + _STYLE)
    back = ("参考图中是这个%s性。把%s**原地转过身去**：完全背对镜头，只能看到后脑勺、后颈、"
            "背部与衣服背面，不要出现脸部。" % (g, he)
            + _FRAME_VIEW + body + cloth + _KEEP_ID_LOOK + _STYLE)
    return face, front, side, back


def images_dir(root: Path) -> Path:
    return Path(root) / "images"


def sheets_dir(root: Path) -> Path:
    d = images_dir(root) / SHEET_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def piece_path(root: Path, name: str, which: str) -> Path:
    return sheets_dir(root) / f"{name}_{which}.png"


def sheet_path(root: Path) -> Path:
    """人工查看用的整张四宫格。"""
    return sheets_dir(root) / "sheet_4up.png"


def _uri(path: Path, max_side: int = 1400) -> str:
    """本地图 → data URI（该厂商只吃 URL / data URI，本地路径会 400）。"""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _save(img, path: Path) -> None:
    from PIL import Image  # noqa: F401

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=95)


def _fetch(url: str, timeout: int = 180):
    import httpx
    from PIL import Image

    with httpx.Client(timeout=timeout, trust_env=False) as c:
        r = c.get(url)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")


def _gen(prompt: str, ref_path: Path, ratio: str, size: str, out: Path, tag: str, log) -> bool:
    """生成一格并落盘。**两类供应商错误都要接住**（实测：只捕一种必然在另一种上崩）。"""
    uri = _uri(ref_path)
    for attempt in range(1, 5):
        try:
            _, url = providers.gen_image(prompt, refs=[uri], ratio=ratio)
            if url:
                _save(_fetch(url), out)
                log("[sheet] %s ok（%s）" % (tag, out.name))
                return True
        except providers.QueueFullError:
            log("[sheet] %s 队列满（第 %d 次）→ 30s 后重试" % (tag, attempt))
            time.sleep(30)
        except providers.RateLimitError:
            log("[sheet] %s 限流 429（第 %d 次）→ 70s 后重试" % (tag, attempt))
            time.sleep(70)
        except Exception as e:  # noqa: BLE001 -- 生图是概率性长任务，重试而非阻断
            log("[sheet] %s 第 %d 次失败：%s" % (tag, attempt, str(e)[:100]))
            time.sleep(10)
    return False


def _compose(root: Path, name: str, log) -> bool:
    """四格拼版：左=脸（大）＋ 右=三视图（等高）。落地两张：
    `_sheets/<名>.png`（人工查看）与 `images/<名>.png`（**绑进每镜的参考图**）。"""
    from PIL import Image

    face = piece_path(root, name, "face")
    views = [piece_path(root, name, k) for k in ("front", "side", "back")]
    if not (face.exists() and all(v.exists() for v in views)):
        return False
    try:
        TH = 1600
        fi = Image.open(face).convert("RGB")
        W, H = fi.size
        head = fi.crop((int(0.12 * W), int(0.03 * H), int(0.88 * W), int(0.72 * H)))
        left = head.resize((int(head.width * TH / head.height), TH), Image.LANCZOS)
        vs = []
        for v in views:
            im = Image.open(v).convert("RGB")
            vs.append(im.resize((int(im.width * TH / im.height), TH), Image.LANCZOS))
        gap = 20
        Wt = left.width + sum(x.width for x in vs) + gap * 5
        c = Image.new("RGB", (Wt, TH + gap * 2), (255, 255, 255))
        x = gap
        c.paste(left, (x, gap)); x += left.width + gap
        for im in vs:
            c.paste(im, (x, gap)); x += im.width + gap
        c.save(sheet_path(root))
        c.save(images_dir(root) / f"{name}.png")
        log("[sheet] %s 四宫格拼版完成（%d×%d）→ images/%s.png" % (name, c.width, c.height, name))
        return True
    except Exception as e:  # noqa: BLE001
        log("[sheet] %s 拼版失败：%s" % (name, str(e)[:100]))
        return False


def ensure(root: Path, name: str, source_path: Path, *, size: str | None = None,
           costume: str = "", log=print) -> Path | None:
    """确保该角色有一张四宫格设定表。返回拼好的 `images/<名>.png`（失败 ⇒ None）。

    `costume`：角色卡**外貌段**。给了 ⇒ **服装与配饰**跟卡片，脸与头发像素锁原图；
    不给 ⇒ 回落历史行为（黑T恤纯色衣）。

    幂等：**每一格**存在即跳过（改提示词后想重做，删对应的 `_sheets/<名>_<格>.png` 即可）。
    """
    src = Path(source_path)
    if not src.exists():
        log("[sheet] %s 源照片不存在：%s" % (name, src))
        return None

    # ★ **可用性守卫**（2026-09-19）：源照片太小/读不出 ⇒ 不足以承担身份锚点，
    #   跳过设定表、由调用方回落"直绑照片"（而不是白烧 4 张生成）。
    #   阈值 256px 短边：低于此的照片做参考图时脸只有几十像素，扩图/三视图都会崩。
    #   （附带好处：单测里的 64×64 占位图不会触发真实联网生成。）
    try:
        from PIL import Image as _I

        with _I.open(src) as _im:
            _w, _h = _im.size
        if min(_w, _h) < 256:
            log("[sheet] %s 源照片过小（%d×%d，短边 <256）⇒ 跳过设定表、回落直绑"
                % (name, _w, _h))
            return None
    except Exception as e:  # noqa: BLE001
        log("[sheet] %s 源照片读不出（%s）⇒ 跳过设定表、回落直绑" % (name, str(e)[:60]))
        return None

    # ★ C（2026-09-19）：**手供表优先** —— 用户自己做好四宫格放进
    #   `images/<名>.sheet.<ext>` 时，直接拿它当参考图，**一次生成都不花**，
    #   且版式完全由用户控制（用户自己在豆包等渠道已有产能，这是最省的路径）。
    for ext in ("jpg", "jpeg", "png", "webp"):
        cand = images_dir(root) / f"{name}.sheet.{ext}"
        if not cand.exists():
            continue
        try:
            from PIL import Image

            im = Image.open(cand).convert("RGB")
            im.save(images_dir(root) / f"{name}.png")        # 绑进每镜的参考图
            im.save(sheet_path(root))                        # 人工查看位
            log("[sheet] %s 使用**手供设定表** %s（%d×%d，不消耗生成）"
                % (name, cand.name, im.width, im.height))
            return images_dir(root) / f"{name}.png"
        except Exception as e:  # noqa: BLE001 -- 读不了就回落自动生成，不阻断
            log("[sheet] %s 手供表读取失败：%s → 回落自动生成" % (name, str(e)[:80]))
            break

    sz = size or config.IMAGE_SIZE or DEFAULT_SIZE
    sheets_dir(root)
    p_face, p_front, p_side, p_back = _prompts(costume)

    face = piece_path(root, name, "face")
    if not face.exists():
        # 源照片是方图就按 1:1 出（用 3:4 会把人物挤窄，实测踩过）
        ratio = "1:1"
        try:
            from PIL import Image
            im = Image.open(src)
            ratio = "3:4" if im.height > im.width * 1.15 else "1:1"
        except Exception:  # noqa: BLE001
            pass
        if not _gen(p_face, src, ratio, sz, face, "脸", log):
            return None
    else:
        log("[sheet] %s 脸已存在，跳过" % name)

    front = piece_path(root, name, "front")
    if not front.exists():
        if not _gen(p_front, face, "3:4", sz, front, "正视", log):
            return None
    side = piece_path(root, name, "side")
    if not side.exists():
        if not _gen(p_side, front, "3:4", sz, side, "侧视", log):
            return None
    back = piece_path(root, name, "back")
    if not back.exists():
        if not _gen(p_back, front, "3:4", sz, back, "背视", log):
            return None

    if not _compose(root, name, log):
        return None
    return images_dir(root) / f"{name}.png"
