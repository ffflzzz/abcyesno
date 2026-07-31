// Manifest registry (L1 discovery). The backend /api/ag-ui/contract/manifests
// is the source of truth when reachable; a bundled fallback keeps the UI usable
// offline. Both are pure DATA: adding a workflow = adding one manifest object,
// never a new component or new branch.
//
// WHITELIST: only production workflows are exposed in the UI. Test/demo
// workflows (hello_agent, image_gen) were temporary validation artifacts and
// have been removed from the bundled set. If the backend still serves them
// (stale manifest.json on disk), they are filtered out here.
import bundledManifests from "./manifests.js";

const ALLOWED_IDS = new Set(bundledManifests.map((m) => m.id));

let manifests = bundledManifests.slice();
let initialized = false;

export function setManifests(list) {
  if (Array.isArray(list) && list.length) {
    // Deduplicate by id + whitelist: keep only allowed production workflows.
    const seen = new Set();
    manifests = list.filter((m) => {
      if (!m.id || !ALLOWED_IDS.has(m.id)) return false;
      if (seen.has(m.id)) return false;
      seen.add(m.id);
      return true;
    });
    // Fall back to bundled if filtering removed everything.
    if (!manifests.length) manifests = bundledManifests.slice();
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
