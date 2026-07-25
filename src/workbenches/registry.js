// Workbench component registry.
//
// A "workbench" is a dedicated, workflow-specific UI (node-graph, blueprint,
// timeline, custom) that replaces the generic chat surface when a manifest's
// `ui.type` is not "chat" / "form". Components register here by name; the
// frontend router (ChatLayout) resolves `manifest.ui.component` against this
// map and renders the result with zero per-workflow branching.
//
// Adding a new specialized workbench = write the component + register it below
// + set the manifest's `ui` field. No router code changes.

import ManjuCraftWorkbench from "./ManjuCraftWorkbench.jsx";
// Generic, data-driven renderers (spec P4): a new workflow only needs a
// `graph` in its manifest + ui.type "blueprint"/"timeline" — no new component.
import BlueprintWorkbench from "./BlueprintWorkbench.jsx";
import TimelineWorkbench from "./TimelineWorkbench.jsx";

const WORKBENCHES = {
  // Hand-written first-version workbench (spec decision ③) for the manju_craft
  // video pipeline. Each node exposes a per-step artifact slot so the "every
  // step's output is observable" UX is verifiable before the backend streams
  // real workflow.* events.
  ManjuCraftWorkbench,
  // Generic renderers driven entirely by manifest.graph.
  BlueprintWorkbench,
  TimelineWorkbench,
};

// Returns the registered component, or null if unregistered/unknown.
export function getWorkbench(componentName) {
  if (!componentName) return null;
  return WORKBENCHES[componentName] || null;
}

export default WORKBENCHES;
