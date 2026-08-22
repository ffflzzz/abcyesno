import React from "react";
import Icon from "./Icon.jsx";

// Launcher — the app's homepage (启动台). Compact app grid inspired by the
// reference design: a small "应用" heading and a row of colored rounded-square
// icons with labels underneath. Each click opens a dedicated browser-style tab.
//
// 所有图标统一视觉尺寸的关键：每个 .launcher-app-icon 容器永远是 56×56 的
// 带圆角的彩色方形（app.color 背景），img 在内部用 object-fit: contain 显示。
// 图标源 PNG 周围留约 5-6% 透明 padding，所以彩色容器会从四周露出，形成
// 视觉上的「同尺寸彩色方块 + 居中图标」效果。这样不论每个图标的内部画面
// 大小、笔触粗细如何，三个 launcher 图标看起来都是同尺寸的。
export default function Launcher({ apps = [] }) {
  return (
    <div className="launcher launcher--simple">
      <div className="launcher-apps">
        <h2 className="launcher-heading">应用</h2>
        <div className="launcher-grid">
          {apps.map((app) => (
            <button key={app.key} className="launcher-app" onClick={app.onClick}>
              <span className="launcher-app-icon" style={{ background: app.color }}>
                {app.iconSrc ? (
                  <img
                    src={app.iconSrc}
                    alt={app.title}
                    draggable={false}
                  />
                ) : (
                  <Icon name={app.icon} size={28} />
                )}
              </span>
              <span className="launcher-app-name">{app.title}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
