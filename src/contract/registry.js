// Manifest registry (L1 discovery). The backend /api/ag-ui/contract/manifests
// is the source of truth when reachable; the bundled fallback keeps the UI
// usable offline. Both are pure DATA generated at build time from the agent
// manifest.json files (see scripts/gen-contract.mjs): adding a workflow never
// touches a component or branch here.
import { manifests as bundledManifests, allowedIds as bundledAllowedIds } from "./manifests.generated.js";

const ALLOWED_IDS = new Set(bundledAllowedIds);

let manifests = bundledManifests.slice();
let initialized = false;

export function setManifests(list) {
  if (Array.isArray(list) && list.length) {
    // Deduplicate by id + whitelist: keep only allowed production workflows.
    const seen = new Set();
    const filtered = list.filter((m) => {
      if (!m.id || !ALLOWED_IDS.has(m.id)) return false;
      if (seen.has(m.id)) return false;
      seen.add(m.id);
      return true;
    });
    // Merge into bundled manifests: backend is source of truth for runtime
    // fields (input_schema, output_schema, capabilities), but bundled carries
    // frontend-only metadata such as ui.component/name that the backend must
    // not need to know about.
    if (filtered.length) {
      const byId = new Map(manifests.map((m) => [m.id, m]));
      for (const m of filtered) {
        const existing = byId.get(m.id);
        byId.set(m.id, existing ? { ...existing, ...m } : m);
      }
      manifests = Array.from(byId.values());
    }
  }
  // IMPORTANT: manifests is NEVER cleared to []. Worst case = bundled fallback.
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
    const timer = setTimeout(() => controller.abort(), 3000); // increased from 1.5s
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
      console.debug("[registry] backend manifest fetch failed, using bundled:", err.message || err);
    } finally {
      clearTimeout(timer);
    }
  }
  console.debug("[registry] initContract complete, manifests:", manifests.map(m => m.id));
  return manifests;
}
