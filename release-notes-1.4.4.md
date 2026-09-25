## v1.4.4

### 修复
- **切 tab 杀死 agent + 会话列表消失**：ChatShell 全 tab 常驻（不再被 early return 卸载）；storage.js 加互斥锁 + 原子写，修复 sessions.json 并发撕裂损坏（已抢救全部历史数据）
- **/goal 循环中断**：goal judge 空窗心跳 + goal.continue/goal.complete 显式信号；SSE idle 兜底 600s；goal 模式硬超时 6h
- **thinking 流式可见**：推理/工具/回复运行期实时展示，结束后自动收纳；reasoning 随消息持久化（不再跑完就消失）
- **agent 过程流**：运行期逐行打印（推理暗斜体/工具✓✗行/回复），固定高度滚动、自动滚底、上滚暂停；工具段保留汇总条与手动收纳，完成即自动收纳
- **网页显示不全（真根因）**：webview guest 尺寸创建后被定格未跟随面板（CDP 实测 innerWidth=1078），加 autosize 修复；面板宽度按窗口夹紧，不再被窗口边界裁切；拖手柄实时生效
- **（未收到模型输出）**：补全 whatsapp_identity 缺失函数，修复权限审批通知路径 ImportError 导致 terminal 全部 BLOCKED
- **审批 UI 重设计**：从小 Bach 头顶冒出的紧凑气泡（与报错气泡同款设计语言）
- **内置浏览器自动弹出**：仅 agent 运行中调用浏览器工具时弹，进会话不再误弹
- **工具明细排版**：长 JSON 断行修复 + 参数预览去噪（跳过 tool_id/name 内部字段）
- **过程流健康可见**：后端静默 >45s 显示健康提示；browser panel 进会话误弹修复

### 其他
- 存储写入全链路原子化（tmp+rename+互斥锁+撕裂恢复）
- goal 心跳：judge 评估期间 20s 心跳防 SSE 误断
