# CHARACTER_LIBRARY_SPEC

> 全局（跨项目）角色库 — 用户在 StudioWorkbench 里任何地方生成出来的角色，
> 都会被自动归档进这个库，下次开新项目可以直接复用。

## 背景 & 设计目标

**现状**
- 角色图存在 `StudioWorkbench` 的 `assetImgs.character`（per-project，存
  `_studioCache[workflowId]`，仅当 `workflowId === manjucraft_agent` 时持久化），
  项目结束就丢了。
- 资产面板的 `角色` tab 只列当前项目里 `parse_script` 出来的角色，没有
  「跨项目的角色库」概念。
- 截图是一个参考：可以点开看每个角色的全身图/面部/表情九宫格/多视角，
>  并能「应用至画布」——也就是把库里的角色绑定到当前项目的角色空位。

**目标**
1. 全局角色库，独立于项目，跨项目共享。
2. **默认就内置一批常用角色**（截图底栏那一排的"圆妹/清新少女"
   "霸总/精英大佬"……约 10 个），用户打开就能用。
3. **每次生成的角色自动归档**——跑 `generate_characters` 的输出、手动
   「↻ 重新生成角色」按钮，都会顺手进库。
4. 「应用至画布」按钮，把库里选中的角色塞进当前项目的 `assetImgs.character`。

## 数据模型 & 存储

```
~/.hermes_portable_data/manjucraft_agent/character_library/
├── index.json                        # 角色元数据列表
├── cards/
│   ├── char_xxx_front.jpg            # 主头像（用于底栏缩略图）
│   ├── char_xxx_views_angle.jpg      # 多视角（可选）
│   └── …
└── seed/built_in.json                # 内置种子（按需拷贝到 index.json）
```

**`index.json` 每条**:
```js
{
  "id": "char_xxxxxx",          // 内部唯一 id（readable 哈希）
  "name": "圆妹/清新少女",     // 角色名（同项目里的 character 名）
  "tags": ["清新少女","女主","现代","青年","逆袭"],  // 自由 tag
  "prompt": "清新少女，全身设定图…",  // 用于"应用至画布"时直接调用图模型
  "style": "写实",            // 默认风格，可省
  "frontUrl": "cards/char_xxx_front.jpg",      // 相对 libDir
  "views": { "正面": "cards/char_xxx_front.jpg", "侧面": "..." },
  "source": "builtin" | "generated:<projectName>",
  "createdAt": 1745123456789,
  "lastUsedAt": 1745567890123,    // 仅当应用过
  "useCount": 3
}
```

## 后端（Hermes / manjucraft_agent）

`electron/backend/character_library.py`（新增；遵循 `_portablizeVenv()`
使用随包 Python，落到 `HERMES_HOME`）：

```python
def library_dir() -> Path           # ~/.hermes_portable_data/manjucraft_agent/character_library/
def load_index() -> list[dict]      # 读 index.json
def find_by_name(name) -> dict|None # 按 name 精确查
def upsert_card(card, *, image_bytes=None, view_bytes_map=None) -> dict
def touch_used(card_id) -> None     # 写 lastUsedAt / useCount++
def seed_if_empty(builtin_json_path: Path) -> None
```

**接口（沿用现有 AG-UI IPC，新增 `character_library.*` 命名空间）**:
- `character_library.list` → `[]`  返回所有卡片
- `character_library.upsert` → `{id}`  接收 `{name, tags, prompt, frontUrlDataUri?, views?}`
- `character_library.touch_used` → `{}` 接 `{id}`
- 初次启动时，`bootstrap.py` 检测 `index.json` 不存在 → 从
  `seed/built_in.json` 拷贝 + 写本地空位（front 图用 SVG 占位）。

**自动归档钩子**:
- `manjucraft_agent/agents/manjucraft_agent/nodes/generate_characters.py`
  生成完每个角色的图后，调用 `upsert_card({...source: "generated:<project>"})`。
- 前端如果绕过 agent 直接调 `api('generate-image', ...)`（`genOne`
  路径），由前端在拿到 URL 后回调 `upsert`（见下文），避免给前端 + 后端
  双份实现。

## 前端

### 新组件
- `src/components/CharacterLibraryModal.jsx`
  - 全屏 overlay（`<aside class="char-lib-modal">`）
  - 上半区：选中角色的 detail（全身 + 面部 + 表情九宫格 + 多视角）
  - 下半区：左侧 `角色筛选` 下拉 + 顶栏 + 居中卡片列表（缩略图 + 标签 + 名字）
  - 右上：「✕ 关闭」
  - 右下：「+ 应用至画布」（用 onApply 回调）
  - 用 CSS 变量对齐现有暗色主题（`--surface-1` / `--surface-2` /
    `--text-primary` 等）

### 触发按钮
- `src/workbenches/StudioWorkbench.jsx` 里的 `AssetLibrary`：
  角色 tab 顶部加「📚 角色库」按钮，点击打开 modal。
  把 modal state 提到 workbench 顶层（仅在 `curTab === "character"` 时可见）。

### 自动归档（前端路径）
- 现有 `ingestArtifact` 函数（line 1399+，当 `type === 'image'` 且
  `label` 以 `角色·` 开头）：在 `setAssetImgs` 之后追加
  `api("character_library.upsert", {name, tags: [...], prompt, frontUrl: src})`。
- 现有 `genOne("character")`（line 1556+）：成功后同样的 `upsert`。

### API 客户端
- `src/contract/agentBridge.js`（或类似层）：在已有 `invoke(type, payload)`
  上加 `agentBridge.characterLibrary.list/upsert/touchUsed` 的便捷方法。

### i18n / 文本
- 全部走 inline 中文常量（参考现有风格）。

## 内置种子 — `seed/built_in.json`

10 个：
| name | tags |
|---|---|
| 圆妹/清新少女 | 清新少女, 女主, 现代, 青年, 逆袭 |
| 霸总/精英大佬 | 霸总, 男主, 现代, 中年, 都市 |
| 温柔妈妈/慈爱妇女 | 母亲, 女主, 家庭, 中年, 治愈 |
| 清冷千金/白鹤染主任 | 千金, 女主, 现代, 青年, 豪门 |
| 古风男主 | 古风, 男主, 古代, 青年, 武侠 |
| 古风女主 | 古风, 女主, 古代, 青年, 言情 |
| 恶毒女配/白莲花 | 反派, 女配, 现代, 青年, 阴谋 |
| 正派长辈/父亲 | 长辈, 男配, 古代, 中年, 威严 |
| 偏激长辈/刻薄亲戚 | 反派, 长辈, 现代, 中年, 偏见 |
| 反派长辈/刻薄亲 | 反派, 长辈, 现代, 中年, 阴冷 |
| 生活务实老善良 | 平民, 长辈, 现代, 中年, 邻家 |

每个卡片都带一段高质量 prompt（中文多模态常用风格），便于后续
「+ 应用至画布」直接喂给 Agnes。

**占位图策略（待确认，见 §问题）**：
- 选项 A — SVG placeholder：内置生成纯文字渐变卡片作为 frontUrl，视觉统一、不花钱（图模型不调用）。
- 选项 B — Agnes 一次性渲染：首次启动拉一发图模型，把 11 个图写入磁盘
  （约 11*credits）。视觉真实但首次启动慢 + 烧 quota。

## 与原有 AssetLibrary 关系

- `AssetLibrary` 角色 tab：照旧显示「本项目」`assetImgs.character`。
- 点 `📚 角色库` 按钮 → modal 打开，可看全局库，点「应用至画布」
  → modal 关闭 + `setAssetImgs` 把选中卡片塞进当前 `assetImgs.character[name]`。
- 同时调 `character_library.touch_used`，把 `lastUsedAt/useCount` 维护好。

## 验收 / 测试

`scripts/test-multisession/run.mjs` 新增 `H1-H6`：
- H1 `seed/built_in.json` 存在且 ≥10 条
- H2 `CharacterLibraryModal` 文件存在且导出 default
- H3 `StudioWorkbench.jsx` 含 `"📚 角色库"` 文本 + 调用 `openCharLibrary`
- H4 `ingestArtifact` 角色分支末尾调用了
  `character_library.upsert` 或类似名
- H5 `genOne` 成功后调用了同一名
- H6 manifest.json 保持 `openMode=window`，无回归

## 问题

1. **触发按钮位置**：选项
   - A (推荐) AssetLibrary 角色 tab 顶栏「📚 角色库」按钮（最自然，
     与现有「+ 添加场景」「↻ 重新生成」并列）。
   - B storyboard 阶段 header 上加一个图标按钮。
   - C 启动台 launcher 多加一个「角色库」app 图标（独立窗口或主窗口 tab）。
2. **占位图策略**：选项 A vs B（见上）。
