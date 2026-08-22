import asyncio, os, sys, time, urllib.request

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

PROJ = r"C:/Users/Administrator/.hermes_portable_data/manjucraft_agent/projects/manjucraft-series-20260822-162034/ep000"
KF = os.path.join(PROJ, "keyframes")
FIRST = os.path.join(KF, "shot_000.png")
SECOND = os.path.join(KF, "_kf_demo_second.png")
RESULT = os.path.join(PROJ, "_kf_transition_demo.mp4")

async def gen_second():
    if os.path.exists(SECOND):
        print("second keyframe already exists, skip")
        return
    prompt = ("The exact same wet rooftop at sunset scene moments later: "
              "the sun has dipped lower near the horizon, most pigeons have flown away leaving only a few on the railing, "
              "the sky shifts from golden-red to deep warm purple, same rooftop and city skyline, same atmospheric after-rain mood")
    await am.generate_image(prompt, size="1024x576", output_path=SECOND)
    print("second keyframe generated:", SECOND)

async def create_with_retry(prompt, keyframes, key, max_wait=600):
    deadline = time.time() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            vid = await am.create_video(prompt, keyframes=keyframes,
                                        num_frames=121, frame_rate=24, api_key=key)
            return vid
        except Exception as e:
            msg = str(e)
            if "video_queue_full" in msg or "503" in msg or "429" in msg:
                if time.time() > deadline:
                    print("still rate-limited after %ds, giving up" % max_wait)
                    raise
                print(f"[attempt {attempt}] rate-limited ({msg[:60]}), retry in 65s...")
                await asyncio.sleep(65)
                continue
            raise

async def main():
    am.MOCK = False
    key = am._fallback_api_key()
    if not key:
        print("WARN: no fallback key, falling back to primary")
        key = am._primary_api_key()
    print("using key:", (key or "NONE")[:12], "...")
    await gen_second()

    prompt = ("A smooth cinematic time-lapse on a wet rooftop at sunset, the sun slowly dipping toward the horizon, "
              "birds gradually flying away, the atmosphere transitions from bright golden to deep warm purple, "
              "maintain visual consistency and the same scene throughout")
    print("== create_video (keyframes mode) ==")
    vid = await create_with_retry(prompt, [FIRST, SECOND], key)
    print("video_id:", vid)

    print("== poll_video ==")
    url = await am.poll_video(vid, api_key=key)
    print("url:", url)

    print("== download ==")
    urllib.request.urlretrieve(url, RESULT)
    print("saved:", RESULT, os.path.getsize(RESULT), "bytes")

asyncio.run(main())
