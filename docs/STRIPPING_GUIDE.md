# Abcyesno — 源码精简指南

本指南记录如何安全地从 Hermes Fork 中移除非必要模块，同时保留核心 harness（chat、tool calling、skills、memory、gateway web server）。每次删除后必须运行 `hermes serve` 验证启动正常。

## 1. 精简原则

1. **先标记，后删除**：先在代码中注释或禁用，确认无影响再删除文件。
2. **小步验证**：每删除一个模块就启动一次 `hermes serve`。
3. **保留 CLI 加载路径**：`hermes_cli/main.py` 中对子命令的 import 和注册必须同步清理。
4. **保留依赖声明**：`pyproject.toml` 中对应依赖也要移除。
5. **不要破坏核心 harness**：`agent/`、`tools/`（仅删除 IM 工具）、`skills/` 框架、`memory/`、`gateway/` web server、`config/`、`hermes_cli/` 核心必须保留。

## 2. Phase 1 已拆除模块清单

### 2.1 IM / messaging 通道（高优先级）

| 模块 | 路径/文件 | 处理状态 |
|------|-----------|----------|
| WhatsApp platform plugin | `plugins/platforms/whatsapp/` | 已删除 |
| Slack platform plugin | `plugins/platforms/slack/` | 已删除 |
| Telegram platform plugin | `plugins/platforms/telegram/` | 已删除 |
| Discord platform plugin | `plugins/platforms/discord/` | 已删除 |
| Signal platform plugin | `plugins/platforms/signal/` | 已删除 |
| Weixin platform plugin | `plugins/platforms/weixin/` | 已删除 |
| Line platform plugin | `plugins/platforms/line/` | 已删除 |
| Matrix platform plugin | `plugins/platforms/matrix/` | 已删除 |
| Mattermost platform plugin | `plugins/platforms/mattermost/` | 已删除 |
| DingTalk platform plugin | `plugins/platforms/dingtalk/` | 已删除 |
| Feishu/Lark platform plugin | `plugins/platforms/feishu/` | 已删除 |
| Google Chat platform plugin | `plugins/platforms/google_chat/` | 已删除 |
| IRC platform plugin | `plugins/platforms/irc/` | 已删除 |
| SMS platform plugin | `plugins/platforms/sms/` | 已删除 |
| Teams platform plugin | `plugins/platforms/teams/` | 已删除 |
| WeCom platform plugin | `plugins/platforms/wecom/` | 已删除 |
| SimpleX platform plugin | `plugins/platforms/simplex/` | 已删除 |
| Ntfy platform plugin | `plugins/platforms/ntfy/` | 已删除 |
| WhatsApp Cloud gateway adapter | `gateway/platforms/whatsapp_cloud.py` | 已删除 |
| WhatsApp common helpers | `gateway/platforms/whatsapp_common.py` | 已删除 |
| Signal gateway adapter | `gateway/platforms/signal.py` | 已删除 |
| Signal format helpers | `gateway/platforms/signal_format.py` | 已删除 |
| Signal rate-limit helpers | `gateway/platforms/signal_rate_limit.py` | 已删除 |
| Weixin gateway adapter | `gateway/platforms/weixin.py` | 已删除 |
| BlueBubbles (iMessage) adapter | `gateway/platforms/bluebubbles.py` | 已删除 |
| MSGraph Teams webhook adapter | `gateway/platforms/msgraph_webhook.py` | 已删除 |
| QQ Bot adapter | `gateway/platforms/qqbot/` | 已删除 |
| Yuanbao adapter | `gateway/platforms/yuanbao.py` | 已删除 |
| Yuanbao media/proto/sticker | `gateway/platforms/yuanbao_media.py`, `yuanbao_proto.py`, `yuanbao_sticker.py` | 已删除 |
| WhatsApp identity helpers | `gateway/whatsapp_identity.py` | 已删除并替换为 stub |
| Discord tool | `tools/discord_tool.py` | 已删除 |
| Yuanbao tools | `tools/yuanbao_tools.py` | 已删除 |
| Yuanbao skill | `skills/yuanbao/` | 已删除 |
| WhatsApp Cloud setup wizard | `hermes_cli/setup_whatsapp_cloud.py` | 已删除 |
| Telegram managed bot helper | `hermes_cli/telegram_managed_bot.py` | 已删除 |
| Send subcommand | `hermes_cli/send_cmd.py` | 已删除 |
| WhatsApp CLI subcommand | `hermes_cli/subcommands/whatsapp.py` | 已删除 |
| Slack CLI subcommand | `hermes_cli/subcommands/slack.py` | 已删除 |
| WhatsApp bridge script | `scripts/whatsapp-bridge/` | 已删除 |
| Discord voice doctor script | `scripts/discord-voice-doctor.py` | 已删除 |

保留的平台插件：`plugins/platforms/email/`、`plugins/platforms/homeassistant/`、`plugins/platforms/photon/`、`plugins/platforms/raft/`。
保留的 gateway adapters：`gateway/platforms/base.py`、`gateway/platforms/api_server.py`、`gateway/platforms/webhook.py`。

### 2.2 官方服务与更新（高优先级）

| 模块 | 路径/文件 | 处理状态 |
|------|-----------|----------|
| `update` CLI subcommand | `hermes_cli/subcommands/update.py` | 已删除 |
| `login` CLI subcommand | `hermes_cli/subcommands/login.py` | 已删除 |
| `logout` CLI subcommand | `hermes_cli/subcommands/logout.py` | 已删除 |
| `auth` CLI subcommand | `hermes_cli/subcommands/auth.py` | 已删除 |
| 启动时更新检查/自修复 | `hermes_cli/main.py` 中 `_cleanup_quarantined_exes`、`_recover_from_interrupted_install`、`_termux_should_prefetch_update_check` 调用 | 已移除调用 |
| `/update` 交互命令 | `cli.py` 中 `/update` dispatch | 已禁用 |
| `desktop`/`gui` CLI subcommand | `hermes_cli/subcommands/gui.py` | 已删除 |
| Desktop app source | `apps/desktop/` | 已删除 |
| Docker packaging | `docker/` | 已删除 |
| Nix flake | `flake.nix`, `flake.lock` | 已删除 |
| Batch runner | `batch_runner.py` | 已删除 |
| Cron scheduler directory | `cron/` | 已删除 |
| Cron CLI subcommand | `hermes_cli/subcommands/cron.py`、`hermes_cli/cron.py` | 已删除 |
| Gateway cron 启动 | `gateway/run.py` 中 cron scheduler 启动代码 | 已移除 |

说明：本地 credential 管理（`hermes_cli/auth.py` 中的 provider registry）仍保留，仅删除在线登录/认证的 CLI 入口。

### 2.3 依赖清理

在 `pyproject.toml` 中移除的 optional-extras：

- `messaging`
- `slack`
- `matrix`
- `wecom`
- `teams`
- `sms`
- `dingtalk`
- `feishu`
- `cron`
- `termux`
- `termux-all`

并从 `[all]` 中移除对 `hermes-agent[cron]`、`hermes-agent[sms]` 的引用。

在 `pyproject.toml` 中移除的 top-level py-module：

- `batch_runner`

在 `tool.setuptools.packages.find` 的 `include` 中移除：

- `"cron"`, `"cron.*"`

## 3. 兼容性补丁

删除文件后，以下文件需要同步修改引用，否则启动会失败：

1. **`gateway/platforms/__init__.py`**：移除对 `QQAdapter`、`YuanbaoAdapter` 的 `__getattr__` 转发，仅保留 `BasePlatformAdapter`、`MessageEvent`、`SendResult`。
2. **`gateway/session.py`**：原 `from .whatsapp_identity import ...` 改为本地 stub 实现 `canonical_whatsapp_identifier` / `normalize_whatsapp_identifier`。
3. **`gateway/whatsapp_identity.py`**：重建为 stub，返回原值，保留 `gateway/authz_mixin.py`、`gateway/pairing.py`、`gateway/run.py` 的兼容性。
4. **`gateway/run.py`**：
   - `_home_target_env_var` 不再导入 `cron.scheduler`。
   - `_start_cron_ticker` 改为 no-op。
   - `start_gateway` 不再启动 cron scheduler，仅保留 housekeeping。
   - `_load_adapter` 仅保留 `API_SERVER` 与 `WEBHOOK` 两个内置 adapter。
5. **`hermes_cli/main.py`**：
   - 移除 `build_whatsapp_parser`、`build_slack_parser`、`build_login_parser`、`build_logout_parser`、`build_auth_parser`、`build_update_parser`、`build_cron_parser`、`build_gui_parser` 的 import 与注册。
   - 移除 `send` 命令注册。
   - 移除启动阶段的 quarantine 清理、interrupted install 自修复、Termux update prefetch 调用。
   - `cmd_version` 不再检查更新。
6. **`hermes_cli/_parser.py`**：从 `_EPILOGUE` 中移除 `logout`/`auth`/`update` 等已删除命令的示例。
7. **`cli.py`**：`get_job` 改为抛出 `RuntimeError`（cron 已移除）；`/update` 命令打印不可用提示；`/version` 不再检查更新。

## 4. 操作步骤模板

```bash
cd hermes-fork

# 删除 IM 模块
rm -rf plugins/platforms/{whatsapp,slack,telegram,discord,signal,weixin,line,matrix,mattermost,dingtalk,feishu,google_chat,irc,sms,teams,wecom,simplex,ntfy}
rm -f gateway/platforms/{whatsapp_cloud.py,whatsapp_common.py,signal.py,signal_format.py,signal_rate_limit.py,weixin.py,bluebubbles.py,msgraph_webhook.py,yuanbao*.py}
rm -rf gateway/platforms/qqbot
rm -f tools/discord_tool.py tools/yuanbao_tools.py hermes_cli/send_cmd.py hermes_cli/setup_whatsapp_cloud.py hermes_cli/telegram_managed_bot.py
rm -f hermes_cli/subcommands/{whatsapp.py,slack.py,login.py,logout.py,auth.py,update.py,cron.py} hermes_cli/cron.py
rm -rf scripts/whatsapp-bridge skills/yuanbao

# 删除官方服务/更新模块
rm -rf apps/desktop docker cron
rm -f flake.nix flake.lock batch_runner.py scripts/discord-voice-doctor.py

# 同步修改 hermes_cli/main.py、gateway/run.py、gateway/platforms/__init__.py、gateway/session.py、gateway/whatsapp_identity.py、cli.py、hermes_cli/_parser.py、pyproject.toml

# 验证
HERMES_HOME=L:/hermes-portable-v1/.hermes_dev .venv/Scripts/hermes serve --port 9119
# 另开终端
# curl http://127.0.0.1:9119/api/status
```

## 5. 常见错误处理

### 5.1 ImportError: No module named 'xxx'
- 说明 CLI 或其他模块仍引用已删除文件。
- 搜索报错中的模块名，删除对应 import 或改为 stub。

### 5.2 Subcommand not found
- 说明 `hermes_cli/main.py` 中仍有子命令注册，但对应处理文件已删除。
- 删除 `main.py` 中对应的 `build_*_parser` import 与调用。

### 5.3 启动正常但功能异常
- 检查是否误删了被其他模块间接依赖的文件。
- 使用 `git diff` 对比 baseline，定位问题。

## 6. 验收标准

- `hermes serve --port 9119` 能正常启动。
- `GET http://127.0.0.1:9119/api/status` 返回 200。
- `hermes --help` 中无 `whatsapp`、`whatsapp-cloud`、`slack`、`telegram`、`discord`、`signal`、`send`、`login`、`logout`、`auth`、`update`、`cron`、`desktop`、`gui` 等子命令。
- 启动日志中无 IM / 更新 / cron 相关报错。

## 7. 待补充

- [x] 完成具体文件清单（已更新）。
- [x] 确认 `gateway/` 中 messaging 相关文件位置（已清理）。
- [x] 确认自动更新逻辑具体代码位置（`hermes_cli/main.py` 启动段 + `hermes_cli/subcommands/update.py`）。
- [x] 确认 cron 在 `gateway/run.py` 中的调用并已剥离。
