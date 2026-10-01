// Renders automation/animation/template.html + a script JSON to an MP4.
//   node automation/animation/render.mjs <script.json> <out.mp4> [--fps 30] [--audio voice.mp3]
// Needs: `npm i playwright` (or global), ffmpeg on PATH (or FFMPEG env), Chromium
// (PLAYWRIGHT_BROWSERS_PATH or CHROMIUM_PATH env if not the default one).
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright'); // CJS require honours NODE_PATH for a global install
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const args = process.argv.slice(2);
const flag = (n, d) => { const i = args.indexOf(n); return i < 0 ? d : args.splice(i, 2)[1]; };
const fps = Number(flag('--fps', 30));
const audio = flag('--audio', null);
const [scriptPath, outPath] = args;
if (!scriptPath || !outPath) { console.error('usage: render.mjs <script.json> <out.mp4> [--fps N] [--audio file]'); process.exit(1); }

const here = dirname(fileURLToPath(import.meta.url));
const script = JSON.parse(readFileSync(scriptPath, 'utf8'));
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
await page.addInitScript(s => { window.SCRIPT = s; }, script);
await page.goto(pathToFileURL(join(here, 'template.html')).href);
await page.waitForFunction('window.READY === true');
const total = await page.evaluate('window.TOTAL');
const frames = Math.round(total * fps);

const ff = ['-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-'];
if (audio) ff.push('-i', resolve(audio));
ff.push('-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'medium', '-crf', '18');
if (audio) ff.push('-c:a', 'aac', '-shortest');
ff.push('-movflags', '+faststart', resolve(outPath));
const enc = spawn(process.env.FFMPEG || 'ffmpeg', ff, { stdio: ['pipe', 'inherit', 'inherit'] });
const done = new Promise((res, rej) => { enc.on('exit', c => c === 0 ? res() : rej(new Error('ffmpeg exit ' + c))); enc.on('error', rej); });

for (let i = 0; i < frames; i++) {
  await page.evaluate(t => window.seek(t), i / fps);
  const buf = await page.screenshot({ type: 'jpeg', quality: 92 });
  if (!enc.stdin.write(buf)) await new Promise(r => enc.stdin.once('drain', r));
}
enc.stdin.end();
await done;
await browser.close();
console.log(`wrote ${outPath} (${total.toFixed(1)}s, ${frames} frames)`);
