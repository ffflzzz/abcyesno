import React, { useState, useEffect, useRef } from "react";
import Icon from "./Icon.jsx";

/**
 * BlockRequestDialog — P0 阻塞式用户输入弹窗。
 *
 * 覆盖四类后端 _block() 请求（触发即挂起 agent 线程，必须回执才能继续）：
 *  - sudo.request        → 管理员密码输入框（type=password）
 *  - secret.request      → 密钥/环境变量输入框（明文输入）
 *  - terminal.read.request → 终端读取回传（多行文本）
 *  - clarify.request     → 澄清问题（选择或自由输入）
 *
 * 约定：onRespond(value) 仅回传用户输入值；request_id 与应答 method 由 App 负责组装。
 * 取消（关闭）同样回传空值以主动解除后端挂起（避免线程死等后端超时兜底）。
 */
const META = {
  "sudo.request": {
    title: "需要管理员（sudo）密码",
    icon: "lock",
    desc: "agent 正在请求提权执行命令，请输入 sudo 密码以继续。",
    inputType: "password",
    placeholder: "输入 sudo 密码",
    multiline: false,
    confirmLabel: "确认并继续",
  },
  "secret.request": {
    title: "需要密钥 / 环境变量",
    icon: "key",
    desc: "agent 请求一个密钥或环境变量值才能继续。",
    inputType: "text",
    placeholder: "输入密钥值",
    multiline: false,
    confirmLabel: "提交",
  },
  "terminal.read.request": {
    title: "终端读取请求",
    icon: "terminal",
    desc: "agent 需要你提供终端输入以继续。",
    inputType: "text",
    placeholder: "输入要回传给终端的内容…",
    multiline: true,
    confirmLabel: "发送",
  },
  "clarify.request": {
    title: "需要你的回复",
    icon: "question",
    desc: "agent 需要进一步确认才能继续。",
    inputType: "text",
    placeholder: "输入你的回答…",
    multiline: false,
    confirmLabel: "提交",
  },
};

export default function BlockRequestDialog({ blockRequest, onRespond }) {
  if (!blockRequest) return null;
  const type = blockRequest.type || "sudo.request";
  const meta = META[type] || META["sudo.request"];
  const [value, setValue] = useState("");
  const inputRef = useRef(null);

  // clarify.request 专用：选项单选状态。
  const rawChoices = Array.isArray(blockRequest.choices) ? blockRequest.choices.filter((c) => typeof c === "string" && c.trim()) : [];
  const choiceList = rawChoices.length > 0 ? [...rawChoices, "其他（手动输入）"] : [];
  const [selectedIndex, setSelectedIndex] = useState(0);
  const isClarify = type === "clarify.request";
  const hasChoices = choiceList.length > 0;
  const otherSelected = isClarify && hasChoices && selectedIndex === choiceList.length - 1;

  useEffect(() => {
    if (inputRef.current && (!isClarify || otherSelected || !hasChoices)) {
      inputRef.current.focus();
    }
  }, [isClarify, hasChoices, otherSelected]);

  const envVar = blockRequest.env_var;
  const promptText = blockRequest.prompt || blockRequest.question;

  function submit() {
    if (isClarify && hasChoices && !otherSelected) {
      onRespond(choiceList[selectedIndex]);
    } else {
      onRespond(value);
    }
  }
  function cancel() {
    onRespond("");
  }

  return (
    <div className="modal-mask">
      <div className="modal block-request-modal">
        <h3>
          <Icon name={meta.icon} size={16} /> {meta.title}
        </h3>
        <p className="modal-desc">{meta.desc}</p>

        {type === "secret.request" && envVar && (
          <div className="approval-row">
            <span className="approval-label">变量名</span>
            <span className="approval-value">{envVar}</span>
          </div>
        )}
        {promptText && (
          <div className="block-request-prompt">{promptText}</div>
        )}

        {isClarify && hasChoices && (
          <div className="clarify-choices" role="radiogroup">
            {choiceList.map((choice, idx) => (
              <label
                key={idx}
                className={`clarify-choice ${selectedIndex === idx ? "selected" : ""}`}
              >
                <input
                  type="radio"
                  name="clarify-choice"
                  checked={selectedIndex === idx}
                  onChange={() => setSelectedIndex(idx)}
                />
                <span>{choice}</span>
              </label>
            ))}
          </div>
        )}

        {(!isClarify || !hasChoices || otherSelected) && (
          meta.multiline ? (
            <textarea
              ref={inputRef}
              className="block-request-input"
              value={value}
              placeholder={meta.placeholder}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
              }}
              rows={4}
            />
          ) : (
            <input
              ref={inputRef}
              className="block-request-input"
              type={meta.inputType}
              value={value}
              placeholder={meta.placeholder}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          )
        )}

        <div className="modal-actions">
          <button className="ghost" onClick={cancel}>
            取消
          </button>
          <button className="primary" onClick={submit}>
            {meta.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
