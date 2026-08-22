import asyncio, os, sys
sys.path.insert(0, "hermes-fork/skills/langgraph_agents/agents/manjucraft_agent")
from mc_services import agnes_media as am

K0 = r"C:/Users/Administrator/.hermes_portable_data/manjucraft_agent/projects/manjucraft-series-20260822-162034/ep000/keyframes/shot_000.png"
K1 = r"C:/Users/Administrator/.hermes_portable_data/manjucraft_agent/projects/manjucraft-series-20260822-162034/ep000/keyframes/shot_001.png"

async def main():
    am.MOCK = False
    key = am._fallback_api_key()
    print("using key:", (key or "NONE")[:12], "...")
    try:
        vid = await am.create_video(
            "Smooth transition from first keyframe to second, keeping character identity",
            keyframes=[K0, K1],
            num_frames=81, frame_rate=24,
            api_key=key,
        )
        print("RESULT video_id:", vid)
        print("=> MODEL ACCEPTED keyframes mode (returned a task id)")
    except Exception as e:
        print("ERROR:", repr(e))
        if hasattr(e, "response"):
            try:
                print("RESP BODY:", e.response.text[:600])
            except Exception:
                pass

asyncio.run(main())
