import React from "react";
import { createRoot } from "react-dom/client";
import BackgroundProcessBar from "../../src/components/BackgroundProcessBar.jsx";

// 用当天真实观测到的形状喂数据（recover_v4 那条在跑的 + 一条已跑完的）。
// 心跳判据验收用的两个在跑样本：一个刚有输出，一个已经 15 分钟没动。
const RUNNING = [
  {
    session_id: "proc_8cd496f494db",
    command:
      'cd "C:/Users/Administrator/Downloads/abcyesno-v8/release/win-unpacked/shortdrama"\nrm -f .tmp/recover.log\npython .tmp/recover_v4.py 2>&1 | tee .tmp/recover_v4_stdout.log',
    cwd: "C:\\Users\\Administrator\\Downloads\\abcyesno-v8\\shortdrama",
    pid: 7652,
    started_at: "2026-10-01T16:38:41",
    uptime_seconds: 1380,
    status: "running",
    _silentSeconds: 4,
    output_tail: "[16:40:55]   reviewer OK（130s）pass=True rerun=[]\n\n",
  },
];

const STALE = [
  {
    session_id: "proc_stale",
    command: "python .tmp/recover_chain.py",
    cwd: "C:\\Users\\Administrator\\shortdrama",
    pid: 21280,
    started_at: "2026-10-01T15:39:29",
    uptime_seconds: 2700,
    status: "running",
    _silentSeconds: 900,
    output_tail: "[15:54:56] ▶ 媒体链 第 1 集\n",
  },
];

const FINISHED = [
  {
    session_id: "proc_65084304522e",
    command: "python .tmp/recover_v3.py",
    cwd: "C:\\Users\\Administrator\\shortdrama",
    pid: 18172,
    started_at: "2026-10-01T16:20:03",
    uptime_seconds: 803,
    status: "exited",
    exit_code: 0,
    output_tail: "[16:33:26] DRIVER-DONE",
  },
];

function App() {
  return (
    <div style={{ background: "#0f1419", minHeight: "100vh", padding: "16px 0" }}>
      <div id="case-running">
        <BackgroundProcessBar running={RUNNING} justFinished={[]} />
      </div>
      <div id="case-stale">
        <BackgroundProcessBar running={STALE} justFinished={[]} />
      </div>
      <div id="case-finished">
        <BackgroundProcessBar running={[]} justFinished={FINISHED} onDismiss={() => {}} />
      </div>
      <div id="case-empty">
        <BackgroundProcessBar running={[]} justFinished={[]} />
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
