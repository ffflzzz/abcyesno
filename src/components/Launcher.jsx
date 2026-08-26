import React from "react";
import Icon from "./Icon.jsx";

// Launcher — the app's homepage (启动台). Compact app grid inspired by the
// reference design: a small "应用" heading and a row of icons with labels
// underneath. Each click opens a dedicated browser-style tab.
//
// Icon rendering: app.iconSrc is an ALPHA MASK (transparent bg + white shape).
// The .launcher-app-icon container paints itself with `var(--icon-color)` and
// uses the mask as a cutout, so the same PNG works in both themes:
//   light mode → --icon-color: dark text → dark icon on white
//   dark  mode → --icon-color: white       → light icon on dark
// No per-theme PNG set needed; one mask asset adapts to both.
export default function Launcher({ apps = [] }) {
  return (
    <div className="launcher launcher--simple">
      <div className="launcher-apps">
        <h2 className="launcher-heading">应用</h2>
        <div className="launcher-grid">
          {apps.map((app) => (
            <button key={app.key} className="launcher-app" onClick={app.onClick}>
              <span className="launcher-app-icon">
                {app.iconSrc ? (
                  <img src={app.iconSrc} alt={app.title} draggable={false} />
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
