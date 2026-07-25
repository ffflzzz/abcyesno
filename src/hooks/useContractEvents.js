import { useEffect, useState } from "react";
import { subscribeContractEvents, getContractEvents } from "../contract/eventBus.js";

// Subscribe to contract (workflow.*) events for a given runId. The adapter
// pushes AG-UI CUSTOM events into the bus; this hook feeds them to the
// generic renderers. No per-workflow logic.
export function useContractEvents(runId) {
  const [events, setEvents] = useState(() => getContractEvents(runId));
  useEffect(() => {
    setEvents(getContractEvents(runId));
    const unsub = subscribeContractEvents((rid, ev) => {
      if (rid === runId) setEvents(getContractEvents(runId));
    });
    return unsub;
  }, [runId]);
  return events;
}
