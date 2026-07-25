import React, { useMemo } from "react";

/**
 * StructuredThinking — 结构化思考/推理展示
 *
 * 将 thinkingText（累积的 thinking.delta）解析为结构化行：
 *   🔍 已搜索  C:\path\to\file
 *   © 已读取   src/components/X.jsx L251
 *   ▸ 步骤描述文字
 *   · 普通推理文本
 *
 * 解析规则（按优先级）：
 *   1. "已搜索" / "搜索" + 文件路径 → search 行
 *   2. "已读取" / "©" / "读取" + 文件路径 → file 行
 *   3. 行首中文动作词（首先/然后/接着/正在/步骤/开始/尝试/检查/分析/判断/决定/生成/调用/创建/查找/定位/扫描/解析/裁剪/替换/修改/写入/保存）→ step 行
 *   4. 含文件路径（C:\... 或 /.../*.ext）但无上述关键词 → inference 行（带路径高亮）
 *   5. 其余 → text 行
 */

const PATH_RE = /[A-Z]:\\[^\s,;，；)）\]]+|\/[^\s,;，；)）\]]+\.(?:jsx?|tsx?|py|js|ts|md|css|json|png|jpg|svg|html)/gi;
const SEARCH_RE = /(已搜索|搜索[到过]?|查找[了过]?\s*[:：]?)\s*(.+)/;
const READ_RE = /(已读取|已获取|©|读取[了过]?\s*[:：]?)\s*(.+)/;
const STEP_PREFIX_RE = /^(首先|然后|接着|之后|随后|正在|步骤\s*\d*[、.:：]|开始|尝试|检查|分析|判断|决定|生成|调用|创建|查找|定位|扫描|解析|裁剪|替换|修改|写入|保存|打开|加载|导入|导出|构建|编译|打包|安装|配置|连接|发送|请求|处理|执行|运行|启动|停止|删除|清空|重置|更新|升级|降级|回退|提交|推送|合并|分支|切换|下载|上传|复制|移动|重命名|格式化|优化|修复|重构|测试|验证|确认|选择|筛选|排序|过滤|映射|转换|编码|解码|压缩|解压|渲染|绘制|绑定|挂载|注册|注销|订阅|发布|监听|拦截|捕获|抛出|返回|输出|输入|提取|拆分|合并|拼接|拼接|追加|移除|插入|替换|覆盖|清空|重载|刷新|同步|异步|并行|串行|等待|休眠|延迟|超时|重试|回滚|撤销|恢复|备份|还原|迁移|转换|适配|兼容|封装|抽象|继承|实现|接口|协议|标准|规范|约定|配置|部署|发布|上线|下线|扩容|缩容|监控|告警|日志|审计|追踪|调试|排错|诊断|性能|安全|权限|认证|授权|加密|解密|签名|校验|哈希|摘要|指纹|版本|迭代|增量|全量|差异|补丁|热更|灰度|蓝绿|金丝雀|熔断|降级|限流|削峰|缓存|预热|穿透|击穿|雪崩|一致性|可用性|可靠性|可扩展|可维护|可测试)/u;

function parseThinkingLines(text) {
  if (!text || !text.trim()) return [];
  const lines = text.split("\n");
  const parsed = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;

    // 1. Search pattern
    let m = line.match(SEARCH_RE);
    if (m) {
      parsed.push({ type: "search", text: line, path: extractPath(m[2] || line) });
      continue;
    }

    // 2. Read pattern
    m = line.match(READ_RE);
    if (m) {
      parsed.push({ type: "file", text: line, path: extractPath(m[2] || line) });
      continue;
    }

    // 3. Step prefix
    if (STEP_PREFIX_RE.test(line)) {
      parsed.push({ type: "step", text: line });
      continue;
    }

    // 4. Contains path → inference with highlight
    const paths = line.match(PATH_RE);
    if (paths && paths.length > 0) {
      parsed.push({ type: "inference", text: line, paths });
      continue;
    }

    // 5. Plain text
    parsed.push({ type: "text", text: line });
  }

  return parsed;
}

function extractPath(text) {
  const m = text.match(PATH_RE);
  return m ? m[0] : text;
}

/** Highlight file paths in text */
function HighlightPaths({ text, paths }) {
  if (!paths || paths.length === 0) return <>{text}</>;
  let result = text;
  // Sort by length descending to replace longer paths first
  const sorted = [...paths].sort((a, b) => b.length - a.length);
  for (const p of sorted) {
    const escaped = p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    result = result.replace(new RegExp(escaped, "g"), `<code class="think-path">${p}</code>`);
  }
  return <span dangerouslySetInnerHTML={{ __html: result }} />;
}

export default function StructuredThinking({ text = "", phaseLabel = "" }) {
  const lines = useMemo(() => parseThinkingLines(text), [text]);

  if (!text.trim() && !phaseLabel) {
    return (
      <div className="thinking-indicator">
        <span className="thinking-spinner" aria-hidden="true" />
        <span className="thinking-text">正在思考…</span>
        <span className="thinking-dots">
          <span className="thinking-dot" style={{ animationDelay: "0ms" }}>.</span>
          <span className="thinking-dot" style={{ animationDelay: "150ms" }}>.</span>
          <span className="thinking-dot" style={{ animationDelay: "300ms" }}>.</span>
        </span>
      </div>
    );
  }

  return (
    <div className="structured-thinking">
      {(phaseLabel || !text.trim()) && (
        <div className="think-phase-header">
          <span className="thinking-spinner" aria-hidden="true" />
          <span className="think-phase-label">{phaseLabel || "正在思考…"}</span>
        </div>
      )}
      {lines.map((line, i) => (
        <div key={i} className={`think-line think-${line.type}`}>
          {line.type === "search" && (
            <>
              <span className="think-icon think-icon-search" aria-hidden="true">🔍</span>
              <span className="think-label">已搜索</span>
              <code className="think-path">{line.path}</code>
            </>
          )}
          {line.type === "file" && (
            <>
              <span className="think-icon think-icon-file" aria-hidden="true">©</span>
              <span className="think-label">已读取</span>
              <code className="think-path">{line.path}</code>
            </>
          )}
          {line.type === "step" && (
            <>
              <span className="think-icon think-icon-step" aria-hidden="true">▸</span>
              <span className="think-text-content">{line.text}</span>
            </>
          )}
          {line.type === "inference" && (
            <>
              <span className="think-icon think-icon-infer" aria-hidden="true">·</span>
              <span className="think-text-content"><HighlightPaths text={line.text} paths={line.paths} /></span>
            </>
          )}
          {line.type === "text" && (
            <>
              <span className="think-icon think-icon-text" aria-hidden="true">·</span>
              <span className="think-text-content">{line.text}</span>
            </>
          )}
        </div>
      ))}
      {/* Live indicator at bottom when streaming — only when there are parsed lines */}
      {lines.length > 0 && (
        <div className="think-live">
          <span className="thinking-spinner" aria-hidden="true" />
        </div>
      )}
    </div>
  );
}
