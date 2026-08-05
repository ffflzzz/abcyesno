// In-memory mock of src/hooks/uiBus.js for the multi-session regression harness.
// The harness drives the REAL useAgentStream.js (which imports ./uiBus.js) against
// a fake SSE server; this mock keeps the toast bus side-effect-free so tests stay
// deterministic. Not shipped — lives only under scripts/test-multisession.
const showListeners = new Set();
const clearListeners = new Set();

export function onToastShow(cb) {
  showListeners.add(cb);
  return () => showListeners.delete(cb);
}
export function onToastClear(cb) {
  clearListeners.add(cb);
  return () => clearListeners.delete(cb);
}
export function emitToastShow(payload) {
  showListeners.forEach((cb) => cb(payload));
}
export function emitToastClear(payload) {
  clearListeners.forEach((cb) => cb(payload));
}
