// Scans public/ for the optional AI clips, audio and the capture manifest,
// and writes src/assets.generated.json for the composition. Run before studio/render.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pub = join(root, "public");
const has = (rel) => (existsSync(join(pub, rel)) ? rel : null);

const AI_SLOTS = ["01-susza", "02-nadmiar", "03-z-gory", "04-pole-swit", "05-makro", "06-final"];
const ai = Object.fromEntries(AI_SLOTS.map((s) => [s, has(`ai/${s}.mp4`)]));
const audio = { voice: has("audio/voiceover.mp3") ?? has("audio/voiceover.wav"), music: has("audio/music.mp3") ?? has("audio/music.wav") };

let manifest = null;
const mPath = join(pub, "rec", "manifest.json");
if (existsSync(mPath)) {
  manifest = JSON.parse(readFileSync(mPath, "utf8"));
  for (const [name, clip] of Object.entries(manifest.clips ?? {})) {
    if (!existsSync(join(pub, clip.file))) delete manifest.clips[name];
  }
}

const out = { ai, audio, manifest };
writeFileSync(join(root, "src", "assets.generated.json"), JSON.stringify(out, null, 2) + "\n");
const missing = AI_SLOTS.filter((s) => !ai[s]);
console.log(`[scan] AI clips: ${AI_SLOTS.length - missing.length}/${AI_SLOTS.length}${missing.length ? ` (fallback photos for: ${missing.join(", ")})` : ""}`);
console.log(`[scan] voice: ${audio.voice ?? "none"}, music: ${audio.music ?? "none"}`);
console.log(`[scan] recordings: ${manifest ? Object.keys(manifest.clips).join(", ") : "none (run pnpm capture)"}`);
