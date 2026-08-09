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

// Generic, data-driven renderers (spec P4): a new workflow only needs a
// `graph` in its manifest + ui.type "blueprint"/"timeline" — no new component.
import BlueprintWorkbench from "./BlueprintWorkbench.jsx";
import TimelineWorkbench from "./TimelineWorkbench.jsx";
// Custom short-drama production studio (script → assets → storyboard →
// edit/export a Jianying draft). Frontend authoring workbench; opened in the
// sidebar when its manifest is selected from the SkillPanel. This is the
// unified video-production front-end for the manjucraft_agent LangGraph
// pipeline — all manju_craft/manjucraft functionality converges here.
import StudioWorkbench from "./StudioWorkbench.jsx";

const WORKBENCHES = {
  // Generic renderers driven entirely by manifest.graph.
  BlueprintWorkbench,
  TimelineWorkbench,
  // Custom short-drama production studio (unified video-production frontend).
  StudioWorkbench,
};

// Returns the registered component, or null if unregistered/unknown.
export function getWorkbench(componentName) {
  if (!componentName) return null;
  return WORKBENCHES[componentName] || null;
}

export default WORKBENCHES;
