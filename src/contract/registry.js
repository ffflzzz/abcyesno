// Manifest registry (L1 discovery). The backend /api/ag-ui/contract/manifests
// is the source of truth when reachable; a bundled fallback keeps the UI usable
// offline. Both are pure DATA: adding a workflow = adding one manifest object,
// never a new component or branch.
import bundledManifests from "./manifests.js";

let manifests = bundledManifests.slice();
let initialized = false;

export function setManifests(list) {
  if (Array.isArray(list) && list.length) {
    manifests = list;
  }
}

export function listManifests() {
  return manifests;
}

export function getManifest(id) {
  return manifests.find((m) => m.id === id) || null;
}

// Pull manifests from the adapter. Falls back silently to the bundled set.
export async function initContract(aguiPort) {
  if (initialized) return manifests;
  initialized = true;
  if (aguiPort) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1500);
    try {
      const res = await fetch(
        `http://127.0.0.1:${aguiPort}/api/ag-ui/contract/manifests`,
        { signal: controller.signal }
      );
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.manifests) && data.manifests.length) {
          setManifests(data.manifests);
        }
      }
    } catch (err) {
      // offline: keep bundled fallback
    } finally {
      clearTimeout(timer);
    }
  }
  return manifests;
}
