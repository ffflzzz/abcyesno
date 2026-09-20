# Pavo 云端 API 外调 · 取证与客户端

> 结论先行：**可行，且不需要"截流转发"代理。**
> 该站点内部接口的鉴权**只有一个 `Authorization: Bearer <JWT>` 头**——
> 无签名头、无 nonce 校验、不绑定浏览器上下文。浏览器只需要**取一次令牌**，
> 之后任意服务端进程（Node / Python / 你的后端）都能直接调用，走你自己的积分。
>
> 取证时间：2026-09-18 · Chrome 153 · 站点 `app.pavo-ai.cn`

---

## 1. 决定性证据

在 `app.pavo-ai.cn` 页面上下文内做对照实验（`_tools` 侧脚本 `authprobe.mjs`）：

| 测试 | 条件 | 结果 |
|---|---|---|
| T1 | **只带 `Authorization`**，`credentials:'omit'`（**不带 Cookie**） | **200 + 真实业务数据** |
| T2 | 不带任何鉴权头 | 401 `{"code":"000501","message":"Login expired"}` |
| T3 | 伪造 JWT | 401（服务端**真校验签名**） |
| T4 | 只带令牌读项目资产 | 200（返回角色/场景/道具注册表） |

**浏览器之外**（真正的"外调"判据）：

```
DNS  api.pavo-ai.cn -> 183.253.56.121
Node  fetch + Bearer  -> HTTP 200 {"code":"000000","data":{"total_balance":200,...}}
Python urllib + Bearer -> HTTP 200（同一信封）
```

即：**无需浏览器、无需 Cookie、无需签名**。所谓"截流转发"是不必要的复杂化；
真正需要的只是**把令牌搬出来一次**。

> ⚠️ 本机 `curl` 走不通（沙箱代理变量会劫持，`--noproxy` 亦失败）。
> **用 Node 或 Python**，并在进程内清空代理变量（本客户端已内置）。

---

## 2. 鉴权模型

| 项 | 值 |
|---|---|
| API 基址 | `https://api.pavo-ai.cn/api` |
| 令牌来源 | 页面 `localStorage['token']`（HS256 JWT） |
| 请求头 | `Authorization: Bearer <JWT>`（必需） |
| 辅助头 | `X-Platform: 1`、`X-User-Language`、`X-App-Timezone`、`X-Client-Time-Ms` |
| **签名** | **无**（这四个头是普通上下文信息，非防重放签名） |
| 异常形制 | 未授权统一 `401 / code=000501`（`Login expired`，令牌过期也走这条） |
| 当前令牌 | `sub=bf5e8262-…`，签发 2026-09-08，**过期 2026-10-06 21:13** |

**取令牌**（浏览器侧一次性）：

```bash
node <chrome-debug>/cdp.mjs eval "localStorage.getItem('token')" > web/.pavo_token
```

令牌过期后重新登录再取一次即可（`POST /v1/user/login`）。

---

## 3. 关键端点（142 个端点全量见 `pavo-endpoints.json`）

### 生成（消耗积分）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/pixa/short-drama/segments/{sid}/video/generate` | 单镜出视频 |
| POST | `/v1/pixa/short-drama/segments/{sid}/keyframe/generate` | 单镜出关键帧 |
| POST | `/v1/pixa/short-drama/episodes/{eid}/segments/batch/video/generate` | **批量出视频（推荐）** |
| POST | `/v1/pixa/short-drama/episodes/{eid}/segments/batch/keyframe/generate` | 批量出关键帧 |
| POST | `/v1/pixa/short-drama/episodes/{eid}/compose` | 合成成片 |
| POST | `/v1/pixa/short-drama/export` | 导出 |

单镜请求体（自前端 bundle 反解，字段名逐字）：

```json
{ "model_code": "agnes-video-new", "style_id": "333482574429351936",
  "keyframe_production_mode": "single_frame",
  "upload_reference_image_url": "<关键帧 URL>",
  "custom_style_prompt": "", "resolution": "sd" }
```

⚠️ **`resolution` 必须落在该模型 `supported_resolutions` 内**，否则任务会
**先返回 `code=000000`（accepted）再 failed**，`error_code=050100`
（文案通用："无法处理此请求，请检查输入内容后重试"）——**不指出是哪个参数错**。
生成前先 `pavo_api.py spec <model_code>`。

**生成响应契约（2026-09-18 实证）**：

```json
{ "code": "000000", "message": "success",
  "data": { "task": { "task_id": "359308241892600448",
                      "task_type": "segment_video_generate",
                      "status": "pending",
                      "episode_id": "356461024926236672" } } }
```

**任务进度怎么查**（`GET /v1/pixa/short-drama/tasks/{id}` **不存在，404**）：

1. `GET /v1/pixa/short-drama/segments/{sid}/media?role=segment_video`
   —— 在跑的生成会以 **`kind:"task"` 占位条目**出现在同一列表里：
   `{"kind":"task","task_status":"processing"...}`；
   失败时该条带 `error_code` / `error_message`；成功后变成 `kind:"media"` 且带 `url`。
2. `GET /v1/pixa/short-drama/episodes/{eid}/storyboard/detail` 的 `active_tasks`
   + 项目 `progress` 的 `latest_task`（含 `error_code` / `error_message`）。

观测到的状态序列：`pending → waiting_1 → processing → (完成 | failed)`。

⚠️ **终态条目不会消失**：task 一旦进入 `failed`/`success`，它会**永久留在 media 列表里**
（失败条目还带 `error_message`）。因此判定"跑完了没"必须**先排除终态**，
否则上一轮的失败残留会让轮询**永不结束、把已成功的出片误报成 failed**
（`pavo_api.py` 的 `watch` 已修正；`TASK_TERMINAL` 集合）。

批量请求体：`{segment_ids:[...], style_id, model_code, keyframe_production_mode, resolution}`
——**一次请求渲多镜**，比逐镜发 N 个请求更省（一个任务一条台账）。

### 读（免费）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/pixa/short-drama/projects?page=1&page_size=50&is_demo=false` | 项目列表（字段 `title`/`episode_count`） |
| GET | `/v1/pixa/short-drama/projects/{pid}/episodes/storyboard` | 分集列表 |
| GET | `/v1/pixa/short-drama/episodes/{eid}/storyboard/detail` | **分镜正文**：`segments[].video_prompt_text` + `selected_keyframe_url` / `selected_video_url` + `active_tasks` |
| GET | `/v1/pixa/short-drama/projects/{pid}/progress?include_content=false` | 项目级 `style.style_id`、`ratio`、`latest_task` |
| GET | `/v1/pixa/short-drama/segments/{sid}/media?role=segment_video` | 该镜已产媒体（含 `is_selected`） |
| GET | `/v1/pixa/short-drama/segments/{sid}/reference-images` | 参考图绑定（跨镜一致性依据） |

### 计费

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/aigc/model-capability-spec?model_code=<mc>` | **模型约束**（`supported_resolutions` / `duration_seconds` / `mode_types` / `aspect_ratios`）。★ 注意是**查询参数**，不是路径段（拼成路径会 404） |
| GET | `/v1/pixa/mode_support_models?mode_code=<mode>` | 某模式支持的模型（同样是查询参数） |
| POST | `/v1/aigc/credits/estimate` | **不消耗积分的预估** |
| POST | `/v1/aigc/credits/calculate` | 同上，需 `model_type` |
| GET | `/v3/subscription/credits-balance` | 余额 |

估价请求体须**同时带 `model_type`**（只给 `model_code` 会回 `ModelType is required`）：

```json
{ "model_code": "agnes-video-new", "model_type": "video",
  "outputs": [{"media_type":"video","count":1,"duration_seconds":10,
               "gen_audio":false,"resolution":"sd"}] }
```

---

## 4. 模型清单、门槛与成本

### ★ 先看门槛：`subscription_level`（决定"能不能跑"的第一判据）

**权威目录是 `GET /v1/pixa/mode_support_models?mode_code=<m>`**，不是 `/v1/pixa/models`：
- 它**带 `subscription_level`**（准入门槛），
- 而且**目录更全**（video 模式 14 个，`/v1/pixa/models` 只有 8 个视频模型；
  连 `agnes-video-new-flash` 都只在这里出现）。

请求被门槛拦下时返回 **`code=067038`「当前会员等级未达到该模型门槛」**（实测）。

**本账号 `level = 0`。⇒ 真正能用的只有 3 个：**

| 模式 | 可用模型 |
|---|---|
| 视频 | `agnes-video-new-flash`（0 积分）、`agnes-video-new`（2 积分） |
| 图片 | `agnes-image`（0 积分） |

**其余全部 `subscription_level = 1`（需订阅会员）**，与积分多少无关：

| 模式 | 被门槛拦下的模型（level=1） |
|---|---|
| video（共 14） | seedance-2-5 · seedance-2-0 · seedance-2-0-fast · seedance-2-0-mini · seedance-1-5-pro · seedance-1-0-pro · wan-3-0 · wan-3-0-prime · wan-2-7 · happyhorse-1-1 · happyhorse-1-0 · minimax-h3 |
| image（共 13） | gpt-image-2 · gpt-image-2-5-flare · gpt-image-2-5-sunburst · seedream5-0 · seedream5-0-pro · seedream4-5 · seedream4-0 · nano2 · nano2-lite · nanopro · qwen-image-3 · qwen-image-3-pro |

> ⚠️ **修正一处早期误判**：先前据 `exclusive_credits.applicable_model_codes` 推断
> "付费模型会被余额拒绝"。实测 `seedance-2-5` 被拒的真实原因是 **会员等级门槛（067038）**，
> 与余额无关。额度结构仍会影响**等级 1 之后**能花哪些分，但**当前的第一拦路虎是等级**。

### 价位与分辨率（`pavo_api.py models --full`，2026-09-18 实测）

`GET /v1/pixa/models` 返回 **17 个**（全部 `is_online: true`）。计费按**输出条数**，
`duration_seconds` 不改变 `billable_units`。

> ★ 下表中**除 `agnes-video-new` 外，视频与图片类全部 `subscription_level = 1`**，
> 本账号（level 0）跑不了。价位列出来是给"升级后"做预算参考。

### 视频（8）

| model_code | 积分 | 可用分辨率 | 时长 | 别名 |
|---|---|---|---|---|
| `agnes-video-new` | 2 | hd/fhd/qhd | 4-12s | Agnes Video 2.5 |
| `seedance-2-0-mini` | 2 | sd/hd | 4-15s | Seedance 2.0 Mini |
| `wan-3-0` | 5 | sd/hd/fhd | 2-30s | Wan 3.0 |
| `happyhorse-1-1` | 7 | hd/uhd | 3-15s | HappyHorse 1.1 |
| `wan-3-0-prime` | 7 | sd/hd/fhd | 2-30s | Wan 3.0 Prime |
| `seedance-2-0` | 9 | sd/hd/fhd/uhd | 4-15s | Seedance 2.0 |
| `wan-2-7` | 12 | hd/uhd | 2-15s | Wan 2.7 |
| `seedance-2-5` | 14 | sd/hd/fhd | 4-30s | Seedance 2.5 |

**另有一个不在列表但可用且免费的**：`agnes-video-new-flash` —— 0 积分，**仅 `hd`**，4-12s。
`model-capability-spec` 报 `is_online: true`，实测已出片（见 §6）。

### 图片（8）

| model_code | 积分 | 可用分辨率 | 别名 |
|---|---|---|---|
| `agnes-image` | **0** | sd/hd/uhd | Agnes Image 2.5 Flash |
| `qwen-image-3-pro` | 4 | sd/hd | Qwen Image 3 Pro |
| `gpt-image-2-5-flare` | 5 | sd/hd/uhd | 全能图片G2.5 Flare |
| `gpt-image-2-5-sunburst` | 5 | sd/hd/uhd | 全能图片G2.5 Sunburst |
| `seedream5-0` | 5 | hd/uhd | Seedream 5.0 Lite |
| `seedream5-0-pro` | 6 | sd/hd | Seedream 5.0 Pro |
| `gpt-image-2` | 7 | sd/hd/uhd | 全能图片G2 |
| `nano2` | 9 | sd/hd/uhd | NB 2 |

### 音频（1）

| model_code | 积分 | 模式 | 别名 |
|---|---|---|---|
| `minimax-music-3-0` | 估价未返回（audio 载荷形状未探明） | short_drama | MiniMax Music 3.0 |

### 额度结构（等级 1 之后才会用到）

余额 **200**，结构是 `exclusive_balance: 200` 且
**`applicable_model_codes: ["agnes-video-new"]`**；`general_balance` / `subscription_credits`
/ `permanent_balance` **均为 0**。

⇒ 各档的实际可用性：
- **0 积分模型**（`agnes-video-new-flash`、`agnes-image`）——可直接跑（flash 已实证出片）；
- **`agnes-video-new`（2/镜）**——由这 200 专属额度覆盖，约 100 镜；
- **其余付费模型**——**先被等级门槛拦下（067038）**；即便升级到等级 1，
  200 分专属额度也不适用于它们、通用额度为 0，**仍需另充通用/订阅额度**
  （升级后是否能透支，未实测）。


---

## 5. 用法

```bash
cd web
python pavo_api.py balance
python pavo_api.py models --full                     # 全模型 + 价位 + 可用分辨率
python pavo_api.py projects
python pavo_api.py episodes <project_id>
python pavo_api.py segments <episode_id> --prompt
python pavo_api.py estimate agnes-video-new --count 1 --dur 10
python pavo_api.py spec agnes-video-new-flash      # ★ 生成前必查 supported_resolutions
python pavo_api.py media <segment_id> --role segment_video
python pavo_api.py refs <segment_id>

# 生成类必须显式 --yes（否则拒绝执行）
python pavo_api.py gen-batch <episode_id> --segment-ids A,B,C --kind video \
       --model agnes-video-new --res hd --project-id <pid> --yes
python pavo_api.py gen-video <segment_id> --model agnes-video-new-flash --res hd \
       --episode-id <eid> --project-id <pid> --yes
python pavo_api.py watch <segment_id>              # 盯出片直到任务离开队列
python pavo_api.py compose <episode_id> --yes
python pavo_api.py poll <episode_id>
```

令牌优先级：`--token` > 环境变量 `PAVO_TOKEN` > `web/.pavo_token`。

---

## 6. 边界与已知缺口

1. **浏览器跨域直调未验证**。`Access-Control-Allow-Origin` 属 CORS 安全响应头，
   JS 读不到（实测 `headers.get()` 返回 `null`，**不能据此判定"无 CORS"**）。
   若要让 `web/` 页面**在浏览器内**直接打云端，需实测；被挡就加一个本地中转
   （同源 shim 已经在 `/v1/pixa/*` 上做了转发，是最自然的落点）。
   **服务端调用不受 CORS 约束**，已证实可用。
2. ~~生成接口响应体未实证~~ → **已实证**（见 §3），单镜 `hd` 免费模型**已跑通到出片**。
3. **`web/assets/js/core/api.js` 的批量路径与云端不一致**：
   该文件 `generateMedia()` 拼的是 `/v1/pixa/short-drama/segments/batch/{kind}/generate`，
   而云端真实路径是 `/v1/pixa/short-drama/episodes/{eid}/segments/batch/{kind}/generate`。
   对自建后端（`driver=http`）无影响，但**指向云端会 404**。
   另：它 `estimateCredits` 只传 `model_code`+`outputs`，**缺 `model_type`**
   → 云端会回 `000101 ModelType is required`。
4. **`050100` 是"参数/模型校验失败"的通用码**，不指明具体字段。本轮踩到的是
   `resolution` 越界。⇒ 见到 050100 先核对 `spec` 里的 `supported_resolutions`。
5. **令牌 = 账号凭证**：泄露即账号被接管（无二次校验）。
   `.pavo_token` 不应进版本库（已在 `.gitignore`）。
6. **无签名/无防重放**意味着第三方可重放；同理，任何拿到令牌的脚本都能**花光积分**。
   批量生成前**先用 `estimate` 估价**。
7. 遵守平台服务条款，风险自负。
