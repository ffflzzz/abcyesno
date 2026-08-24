import { useState, useCallback } from 'react';

const ONBOARDING_KEY = 'abcyesno:onboarding';
const ONBOARDING_DONE_KEY = 'abcyesno:onboardingDone';

/** Read persisted onboarding answers (or null if never done). */
export function getOnboarding() {
  try {
    const raw = localStorage.getItem(ONBOARDING_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

/** Check if user has already completed onboarding. */
export function isOnboardingDone() {
  try {
    return localStorage.getItem(ONBOARDING_DONE_KEY) === '1';
  } catch (_) {
    return false;
  }
}

const STYLE_OPTIONS = [
  { value: 'direct', label: '简洁直接', desc: '少废话，给结果' },
  { value: 'friendly', label: '详细友好', desc: '耐心解释，像朋友' },
  { value: 'professional', label: '专业严谨', desc: '结构化，有依据' },
  { value: 'casual', label: '轻松幽默', desc: '有趣不枯燥' },
];

const SCENE_OPTIONS = [
  { value: 'coding', label: '写代码' },
  { value: 'writing', label: '写文档' },
  { value: 'data', label: '数据分析' },
  { value: 'creative', label: '创意设计' },
  { value: 'daily', label: '日常问答' },
  { value: 'research', label: '调研搜索' },
];

/**
 * First-launch onboarding — shown inside the Bootstrap splash while the
 * backend initialises.  Collects name, response style and primary use-cases,
 * persists to localStorage, then yields control so Bootstrap can render <App>.
 *
 * Parent passes `ready` (boolean): when true the backend is up and we can
 * unlock the "Go" button (user may still be filling the form).
 */
export default function Onboarding({ ready, onComplete }) {
  const [name, setName] = useState('');
  const [style, setStyle] = useState('direct');
  const [scenes, setScenes] = useState([]);

  const toggleScene = useCallback((v) => {
    setScenes((prev) =>
      prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]
    );
  }, []);

  const handleSubmit = useCallback(() => {
    const data = {
      name: name.trim(),
      style,
      scenes,
      ts: Date.now(),
    };
    try {
      localStorage.setItem(ONBOARDING_KEY, JSON.stringify(data));
      localStorage.setItem(ONBOARDING_DONE_KEY, '1');
    } catch (_) {}
    onComplete?.(data);
  }, [name, style, scenes, onComplete]);

  const handleSkip = useCallback(() => {
    try {
      localStorage.setItem(ONBOARDING_DONE_KEY, '1');
    } catch (_) {}
    onComplete?.(null);
  }, [onComplete]);

  return (
    <div className="onboarding">
      <div className="onboarding-spinner">
        <div className="spinner-ring" />
        {/* avatar img rendered by parent; we only need the ring here */}
      </div>

      <h2 className="onboarding-title">Chaos</h2>
      <p className="onboarding-subtitle">花几秒告诉我，让我更懂你</p>

      <div className="onboarding-form">
        {/* Q1: 称呼 */}
        <label className="ob-label">
          怎么称呼你？
          <input
            className="ob-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="留空就叫我「你好」"
            maxLength={20}
          />
        </label>

        {/* Q2: 风格 */}
        <label className="ob-label">偏好回复风格</label>
        <div className="ob-chips">
          {STYLE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`ob-chip ${style === opt.value ? 'active' : ''}`}
              onClick={() => setStyle(opt.value)}
              title={opt.desc}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Q3: 场景 */}
        <label className="ob-label">平时用得最多（可多选）</label>
        <div className="ob-chips ob-chips-multi">
          {SCENE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`ob-chip ${scenes.includes(opt.value) ? 'active' : ''}`}
              onClick={() => toggleScene(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="onboarding-actions">
        <button className="ob-btn ob-btn-skip" onClick={handleSkip}>
          跳过
        </button>
        <button
          className={`ob-btn ob-btn-go ${ready ? '' : 'disabled'}`}
          onClick={handleSubmit}
          disabled={!ready}
          title={!ready ? '等待后端就绪…' : '开始使用'}
        >
          {ready ? '开始使用 →' : '正在准备环境…'}
        </button>
      </div>

      {!ready && (
        <p className="ob-footer-hint">后台正在初始化，准备好后即可进入</p>
      )}
    </div>
  );
}
