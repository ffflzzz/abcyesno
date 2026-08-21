import React from "react";
import Icon from "./Icon.jsx";

// Launcher — the app's homepage (启动台). Compact app grid inspired by the
// reference design: a small "应用" heading and a row of colored rounded-square
// icons with labels underneath. Each click opens a dedicated browser-style tab.
export default function Launcher({ apps = [] }) {
  return (
    <div className="launcher launcher--simple">
      <div className="launcher-apps">
        <h2 className="launcher-heading">应用</h2>
        <div className="launcher-grid">
          {apps.map((app) => (
            <button key={app.key} className="launcher-app" onClick={app.onClick}>
              <span
                className="launcher-app-icon"
                style={app.iconSrc ? undefined : { background: app.color }}
              >
                {app.iconSrc ? (
                  <img
                    src={app.iconSrc}
                    alt={app.title}
                    width={28}
                    height={28}
                    style={{ borderRadius: 8, display: "block" }}
                    draggable={false}
                  />
                ) : (
                  <Icon name={app.icon} size={24} />
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
