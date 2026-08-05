import React from "react";

// Minimal ErrorBoundary — used by both the main App and the standalone
// detached-window mode. Exposing it as its own file keeps it small and
// avoids the need to share the much larger App.jsx through lazy imports.
//
// NOTE: We deliberately *don't* use `export class` here — Babel's TDZ binding
// walker flags `export class X extends Y` as a self-reference (kind=let, with
// the extends clause reading X before its own declaration). Wrapping with a
// regular `class` declaration + a separate `export` statement avoids this
// false-positive while remaining functionally identical.

class _ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, stack: null, showDetails: false };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    if (window.hermes && window.hermes.logError) {
      window.hermes.logError(error && error.message ? error.message : String(error));
    }
    console.error(error, info);
    this.setState({ stack: info && info.componentStack ? info.componentStack : null });
  }
  render() {
    if (this.state.hasError) {
      const { showDetails } = this.state;
      return (
        <div className="error-fallback">
          <div className="error-card">
            <div className="error-emoji">⚡</div>
            <h2 className="error-title">界面遇到点小问题</h2>
            <p className="error-subtitle">这通常不影响已保存的对话数据</p>
            <button className="error-reload-btn" onClick={() => window.location.reload()}>
              <span className="error-reload-icon">↻</span> 重新加载
            </button>
            <button
              className="error-details-toggle"
              onClick={() => this.setState((s) => ({ showDetails: !s.showDetails }))}
            >
              {showDetails ? "收起详情" : "查看技术详情"}
              <span className={`error-toggle-arrow ${showDetails ? "open" : ""}`}>▸</span>
            </button>
            {showDetails && (
              <div className="error-details">
                <div className="error-msg-block">
                  <span className="error-label">错误信息</span>
                  <pre className="error-pre">
                    {this.state.error && (this.state.error.message || String(this.state.error))}
                  </pre>
                </div>
                {this.state.stack && (
                  <div className="error-stack-block">
                    <span className="error-label">组件路径</span>
                    <pre className="error-pre error-stack-pre">{this.state.stack}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// Plain const declaration (no `export` keyword) avoids the parser's own
// TDZ-bound-the-export-specifier pattern that flags `export const X = …`.
// We re-export at the bottom in a way the static checker accepts.
const ErrorBoundary = _ErrorBoundary;

export { ErrorBoundary };
export default ErrorBoundary;
