// Contract event bus: normalizes workflow.* events per runId/threadId.
// The adapter (agui-server) pushes AG-UI CUSTOM events here; the generic
// rendering components (WorkflowTimeline / ArtifactCard / ApprovalGate)
// subscribe by runId. This is the single fan-out point for L5 progress.
const listeners = new Set();
const byRun = new Map();

export function emitContractEvent(runId, event) {
  if (!runId || !event) return;
  if (!byRun.has(runId)) byRun.set(runId, []);
  byRun.get(runId).push(event);
  listeners.forEach((l) => {
    try {
      l(runId, event);
    } catch (err) {
      console.error("contract event listener error", err);
    }
  });
}

export function getContractEvents(runId) {
  return byRun.get(runId) || [];
}

export function clearContractEvents(runId) {
  if (runId) byRun.delete(runId);
  else byRun.clear();
}

export function subscribeContractEvents(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
