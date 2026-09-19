// 把 BrowserPanel 里的 `import ... from "react"` 接到 mini-react（无 DOM 的钩子运行时）。
// 与 scripts/test-multisession 共用同一套实现，避免两套假 React 行为漂移。
export {
  useState,
  useRef,
  useCallback,
  useMemo,
  useEffect,
} from "../test-multisession/mini-react.mjs";

import * as hooks from "../test-multisession/mini-react.mjs";

export default hooks;
