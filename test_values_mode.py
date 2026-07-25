import asyncio
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

class S(TypedDict):
    x: int
    status: str

def node_a(state: S):
    return {"x": state["x"] + 1, "status": "stage-a"}

def gate(state: S):
    decision = interrupt({"gate_id": "first_frame", "label": "首帧确认"})
    return {"x": state["x"] + 100, "status": "after-gate"}

def node_b(state: S):
    return {"x": state["x"] + 1, "status": "stage-b"}

b = StateGraph(S)
b.add_node("node_a", node_a)
b.add_node("gate", gate)
b.add_node("node_b", node_b)
b.add_edge(START, "node_a")
b.add_edge("node_a", "gate")
b.add_edge("gate", "node_b")
b.add_edge("node_b", END)
graph = b.compile(checkpointer=MemorySaver())
cfg = {"configurable": {"thread_id": "vm-1"}}

async def run():
    pending = {"x": 0, "status": "idle"}
    for attempt in range(2):
        print(f"-- values attempt {attempt} --")
        try:
            async for st in graph.astream(pending, config=cfg, stream_mode="values"):
                print("  chunk type:", type(st))
                if isinstance(st, dict) and "__interrupt__" in st:
                    print("  INTERRUPT:", st["__interrupt__"][0].value)
                    pending = Command(resume={"decision": "approve"})
                    break
                else:
                    print("  VALUES chunk:", st)
        except Exception as e:
            print("  EXC:", type(e).__name__, e)
            break
    print("FINAL:", graph.get_state(cfg).values)

asyncio.run(run())
print("DONE")
