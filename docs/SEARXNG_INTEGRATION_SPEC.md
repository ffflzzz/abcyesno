# SearXNG 集成规格（草案 v1）

> 2026-08-30 · 目标：给 hermes agent 一个真正的 `web_search` 工具，桌面/微信端通用；
> SearXNG 本地实例随软件启动、随 win-unpacked 分发。

## 0. 结论

**可行，推荐做。** 项目已有两个「随主进程跑本地服务」的成熟模式可抄：
- `paper-rewriter-dashboard-runner.js`：spawn Python 子进程 + 健康检查 + 崩溃自动重启；
- `wechat-bridge-runner.js`：进程内常驻服务。

SearXNG 本身是纯 Python（Flask + httpx + lxml，全有现成 wheel），可以装进
`hermes-fork/.venv`（该 venv 已随包分发，无需新增打包路径）。

**两个必须认清的前提：**
1. **SearXNG 不解决联网本身**——它是聚合器，查 Google/Bing/DDG 照样要出网。
   出网走它自己的 `outgoing.proxies` 配置（可指到 `127.0.0.1:7890`，端口探测存活才写入；
   代理节点挂了 = 搜索空结果/超时，`web_search` 会明确报错让模型如实转告）。
   也就是说：节点活着时搜索质量大幅提升；节点死了依旧没法查（这是网络环境，不是代码能修的）。
2. **许可 = AGPL-3.0**。对外分发捆绑 SearXNG 需满足 AGPL：仓库已公开（ffflzzz/abcyesno）+
   保留上游 LICENSE + 声明修改，基本满足合规。若介意，见 §6 备选方案。

## 1. 架构

```
agent(web_search 工具) ──HTTP──> SearXNG 本地实例(127.0.0.1:18890)
                                      │
                                      └── outgoing(可选代理 127.0.0.1:7890) ──> Google/Bing/DDG…
```

### 1.1 运行载体
- **安装位置**：`hermes-fork/.venv` 内 `pip install searxng`（打包路径零新增）。
- **启动命令**：`hermes-fork/.venv/Scripts/python.exe -m searxng.webapp`（以 Flask 内置
  server 跑单用户本地实例足够；uwsgi 是多用户生产方案，不引入）。
- **端口**：`127.0.0.1:18890`（避开 8888/8080 常见占用）。
- **配置**：打包内置一份 `searxng_settings.yml` 模板，关键项：
  - `search.formats: [html, json]`（**json 默认关闭，必须显式开**——工具靠 JSON API）；
  - `server.secret_key`：首启随机生成写入 HERMES_HOME；
  - `limiter: false`（本机自用，无需限流）；
  - `outgoing.proxies`：启动时探测 7890/7897，存活才注入，否则留空直连。

### 1.2 Runner（新文件 `electron/backend/searxng-runner.js`）
照抄 `paper-rewriter-dashboard-runner.js` 模式：
- `doStartBackend` 尾部 fire-and-forget 启动；
- 启动后轮询 `http://127.0.0.1:18890/healthz`（SearXNG 自带）直至就绪，超时 30s 放弃（非致命）；
- 崩溃自动重启（指数退避，上限 3 次）；
- `window-all-closed` / `before-quit` 时 kill 子进程树（`taskkill /pid /t /f`）。
- 状态经 agui-server 的 env-context 告知 agent（`web_search: 本地实例 http://127.0.0.1:18890`）。

### 1.3 web_search 工具（hermes-fork）
- 新文件 `tools/web_search.py`，注册进 `hermes-cli` toolset：
  - 入参 `{query, max_results?=5}`；
  - `GET http://127.0.0.1:18890/search?q=...&format=json`，10s 超时；
  - 返回精简结果数组 `[{title, url, snippet}]`（截断 snippet，防上下文爆炸）；
  - 本地实例不可用 → 返回明确错误「本地搜索服务未就绪」，**不再引导模型 curl 瞎试**。
- 顺手修正 `pw_browser_*` 的 fallback 文案：删掉对不存在的 `web_extract`/`web_search` 的
  空头支票（改提真实存在的工具）。
- `browser-pw` headless 兜底（面板未就绪时）独立排期，不阻塞本 spec。

## 2. 分发影响

| 项 | 代价 |
|---|---|
| 磁盘 | venv 增加 ~60-80MB（searxng + Flask 全家桶） |
| 内存 | 常驻 Flask 实例 ~100-150MB |
| 许可 | AGPL-3.0：公开仓库 + 保留 LICENSE + README 声明使用 SearXNG 及修改 |
| 启动 | 后台多 ~1-2s（健康检查并行，不阻塞主流程） |

## 3. 备选方案对比（为什么不先做它们）

| 方案 | 优点 | 缺点 |
|---|---|---|
| **自建 SearXNG（本 spec）** | 多引擎聚合、JSON API、结构化结果、无 key | AGPL、+磁盘/内存、上游仍依赖代理 |
| 公共 SearXNG 实例 | 零捆绑零成本 | 公共实例不稳/限流/封 JSON 格式，可用性不可控 |
| DDG lite HTML 直抓 | 无 AGPL、实现 ~100 行 | 单引擎、HTML 解析脆、被反爬即废 |
| Tavily/Bing API | 最稳、结果质量最好 | 要 key、要付费、又多一个外部依赖 |

## 4. 工作量估算
- searxng-runner.js：~80 行（照抄现成模式）
- settings.yml 模板 + 首启生成逻辑：~60 行
- tools/web_search.py + toolset 注册：~120 行
- fallback 文案修正：~10 行
- 打包/venv 预装校验 + 测试：主要时间成本
- 合计：一个晚上到半天，改完走标准质量门 + 部署重启。

## 5. 验收
1. 软件启动后 `curl 127.0.0.1:18890/healthz` 200；
2. 桌面端问「搜一下 XXX」→ agent 调 web_search 返回真实结果；
3. 微信端同一问题 → 同样可用（bridge 走同一 agent 链路）;
4. 杀掉 SearXNG 子进程 → 30s 内自动重启；
5. 代理节点下线时 → web_search 明确报错，模型如实说「搜索服务暂不可用」，不再假装联网。

## 6. 未决问题（待拍板）
- 装进 `hermes-fork/.venv`（省打包路径，污染主 venv） vs 独立小 venv（干净，多一条打包配置）——**建议前者**。
- upstream 代理注入策略：探测存活即写（今晚实测过「端口通≠节点通」的坑）vs 配置文件手动指定——**建议探测 + 失败自动降级直连**。
