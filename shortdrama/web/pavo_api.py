#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pavo_api.py — Pavo 短剧平台 API 外部直调客户端

背景：官方未提供开放 API。经取证，其内部接口鉴权**仅依赖
`Authorization: Bearer <JWT>`**——无签名头、无 nonce 校验、不绑定浏览器
上下文（无 Cookie 依赖）。令牌取自浏览器 localStorage['token']，因此
**无需"截流转发"代理**：一次取令牌，任意服务端进程即可直接调用。

已验证（2026-09-18，Chrome 153）：
  - 仅带 Authorization（credentials=omit）→ 200 + 真实业务数据
  - 无令牌 / 伪造令牌 → 401 {"code":"000501","message":"Login expired"}
  - 普通 python 进程直连 183.253.56.121 → 200
  - **单镜 `hd` 免费模型已跑通到出片**（agnes-video-new-flash，0 积分）

★ 生成前必查 `spec <model_code>`：
  `resolution` 必须落在该模型 `supported_resolutions` 内，否则任务会
  **先 accepted（code=000000）再 failed**，`error_code=050100`（文案通用、不指向具体参数）。
  实测事故：agnes-video-new-flash 只支持 `hd`，传 `sd` 即失败。

任务进度查法（`GET /tasks/{id}` 不存在，404）：
  `segments/{sid}/media?role=segment_video` 里会有 `kind:"task"` 占位条目，
  失败带 `error_code`/`error_message`，成功变 `kind:"media"` 且带 `url`。

取令牌（浏览器侧一次性操作）：
  node <chrome-debug>/cdp.mjs eval "localStorage.getItem('token')" > .pavo_token

用法：
  python pavo_api.py balance
  python pavo_api.py models
  python pavo_api.py projects
  python pavo_api.py episodes <project_id>
  python pavo_api.py episode <episode_id>
  python pavo_api.py segments <episode_id> [--prompt]
  python pavo_api.py estimate <model_code> --count 1 --dur 10 [--res human]
  python pavo_api.py media <segment_id> --role segment_video
  python pavo_api.py refs <segment_id>
  python pavo_api.py gen-keyframe <segment_id> [--model agnes-image] --yes
  python pavo_api.py gen-video   <segment_id> [--model agnes-video-new] [--ref <keyframe_url>] --yes
  python pavo_api.py gen-batch <episode_id> --segment-ids A,B,C --kind video --project-id <pid> --yes
  python pavo_api.py compose <episode_id> --yes
  python pavo_api.py poll <episode_id> [--interval 10] [--timeout 900]

⚠️ 生成类命令会真实消耗账号积分，除 --yes 外一律拒绝执行。
⚠️ 注意遵守平台服务条款；令牌等同账号凭证，泄露即等于账号被接管。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.pavo-ai.cn/api"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(TOOLS_DIR, ".pavo_token")

# 直连：清空代理，避免环境代理变量劫持（本机 curl 走代理会失败，urllib 直连可用）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_token(explicit=None):
    tok = explicit or os.environ.get("PAVO_TOKEN")
    if not tok and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
    if not tok:
        sys.exit("ERROR: 未找到令牌。请用 --token / PAVO_TOKEN / _tools/.pavo_token 提供。")
    return tok.strip().strip('"')


def call(method, path, token, body=None, timeout=60):
    """返回 (http_status, parsed_json_or_text)"""
    url = API + path if path.startswith("/") else API + "/" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("X-Platform", "1")
    req.add_header("X-User-Language", "zh")
    req.add_header("X-App-Timezone", "Asia/Shanghai")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def show(status, payload, limit=None):
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:limit] if limit else
          json.dumps(payload, ensure_ascii=False, indent=2))
    return status


def ok(status, payload):
    """业务码判断：000000 = success；401/000501 = 令牌失效"""
    if status == 401 or (isinstance(payload, dict) and payload.get("code") == "000501"):
        sys.exit("ERROR: 令牌失效或无效（Login expired）。请重新从浏览器取 localStorage['token']。")
    return payload


# ---------- 只读 ----------

def cmd_balance(a, tok):
    p = ok(*call("GET", "/v3/subscription/credits-balance", tok))
    d = p.get("data", {})
    print(f"总积分: {d.get('total_balance')}  永久: {d.get('permanent_balance')}  "
          f"时效: {d.get('time_sensitive_balance')}  订阅: {d.get('subscription_credits')}")
    for e in d.get("exclusive_credits", []) or []:
        print(f"  专属额度: {e.get('remaining')} 适用模型: {', '.join(e.get('applicable_model_codes', []))}")


def cmd_models(a, tok):
    """列模型。
    默认：`/v1/pixa/models`（17 个，**不含** flash，也不含等级门槛）。
    --full：附价位与可用分辨率。
    --mode <m>：★ **权威视图** —— `mode_support_models?mode_code=`，带
                `subscription_level`（准入门槛）与 tags，且**目录比 /v1/pixa/models 全**。
    """
    if a.mode:
        p = ok(*call("GET", f"/v1/pixa/mode_support_models?mode_code={urllib.parse.quote(a.mode)}", tok))
        ms = (p.get("data") or {}).get("models") or []
        mine = a.level
        if mine is None:      # 未指定则按账号真实等级判定
            try:
                mine = call("GET", "/v3/subscription/credits-balance", tok)[1].get("data", {}).get("level", 0)
            except Exception:
                mine = 0
        print(f"mode_code={a.mode}  共 {len(ms)} 个   你当前等级 level={mine}")
        print(f"{'model_code':<24}{'门槛':>5}  {'可用?':<8}{'标签':<20}name")
        for m in sorted(ms, key=lambda x: (x.get("subscription_level") or 0, x.get("code") or "")):
            lv = m.get("subscription_level")
            tags = ",".join(t.get("label", "") for t in (m.get("tags") or []))
            if lv is None:
                verdict = "?"
            elif lv <= mine:
                verdict = "可用"
            else:
                verdict = f"需等级{lv}"
            print(f"{m.get('code'):<24}{('-' if lv is None else lv):>5}  {verdict:<8}{tags:<20}{m.get('name')}")
        print("\n★ 注意：本视图列出的模型**多于** /v1/pixa/models —— "
              "后者不是完整目录，且不体现门槛。判断能不能跑，以本视图的 subscription_level 为准。")
        return

    p = ok(*call("GET", "/v1/pixa/models", tok))
    ms = p.get("data", {}).get("models", [])
    if not a.full:
        for m in ms:
            print(f"{m.get('model_code'):<26} {m.get('model_type'):<7} {m.get('model_alias')}")
        print(f"\n共 {len(ms)} 个。`--full` 看价位/分辨率；"
              f"`--mode generate_video` 看**门槛与完整目录**"
              f"（flash 不在此列表但免费可用）。")
        return
    rows = []
    for m in ms:
        mc, mt = m.get("model_code"), m.get("model_type")
        spec = call("GET", f"/v1/aigc/model-capability-spec?model_code={urllib.parse.quote(mc)}", tok)[1]
        md = (spec.get("data") or {}).get("model") or {}
        v = md.get("video") or md.get("image") or {}
        res = v.get("supported_resolutions") or []
        dur = v.get("duration_seconds") or []
        if mt == "video":
            outs = [{"media_type": "video", "count": 1, "gen_audio": False,
                     "duration_seconds": min(dur) if dur else 5, "resolution": res[0] if res else "hd"}]
        elif mt == "image":
            outs = [{"media_type": "image", "count": 1, "resolution": "sd"}]
        else:
            outs = [{"media_type": mt, "count": 1}]
        ed = (call("POST", "/v1/aigc/credits/estimate", tok,
                   {"model_code": mc, "model_type": mt, "outputs": outs})[1].get("data") or {})
        rows.append((mt, mc, m.get("model_alias"), ed.get("required_credits"),
                     "/".join(res), f"{min(dur)}-{max(dur)}s" if dur else ""))
    for typ in ("video", "image", "audio"):
        sub = [r for r in rows if r[0] == typ]
        if not sub:
            continue
        print(f"\n== {typ} ==")
        print(f"{'model_code':<24}{'积分':>5}  {'分辨率':<18}{'时长':<9}alias")
        for _, mc, alias, cr, res, dur in sorted(sub, key=lambda r: (r[3] is None, r[3])):
            print(f"{mc:<24}{('-' if cr is None else cr):>5}  {res:<18}{dur:<9}{alias}")


def cmd_projects(a, tok):
    p = ok(*call("GET", "/v1/pixa/short-drama/projects?page=1&page_size=50&is_demo=false", tok))
    items = (p.get("data", {}).get("items") or [])
    for it in items:
        print(f"{it.get('project_id')}  {it.get('title')}  状态={it.get('status')}  "
              f"集数={it.get('episode_count')}  类型={it.get('project_type')}")
    if not items:
        print("（无项目）")


def cmd_episodes(a, tok):
    """列出某项目下的分集，给出 episode_id（后续命令都要它）"""
    p = ok(*call("GET", f"/v1/pixa/short-drama/projects/{a.project_id}/episodes/storyboard", tok))
    d = p.get("data")
    items = d if isinstance(d, list) else (d or {}).get("items") or (d or {}).get("episodes") or []
    for it in items:
        print(f"{it.get('episode_id')}  第{it.get('episode_no')}集  {it.get('title')}  "
              f"phase={it.get('storyboard_phase')}  镜数={it.get('segment_count')}")
    if not items:
        print("（无分集 / 字段结构与预期不符，原始响应见 --raw）")
        print(json.dumps(d, ensure_ascii=False)[:600])


def cmd_episode(a, tok):
    p = ok(*call("GET", f"/v1/pixa/short-drama/episodes/{a.episode_id}/storyboard/detail", tok))
    d = p.get("data", {})
    print(f"集: {d.get('title')}  ratio={d.get('ratio')}  phase={d.get('storyboard_phase')}  "
          f"镜数={len(d.get('segments', []))}")
    print(f"active_tasks: {json.dumps(d.get('active_tasks'), ensure_ascii=False)}")


def cmd_segments(a, tok):
    p = ok(*call("GET", f"/v1/pixa/short-drama/episodes/{a.episode_id}/storyboard/detail", tok))
    for s in p.get("data", {}).get("segments", []):
        kf = "有" if s.get("selected_keyframe_url") else "无"
        vd = "有" if s.get("selected_video_url") else "无"
        dur = (s.get("estimated_duration_ms") or 0) / 1000
        print(f"#{s.get('display_order')} {s.get('segment_id')} {s.get('title')}  "
              f"{dur:g}s  关键帧={kf} 视频={vd}")
        if a.prompt and s.get("video_prompt_text"):
            print("     prompt: " + s["video_prompt_text"].replace("\n", " | ")[:300])


def cmd_estimate(a, tok):
    body = {
        "model_code": a.model_code,
        "model_type": a.model_type,
        "outputs": [{
            "media_type": a.media_type,
            "count": a.count,
            "duration_seconds": a.dur,
            "gen_audio": False,
            "resolution": a.res,
        }],
    }
    p = ok(*call("POST", "/v1/aigc/credits/estimate", tok, body))
    d = p.get("data", {})
    print(f"{a.model_code}: 单价={d.get('unit_price')} 计费单位={d.get('billable_units')} "
          f"→ 需要积分={d.get('required_credits')}  (count={a.count}, {a.dur}s, {a.res})")


# ---------- 生成（需 --yes） ----------

def _require_yes(a):
    if not a.yes:
        sys.exit(f"拒绝执行：{a.cmd} 会真实消耗积分/改写项目。确认后加 --yes。")


def cmd_gen_keyframe(a, tok):
    _require_yes(a)
    style_id = a.style_id or _project_style_id(a, tok)
    body = {
        "model_code": a.model,
        "style_id": style_id,
        "keyframe_production_mode": "single_frame",
        "custom_style_prompt": a.style_prompt or "",
        "resolution": a.res,
    }
    st, p = call("POST", f"/v1/pixa/short-drama/segments/{a.segment_id}/keyframe/generate", tok, body)
    print(f"http={st}")
    print(json.dumps(p, ensure_ascii=False, indent=2))


def cmd_gen_video(a, tok):
    _require_yes(a)
    style_id = a.style_id or _project_style_id(a, tok)
    ref = a.ref
    if not ref and a.episode_id:
        _, p = call("GET", f"/v1/pixa/short-drama/episodes/{a.episode_id}/storyboard/detail", tok)
        for s in p.get("data", {}).get("segments", []):
            if str(s.get("segment_id")) == str(a.segment_id):
                ref = s.get("selected_keyframe_url")
    if not ref:
        print("WARN: 未提供参考图（--ref / --episode-id 自动取关键帧），视频一致性可能下降。")
    body = {
        "model_code": a.model,
        "style_id": style_id,
        "keyframe_production_mode": "single_frame",
        "upload_reference_image_url": ref,
        "custom_style_prompt": a.style_prompt or "",
        "resolution": a.res,
    }
    st, p = call("POST", f"/v1/pixa/short-drama/segments/{a.segment_id}/video/generate", tok, body)
    print(f"http={st}")
    print(json.dumps(p, ensure_ascii=False, indent=2))


def _project_style_id(a, tok):
    if not a.project_id:
        sys.exit("需要 --project-id（或 --style-id）以解析项目风格。")
    p = call("GET", f"/v1/pixa/short-drama/projects/{a.project_id}/progress?include_content=false", tok)[1]
    sid = (p.get("data", {}).get("style") or {}).get("style_id")
    if not sid:
        sys.exit("项目未设置风格（style_id 为空）。")
    print(f"[info] 解析到项目风格 style_id={sid}")
    return sid


def cmd_gen_batch(a, tok):
    """批量生成：**一次请求渲多镜**（后端按 segment_ids 起一个任务）
    真实路径：POST /v1/pixa/short-drama/episodes/{eid}/segments/batch/{keyframe|video}/generate
    """
    _require_yes(a)
    style_id = a.style_id or _project_style_id(a, tok)
    seg_ids = [s for s in a.segment_ids.split(",") if s.strip()]
    if not seg_ids:
        sys.exit("需要 --segment-ids 逗号分隔的镜号列表。")
    body = {
        "segment_ids": seg_ids,
        "style_id": style_id,
        "model_code": a.model,
        "keyframe_production_mode": "single_frame",
        "resolution": a.res,
    }
    st, p = call("POST", f"/v1/pixa/short-drama/episodes/{a.episode_id}"
                         f"/segments/batch/{a.kind}/generate", tok, body)
    print(f"http={st}  提交 {len(seg_ids)} 镜  kind={a.kind}  model={a.model}")
    print(json.dumps(p, ensure_ascii=False, indent=2))


def cmd_spec(a, tok):
    """模型能力规格：**生成前必查**。
    ★ 2026-09-18 实测事故：agnes-video-new-flash 的 supported_resolutions **只有 ["hd"]**，
      传 resolution="sd" 时生成任务返回 code=000000 accepted、随后 failed
      error_code=050100（信息通用，不指出是哪个参数错）——**耗时且误导**。
    """
    q = urllib.parse.quote(a.model_code)
    p = ok(*call("GET", f"/v1/aigc/model-capability-spec?model_code={q}", tok))
    d = p.get("data", {})
    m = d.get("model") or {}
    print(f"{m.get('model_code')}  type={m.get('model_type')}  is_online={m.get('is_online')}")
    v = m.get("video") or m.get("image") or {}
    if v.get("supported_resolutions") is not None:
        print(f"  supported_resolutions = {v.get('supported_resolutions')}   ← 必须落在此集合内")
    if v.get("duration_seconds") is not None:
        print(f"  duration_seconds      = {v.get('duration_seconds')}")
    if v.get("mode_types") is not None:
        print(f"  mode_types            = {v.get('mode_types')}")
    if v.get("aspect_ratios") is not None:
        print(f"  aspect_ratios         = {v.get('aspect_ratios')}")
    if not v:
        print("  " + json.dumps(d, ensure_ascii=False)[:600])


# 任务终态：**终止后再也不会从 media 列表里消失**（会一直挂着，带着 error_message）。
# ★ 2026-09-18 踩到：脚本用"列表里还有 kind='task' 就当没结束"→
#   上一轮失败的残留条目让轮询**永远不结束**，把已成功的出片误报成 failed。
TASK_TERMINAL = {"failed", "success", "succeeded", "cancelled", "canceled", "timeout"}


def _active_tasks(items):
    return [i for i in items
            if i.get("kind") == "task" and i.get("task_status") not in TASK_TERMINAL]


def cmd_watch(a, tok):
    """盯某一镜的出片：轮询直到**活跃**任务清空（陈旧终态条目不算）
    任务以 kind='task' 条目挂在 media 列表里；成功后变 kind='media' 且带 url。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < a.timeout:
        p = call("GET", f"/v1/pixa/short-drama/segments/{a.segment_id}/media?role={a.role}", tok)[1]
        its = p.get("data", {}).get("items") or []
        act = _active_tasks(its)
        media = [i for i in its if i.get("kind") == "media" and i.get("url")]
        line = f"active={[x.get('task_status') for x in act] or 'none'} 已出片={len(media)}"
        if line != last:
            print(f"[{int(time.time() - t0):>4}s] {line}")
            last = line
        if not act:
            term = [i for i in its if i.get("kind") == "task"]
            for t in term:
                print(f"  (终态) {t.get('generation_task_id')} {t.get('task_status')} "
                      f"{t.get('error_code') or ''} {t.get('error_message') or ''}".rstrip())
            for x in sorted(media, key=lambda z: z.get("created_at") or ""):
                print(f"  media_id={x.get('media_id')} selected={x.get('is_selected')} "
                      f"model={x.get('model_code')} {x.get('created_at')}")
                print(f"    url={x.get('url')}")
            return
        time.sleep(a.interval)
    print("轮询超时（任务可能仍在跑）。")


def cmd_media(a, tok):
    """查某镜已产出的媒体（role=segment_keyframe / segment_video）"""
    p = ok(*call("GET", f"/v1/pixa/short-drama/segments/{a.segment_id}/media?role={a.role}", tok))
    for it in (p.get("data", {}).get("items") or []):
        flag = "★选中" if it.get("is_selected") else "  备选"
        print(f"{flag} {it.get('media_id')} {it.get('model_code')} {it.get('created_at')}")
        print(f"       {it.get('url')}")


def cmd_refs(a, tok):
    """查某镜的参考图绑定（跨镜一致性的依据）"""
    p = ok(*call("GET", f"/v1/pixa/short-drama/segments/{a.segment_id}/reference-images", tok))
    print(json.dumps(p.get("data"), ensure_ascii=False, indent=2)[:1500])


def cmd_compose(a, tok):
    """合成成片：POST /v1/pixa/short-drama/episodes/{eid}/compose"""
    _require_yes(a)
    st, p = call("POST", f"/v1/pixa/short-drama/episodes/{a.episode_id}/compose", tok)
    print(f"http={st}")
    print(json.dumps(p, ensure_ascii=False, indent=2))


def cmd_poll(a, tok):
    """轮询分集分镜，报告关键帧/视频产出进度"""
    t0 = time.time()
    last = None
    while time.time() - t0 < a.timeout:
        _, p = call("GET", f"/v1/pixa/short-drama/episodes/{a.episode_id}/storyboard/detail", tok)
        segs = p.get("data", {}).get("segments", [])
        tasks = p.get("data", {}).get("active_tasks") or []
        line = (f"关键帧 {sum(1 for s in segs if s.get('selected_keyframe_url'))}/{len(segs)}  "
                f"视频 {sum(1 for s in segs if s.get('selected_video_url'))}/{len(segs)}  "
                f"在跑任务 {len(tasks)}")
        if line != last:
            print(f"[{int(time.time() - t0):>4}s] {line}")
            last = line
        if not tasks and all(s.get("selected_video_url") for s in segs) and segs:
            print("全部镜头已有视频，完成。")
            return
        time.sleep(a.interval)
    print("轮询超时。")


def main():
    ap = argparse.ArgumentParser(description="Pavo 短剧平台 API 直调客户端")
    ap.add_argument("--token")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("balance")
    mm = sub.add_parser("models"); mm.add_argument("--full", action="store_true",
                                                   help="附带价位与可用分辨率（只读，不消耗积分）")
    mm.add_argument("--mode", help="权威视图：按模式列模型并标订阅门槛，如 generate_video / generate_image / short_drama")
    mm.add_argument("--level", type=int, default=None, help="覆盖账号等级（默认取 credits-balance.level）")
    sub.add_parser("projects")

    eps = sub.add_parser("episodes"); eps.add_argument("project_id")
    e = sub.add_parser("episode"); e.add_argument("episode_id")
    s = sub.add_parser("segments"); s.add_argument("episode_id"); s.add_argument("--prompt", action="store_true")

    es = sub.add_parser("estimate")
    es.add_argument("model_code")
    es.add_argument("--model-type", default="video")
    es.add_argument("--media-type", default="video")
    es.add_argument("--count", type=int, default=1)
    es.add_argument("--dur", type=int, default=10)
    es.add_argument("--res", default="sd")

    for name, help_text in (("gen-keyframe", "生成关键帧"), ("gen-video", "生成视频")):
        g = sub.add_parser(name, help=help_text)
        g.add_argument("segment_id")
        g.add_argument("--model", default="agnes-video-new" if name == "gen-video" else "agnes-image")
        g.add_argument("--res", default="sd")
        g.add_argument("--style-id")
        g.add_argument("--style-prompt")
        g.add_argument("--project-id")
        g.add_argument("--episode-id")
        g.add_argument("--ref", help="参考图 URL（视频生成用）")
        g.add_argument("--yes", action="store_true")

    pl = sub.add_parser("poll")
    pl.add_argument("episode_id")
    pl.add_argument("--interval", type=int, default=10)
    pl.add_argument("--timeout", type=int, default=900)

    # 批量生成（推荐路径：一次请求渲多镜）
    gb = sub.add_parser("gen-batch", help="批量生成关键帧/视频")
    gb.add_argument("episode_id")
    gb.add_argument("--segment-ids", required=True, help="逗号分隔的 segment_id 列表")
    gb.add_argument("--kind", choices=["keyframe", "video"], default="video")
    gb.add_argument("--model", default="agnes-video-new")
    gb.add_argument("--res", default="sd")
    gb.add_argument("--style-id")
    gb.add_argument("--project-id")
    gb.add_argument("--yes", action="store_true")

    md = sub.add_parser("media", help="查某镜已产出的媒体")
    md.add_argument("segment_id")
    md.add_argument("--role", default="segment_video",
                    choices=["segment_video", "segment_keyframe"])

    sp = sub.add_parser("spec", help="模型能力规格（生成前必查：supported_resolutions）")
    sp.add_argument("model_code")

    wt = sub.add_parser("watch", help="盯某镜出片，直到任务离开队列")
    wt.add_argument("segment_id")
    wt.add_argument("--role", default="segment_video",
                    choices=["segment_video", "segment_keyframe"])
    wt.add_argument("--interval", type=int, default=10)
    wt.add_argument("--timeout", type=int, default=900)

    rf = sub.add_parser("refs", help="查某镜的参考图绑定")
    rf.add_argument("segment_id")

    cp = sub.add_parser("compose", help="合成成片")
    cp.add_argument("episode_id")
    cp.add_argument("--yes", action="store_true")

    a = ap.parse_args()
    tok = load_token(a.token)
    {"balance": cmd_balance, "models": cmd_models, "projects": cmd_projects,
     "episodes": cmd_episodes,
     "episode": cmd_episode, "segments": cmd_segments, "estimate": cmd_estimate,
     "gen-keyframe": cmd_gen_keyframe, "gen-video": cmd_gen_video,
     "gen-batch": cmd_gen_batch, "media": cmd_media, "refs": cmd_refs,
     "spec": cmd_spec, "watch": cmd_watch,
     "compose": cmd_compose, "poll": cmd_poll}[a.cmd](a, tok)


if __name__ == "__main__":
    main()
