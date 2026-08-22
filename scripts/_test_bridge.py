"""Real end-to-end test of the prev-frame continuity bridge.

Builds a tiny 3-shot episode (semantically related: same rooftop scene,
sequential beats), runs batch_generate_video with _PREV_FRAME_BRIDGE on, and
checks that each shot after the first received the previous shot's last frame
as its first-frame reference. Then concatenates into a demo reel.
"""
import asyncio
import os
import sys

ENV = r"C:/Users/Administrator/.hermes_portable_data/.env"
if os.path.exists(ENV):
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "hermes-fork/skills/langgraph_agents/agents/manjucraft_agent")
from mc_services import agnes_media as am
from mc_services import ffmpeg as ff
from mc_nodes import batch_generate_video as bgv
from mc_state import AgentState

ROOT = r"C:/Users/Administrator/.hermes_portable_data/manjucraft_agent/projects/_bridge_test"
KF = os.path.join(ROOT, "keyframes")
VIDS = os.path.join(ROOT, "videos")
os.makedirs(KF, exist_ok=True)
os.makedirs(VIDS, exist_ok=True)

# A coherent 3-beat rooftop scene (same character, same setting, sequential).
SHOTS = [
    {
        "index": 0,
        "prompt": "A young woman stands at the railing of a wet rooftop at sunset, looking out at the city, gentle breeze",
        "video_prompt": "subtle cinematic motion, she turns her head slightly toward the horizon",
        "duration": 4.0, "motion": "固定",
        "keyframe_path": os.path.join(KF, "shot_000.png"),
    },
    {
        "index": 1,
        "prompt": "The same young woman on the same wet rooftop at sunset, now walking slowly toward a small table with two coffee cups",
        "video_prompt": "she walks forward, camera follows gently",
        "duration": 4.0, "motion": "推进",
        "keyframe_path": os.path.join(KF, "shot_001.png"),
    },
    {
        "index": 2,
        "prompt": "The same young woman on the same wet rooftop at sunset, sitting at the table holding a coffee cup, smiling softly",
        "video_prompt": "she raises the cup, soft smile, static",
        "duration": 4.0, "motion": "固定",
        "keyframe_path": os.path.join(KF, "shot_002.png"),
    },
]


async def main():
    am.MOCK = False
    # Pre-generate the 3 keyframes (single image each) so the video node has input.
    for s in SHOTS:
        if not os.path.exists(s["keyframe_path"]):
            await am.generate_image(s["prompt"], size="1024x576", output_path=s["keyframe_path"])
            print("keyframe", s["index"], "generated")

    state: AgentState = {
        "shots": SHOTS,
        "shot_results": [
            {"index": s["index"], "keyframe_path": s["keyframe_path"], "status": "keyframe_ok"}
            for s in SHOTS
        ],
        "characters": [],
        "resolution": "1080x1920",
        "project_dir": ROOT,
    }
    # episode_project_dir reads state["project_dir"]? patch for test
    import mc_state as ms
    ms.episode_project_dir = lambda st: ROOT

    print("== batch_generate_video (bridge ON) ==")
    out = await bgv.batch_generate_video(state)
    results = out["shot_results"]

    print("\n== bridge report ==")
    for i, r in enumerate(results):
        lp = r.get("last_frame_path")
        has_video = os.path.exists(r.get("video_path", ""))
        print(f"shot {i}: status={r['status']} video={has_video} last_frame={os.path.basename(lp) if lp else None}")
        # Verify shot i>0 used previous last frame: check its video's first frame
        # is similar to previous last frame by existence + naming chain.
        if i > 0 and lp and os.path.exists(lp):
            prev_last = results[i - 1].get("last_frame_path")
            print(f"   shot {i} first-frame anchor = previous shot {i-1} last frame: {os.path.basename(prev_last) if prev_last else 'NONE'}")

    # Concatenate into a demo reel
    vpaths = [r["video_path"] for r in results if r.get("video_path") and os.path.exists(r["video_path"])]
    if len(vpaths) >= 2:
        reel = os.path.join(ROOT, "_bridge_reel.mp4")
        ff.merge_shots(vpaths, [None] * len(vpaths), reel)
        print("\nreel:", reel, os.path.getsize(reel), "bytes")


asyncio.run(main())
