# Composer 真实功能 Spec（限定范围）

> 版本：v1（2026-07-20）
> 背景：上一轮已完成 Kimi 式 Composer 布局重构，但 🎤 为 disabled 占位、🛡 权限选择器为纯本地 state 假开关。
> 本轮目标：**去掉占位，先做限定范围的 spec，再按 spec 完成真实可落地的开发**。

## 1. 范围限定（关键决策）

调研后端真实能力后，对原布局里的两个占位功能做如下范围裁剪：

| 功能 | 原布局意图 | Hermes / Agnes 真实能力 | 本 spec 落地范围 |
|------|-----------|------------------------|----------------|
| 🎤 语音 | 语音输入 | Agnes 有 OpenAI 兼容 `/audio/transcriptions` STT 接口；前端当前零实现 | **真实实现**：浏览器录音 → 转写 → 回填输入框 |
| 🛡 权限 | strict / default / yolo 三档 | Hermes 只有 `ask`（默认，危险才问）/ `off`（=yolo 全不问）两种真实行为；**没有「每调用必问」的 strict 原生模式** | **砍掉 strict**；只做 `default`（=不启用 yolo）/ `yolo`（=启用 session yolo 旁路）两档真实切换 |

不做（本轮明确排除）：
- 语音 TTS 播报（无后端能力依据，纯前端合成不在本期）。
- strict 模式（Hermes 无原生支持，强做只能沦为前端假开关，违背「不要占位」）。
- 权限的全局（global）作用域切到（只切当前 session，与 TUI Shift+Tab 等价）。

## 2. 功能一：语音输入（STT）

### 2.1 端到端链路

```
Composer (MediaRecorder 录 webm)
  → 录音 blob → base64 字符串（FileReader.readAsDataURL 去前缀）
  → window.hermes.transcribeAudio(b64, mime)
  → preload: ipcRenderer.invoke('get-agui-port') + fetch POST /api/transcribe {audio, mime}
  → agui-server POST /api/transcribe
      读 HERMES_HOME/.env 的 AGNES_API_KEY
      → fetch Agnes https://apihub.agnes-ai.com/v1/audio/transcriptions (multipart, Bearer)
      返回 { text }
  → Composer 把 text 追加进 textarea（不自动发送）
```

### 2.2 接口签名

- **前端**：`window.hermes.transcribeAudio(audioBase64: string, mime: string) => Promise<{ text: string } | { error: string }>`
- **preload.js**：新增 `transcribeAudio`，内部取 agui port 后 `fetch('http://localhost:${port}/api/transcribe', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({audio, mime}) })`
- **agui-server.js**：新增 `POST /api/transcribe`，body `{ audio: base64, mime }`，解码为 Buffer → 用 Node 全局 `FormData`+`Blob` 转发 multipart 到 Agnes，返回 `{ text }` 或 `{ error }`
- **Agnes**：`POST /v1/audio/transcriptions`，form-data 字段 `file`（音频）、`model`（固定 `agnes-2.0-flash` 或不传，由 Agnes 默认），Header `Authorization: Bearer <AGNES_API_KEY>`

### 2.3 改动文件

| 文件 | 改动 |
|------|------|
| `src/components/Composer.jsx` | ① 去掉 🎤 的 `disabled`；② 加录音状态 `recording`；③ 点 🎤 开始/停止 `MediaRecorder`；④ 停录后 `readAsDataURL` → `transcribeAudio` → 文本回填 `text`；⑤ 录音中禁用发送、显示红点/计时 |
| `electron/preload.js` | 新增 `transcribeAudio` 方法 |
| `electron/backend/agui-server.js` | 新增 `POST /api/transcribe` 端点 + 一个读 `.env` 取 key 的 helper |

### 2.4 边界与降级

- 浏览器不支持 `MediaRecorder` → 按钮保持禁用并提示。
- 转写失败（无 key / 网络错 / 非音频）→ Composer 显示错误 toast 文本，不影响输入框。
- 录音期间 `disabled`（发送/停）语义：录音时用独立停止按钮，不触发对话发送。
- 浏览器默认产 `audio/webm;codecs=opus`；Agnes 是 OpenAI 兼容 Whisper 协议，接受 webm。若真机返回格式错，后续可在 agui-server 加转码，本期不内置。

## 3. 功能二：权限模式（default / yolo）

### 3.1 端到端链路

```
Composer 选 default | yolo
  → onPermissionChange(mode)
  → App: window.hermes.setPermissionMode(mode, selectedSessionId)
  → preload: ipcRenderer.invoke('set-permission-mode', mode, sessionId)
  → main.js: storage.getThreadMapping(sessionId) 取 hermesSessionId
       → gatewayClient.request('session.set_yolo', { session_id, enabled })
  → server.py @method('session.set_yolo'): enabled ? enable_session_yolo(key) : disable_session_yolo(key)
```

### 3.2 接口签名

- **前端**：`window.hermes.setPermissionMode(mode: 'default'|'yolo', sessionId: string) => Promise<{ success: boolean } | { success:false, error }>`
- **preload.js**：新增 `setPermissionMode`
- **main.js**：新增 `ipcMain.handle('set-permission-mode', async (_e, mode, sessionId) => {...})`，复用 `interrupt-session` 的 `storage.getThreadMapping` 映射与 gateway 可用性检查
- **server.py**：新增 `@method("session.set_yolo")`，参数 `{ session_id, enabled }`，调用已 import 的 `enable_session_yolo` / `disable_session_yolo`，返回 `_ok(rid, {status})`

### 3.3 改动文件

| 文件 | 改动 |
|------|------|
| `src/App.jsx` | ① 加 `const [permissionMode, setPermissionMode] = useState('default')`；② 加 `handlePermissionChange(mode)`（调后端 + setState）；③ 把 `permission`/`onPermissionChange` 经 `ChatLayout` 传给 `Composer` |
| `src/components/ChatLayout.jsx` | 接收并透传 `permission` / `onPermissionChange` 给 `Composer` |
| `src/components/Composer.jsx` | ① `PERMISSION_MODES` 砍掉 strict，仅留 default/yolo；② 用父级 `permission`/`onPermissionChange` 替代内部 useState；③ 选择即触发 `onPermissionChange` |
| `electron/preload.js` | 新增 `setPermissionMode` |
| `electron/main.js` | 新增 `set-permission-mode` IPC handler |
| `hermes-fork/tui_gateway/server.py` | 新增 `@method("session.set_yolo")` |

### 3.4 边界与降级

- gateway 未连 / session 未映射 → main.js 返回 `{ success:false, error }`，前端静默保留本地选择（不 crash）。
- `default` 语义 = 关闭 session yolo（恢复后端默认 `ask`）；`yolo` 语义 = 开启 session yolo 旁路（危险操作也不问）。
- 切换只对**当前 session** 生效（与 TUI Shift+Tab 等价），不影响其他会话或全局配置。

## 4. 不做 / 后续

- 语音 TTS、严格模式、权限持久化到 assistant、权限状态全局指示 —— 均不在本期。
- 真机验证需有效 `AGNES_API_KEY` 与干净 Windows 环境（沙箱无法验证录音与 STT 真实返回）。

## 5. 验收条件

1. `npm run build` 通过。
2. 🎤 按钮可点击、可录音、停录后调后端（沙箱无 key 时优雅报错，不崩）。
3. 🛡 菜单只有「默认权限」「完全自动」两档，选择后真实调用 `session.set_yolo`。
4. 无占位 disabled、无假开关 state（权限真实写后端，语音真实走 STT）。
