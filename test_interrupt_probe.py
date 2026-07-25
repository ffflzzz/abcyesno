import asyncio
import sys
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command
from langgraph.errors import GraphInterrupt

class S(TypedDict):
    x: int

def node_a(state: S):
    return {"x": state["x"] + 1}

def gate(state: S):
    print("  [gate] calling interrupt")
    decision = interrupt({"gate_id": "first_frame", "label": "首帧确认", "value": state["x"]})
    print("  [gate] resumed with:", decision)
    if isinstance(decision, dict) and decision.get("decision") == "reject":
        raise RuntimeError("USER_REJECTED")
    return {"x": state["x"] + 100}

def node_b(state: S):
    return {"x": state["x"] + 1}

b = StateGraph(S)
b.add_node("node_a", node_a)
b.add_node("gate", gate)
b.add_node("node_b", node_b)
b.add_edge(START, "node_a")
b.add_edge("node_a", "gate")
b.add_edge("gate", "node_b")
b.add_edge("node_b", END)
graph = b.compile(checkpointer=MemorySaver())

cfg = {"configurable": {"thread_id": "probe-1"}}

print("=== TEST 1: sync for over graph.astream ===")
try:
    for st in graph.astream({"x": 0}, config=cfg):
        print("  sync chunk:", type(st), st)
except Exception as e:
    print("  SYNC FOR FAILED:", type(e).__name__, e)

print("=== TEST 2: async for over graph.astream (interrupt handling) ===")
async def run():
    pending = {"x": 0}
    for attempt in range(3):
        print(f"-- attempt {attempt} --")
        try:
            async for st in graph.astream(pending, config=cfg):
                if isinstance(st, dict) and "__interrupt__" in st:
                    interrupts = st["__interrupt__"]
                    print("  INTERRUPT CHUNK:", interrupts)
                    for intr in interrupts:
                        print("    interrupt.value:", intr.value, "id:", intr.id)
                    # simulate frontend decision
                    decision = {"decision": "approve"} if attempt < 1 else "approve"
                    pending = Command(resume=decision)
                    break
                else:
                    print("  chunk:", st)
            else:
                print("  stream finished normally")
                break
        except GraphInterrupt as gi:
            print("  GraphInterrupt raised:", gi.args)
            break
    # final state
    print("  FINAL:", graph.get_state(cfg).values)

asyncio.run(run())
print("DONE")
