// Minimal React hooks runtime — just enough to drive a single function
// component (useAgentStream's host) outside of a DOM. Supports useState,
// useRef, useCallback, useEffect, plus render-phase setState.

let hooks = [];
let cursor = 0;
let renderFn = null;
let scheduled = false;
let renderingNow = false;
let needsRerender = false;
let pendingEffects = [];

export function useState(initial) {
  const i = cursor++;
  if (hooks.length <= i) {
    hooks[i] = { type: "state", value: typeof initial === "function" ? initial() : initial };
  }
  const h = hooks[i];
  const setter = (next) => {
    const v = typeof next === "function" ? next(h.value) : next;
    if (Object.is(v, h.value)) return;
    h.value = v;
    if (renderingNow) needsRerender = true;
    else schedule();
  };
  return [h.value, setter];
}

export function useRef(initial) {
  const i = cursor++;
  if (hooks.length <= i) hooks[i] = { type: "ref", value: { current: initial } };
  return hooks[i].value;
}

function depsEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (!Object.is(a[i], b[i])) return false;
  return true;
}

export function useCallback(fn, deps) {
  const i = cursor++;
  if (hooks.length <= i) {
    hooks[i] = { type: "cb", fn, deps };
    return fn;
  }
  const h = hooks[i];
  if (!depsEqual(h.deps, deps)) {
    h.fn = fn;
    h.deps = deps;
  }
  return h.fn;
}

export function useMemo(factory, deps) {
  const i = cursor++;
  if (hooks.length <= i) {
    hooks[i] = { type: "memo", value: factory(), deps };
    return hooks[i].value;
  }
  const h = hooks[i];
  if (!depsEqual(h.deps, deps)) {
    h.value = factory();
    h.deps = deps;
  }
  return h.value;
}

export function useEffect(fn, deps) {
  const i = cursor++;
  if (hooks.length <= i) {
    hooks[i] = { type: "effect", deps: undefined, cleanup: undefined };
  }
  const h = hooks[i];
  if (!depsEqual(h.deps, deps)) {
    pendingEffects.push(() => {
      if (typeof h.cleanup === "function") h.cleanup();
      h.cleanup = fn();
    });
    h.deps = deps;
  }
}

function schedule() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    doRender();
  });
}

let lastResult = null;

function doRender() {
  let guard = 0;
  do {
    needsRerender = false;
    cursor = 0;
    renderingNow = true;
    try {
      lastResult = renderFn();
    } finally {
      renderingNow = false;
    }
    guard += 1;
    if (guard > 25) throw new Error("render loop did not settle");
  } while (needsRerender);

  const effects = pendingEffects;
  pendingEffects = [];
  for (const e of effects) e();
  return lastResult;
}

export function mount(fn) {
  hooks = [];
  renderFn = fn;
  return doRender();
}

export function getResult() {
  return lastResult;
}

/** Force a synchronous re-render (used after changing external props). */
export function rerender() {
  return doRender();
}

export function unmount() {
  for (const h of hooks) {
    if (h.type === "effect" && typeof h.cleanup === "function") h.cleanup();
  }
  hooks = [];
  renderFn = null;
}

export default { useState, useRef, useCallback, useMemo, useEffect };
