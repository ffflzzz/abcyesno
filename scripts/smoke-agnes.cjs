// Smoke test for the Agnes Node client used by the Studio workbench.
// Validates real generation: image (fast) and video (async poll).
// In sandboxed envs Node's global fetch ignores HTTPS_PROXY, so we route it
// through the proxy here for the test only (production has direct egress).
const { ProxyAgent, setGlobalDispatcher } = require('undici');
if (process.env.HTTPS_PROXY || process.env.https_proxy) {
  setGlobalDispatcher(new ProxyAgent(process.env.HTTPS_PROXY || process.env.https_proxy));
}
const agnes = require('../electron/backend/agnes');

async function main() {
  const mode = process.argv[2] || 'image';
  if (mode === 'image') {
    console.log('[smoke] generateImage...');
    const url = await agnes.generateImage({
      prompt: 'test asset, simple blue cube on white background, product render',
      size: '2K',
      ratio: '1:1',
    });
    console.log('[smoke] IMAGE OK ->', url);
    return url;
  }
  if (mode === 'video') {
    const imgUrl = process.argv[3];
    console.log('[smoke] generateVideo (img2vid)...');
    const url = await agnes.generateVideo({
      prompt: 'the blue cube slowly rotates, soft lighting',
      image: imgUrl || undefined,
      width: 1152,
      height: 768,
      num_frames: 81,
      frame_rate: 24,
    });
    console.log('[smoke] VIDEO OK ->', url);
    return url;
  }
}

main().then(
  () => process.exit(0),
  (e) => { console.error('[smoke] FAIL:', e.message); process.exit(1); }
);
