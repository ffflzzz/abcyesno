#!/usr/bin/env bash
# 只读监控脚本：轮询 manjucraft_agent 真实生成任务，不重跑。
set +e
DIR="/c/Users/Administrator/.hermes_portable_data/manjucraft_agent/projects/review-橘猫听雨/ep000"
LOGDIR="/c/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/scripts"
FINAL="$DIR/final.mp4"; DRAFT="$DIR/draft_content.json"; ASSETS="$DIR/assets.zip"
MAXSEC=1500   # 25 分钟硬上限
START=$(date +%s)
LASTPROGRESS=$START
PREV_SIG=""

echo "MONITOR_START $(date +%H:%M:%S)"

while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  LOG=$(ls -t "$LOGDIR"/manju_run_*.log 2>/dev/null | head -1)

  hf=0; hd=0; ha=0; fsize=0
  [ -f "$FINAL" ] && fsize=$(stat -c%s "$FINAL" 2>/dev/null || echo 0)
  [ -f "$FINAL" ] && [ "$fsize" -gt 102400 ] && hf=1
  [ -f "$DRAFT" ] && hd=1
  [ -f "$ASSETS" ] && ha=1

  log_ok=0; log_bad=0
  if [ -n "$LOG" ]; then
    grep -q '"ok"[[:space:]]*:[[:space:]]*true' "$LOG" 2>/dev/null && log_ok=1
    grep -q '"ok"[[:space:]]*:[[:space:]]*false' "$LOG" 2>/dev/null && log_bad=1
    grep -qi 'Traceback' "$LOG" 2>/dev/null && log_bad=1
  fi

  # 进度信号：目录内容变化
  SIG=$(ls -la --time-style=+%s "$DIR" "$DIR/keyframes" "$DIR/shots" 2>/dev/null | md5sum)
  if [ "$SIG" != "$PREV_SIG" ]; then LASTPROGRESS=$NOW; PREV_SIG=$SIG; fi
  STALE=$((NOW - LASTPROGRESS))

  echo "$(date +%H:%M:%S) elapsed=${ELAPSED}s final=$hf(${fsize}B) draft=$hd assets=$ha log_ok=$log_ok log_bad=$log_bad stale=${STALE}s"

  # 完成条件
  if [ $hf -eq 1 ] && [ $hd -eq 1 ] && [ $ha -eq 1 ] && [ $log_ok -eq 1 ]; then
    echo "=== MONITOR_DONE ==="
    echo "ARTIFACTS:"
    echo "  final.mp4      = $(stat -c%s "$FINAL" 2>/dev/null) bytes"
    echo "  draft_content.json = $(stat -c%s "$DRAFT" 2>/dev/null) bytes"
    echo "  assets.zip     = $(stat -c%s "$ASSETS" 2>/dev/null) bytes"
    echo "=== LOG JSON ($LOG) ==="
    cat "$LOG"
    break
  fi

  # 错误提前终止
  if [ $log_bad -eq 1 ]; then
    echo "=== MONITOR_ERROR_DETECTED ==="
    tail -n 40 "$LOG"
    break
  fi

  # 25 分钟无进展 → 报告并退出
  if [ $ELAPSED -gt $MAXSEC ] && [ $STALE -gt 600 ]; then
    echo "=== MONITOR_STALE_TIMEOUT ==="
    echo "elapsed=${ELAPSED}s stale=${STALE}s - 长时间无进展"
    echo "--- log tail ---"; tail -n 30 "$LOG"
    echo "--- dir status ---"; ls -la "$DIR" 2>/dev/null
    break
  fi

  if [ $ELAPSED -gt $MAXSEC ]; then
    echo "=== MONITOR_HARD_TIMEOUT ==="
    echo "elapsed=${ELAPSED}s 已达 25 分钟上限"
    echo "--- log tail ---"; tail -n 30 "$LOG"
    break
  fi

  sleep 30
done
echo "MONITOR_END $(date +%H:%M:%S)"
