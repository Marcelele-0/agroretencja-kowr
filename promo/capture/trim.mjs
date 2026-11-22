// Finds frozen stretches in each recording (ffmpeg freezedetect) and writes `keep` segments into
// public/rec/manifest.json: a freeze longer than MIN_FREEZE keeps only its first HOLD frames, the
// final freeze keeps END_HOLD. The film has no voice-over, so waiting on screen is dead time.
// Usage: node capture/trim.mjs   (run after pnpm capture; pnpm render runs it too)
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const mPath = join(root, "public", "rec", "manifest.json");
const FPS = 30, MIN_FREEZE = 0.8, HOLD = 15, END_HOLD = 75; // the last hold leaves time to read the caption

const m = JSON.parse(readFileSync(mPath, "utf8"));
for (const [name, clip] of Object.entries(m.clips)) {
  const file = join(root, "public", clip.file);
  // freezedetect reports on stderr.
  const log = spawnSync("ffmpeg", ["-hide_banner", "-i", file, "-vf", `freezedetect=n=0.002:d=${MIN_FREEZE}`, "-map", "0:v", "-f", "null", "-"], { encoding: "utf8" }).stderr ?? "";
  const starts = [...log.matchAll(/freeze_start: ([\d.]+)/g)].map((x) => Math.round(Number(x[1]) * FPS));
  const ends = [...log.matchAll(/freeze_end: ([\d.]+)/g)].map((x) => Math.round(Number(x[1]) * FPS));
  const n = clip.durationInFrames;
  const keep = [];
  let cursor = 0;
  starts.forEach((s, i) => {
    const e = ends[i] ?? n; // no end = frozen to the end of the clip
    const hold = e >= n ? END_HOLD : HOLD;
    const cutFrom = Math.min(e, s + hold);
    if (cutFrom > cursor) keep.push([cursor, cutFrom]);
    cursor = Math.max(cursor, e);
  });
  if (cursor < n) keep.push([cursor, n]);
  clip.keep = keep.filter(([a, b]) => b - a > 0);
  const kept = clip.keep.reduce((t, [a, b]) => t + b - a, 0);
  console.log(`[trim] ${name}: ${(n / FPS).toFixed(1)} s -> ${(kept / FPS).toFixed(1)} s`);
}
writeFileSync(mPath, JSON.stringify(m, null, 2) + "\n");
