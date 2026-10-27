#!/usr/bin/env node
/**
 * p5.js Skill — Headless Frame Export
 *
 * Captures frames from a p5.js sketch using Puppeteer (headless Chrome).
 * Uses noLoop() + redraw() for DETERMINISTIC frame-by-frame control.
 *
 * IMPORTANT: Your sketch must call noLoop() in setup() and set
 * window._p5Ready = true when initialized. This script calls redraw()
 * for each frame capture, ensuring exact 1:1 correspondence between
 * frameCount and captured frames.
 *
 * If the sketch does NOT set window._p5Ready, the script falls back to
 * a timed capture mode (less precise, may drop/duplicate frames).
 * Local assets are served from the real sketch directory via request interception,
 * without disabling browser web security.
 *
 * Usage:
 *   node export-frames.js sketch.html [options]
 *
 * Options:
 *   --output <dir>    Output directory (default: ./frames)
 *   --width <px>      Canvas width (default: 1920)
 *   --height <px>     Canvas height (default: 1080)
 *   --frames <n>      Number of frames to capture (default: 1)
 *   --fps <n>         Target FPS for timed fallback mode (default: 30)
 *   --wait <ms>       Wait before first capture (default: 2000)
 *   --selector <sel>  Canvas CSS selector (default: canvas)
 *
 * Examples:
 *   node export-frames.js sketch.html --frames 1                     # single PNG
 *   node export-frames.js sketch.html --frames 300 --fps 30          # 10s at 30fps
 *   node export-frames.js sketch.html --width 3840 --height 2160     # 4K still
 *
 * Sketch template for deterministic capture:
 *   function setup() {
 *     createCanvas(1920, 1080);
 *     pixelDensity(1);
 *     noLoop();                    // REQUIRED for deterministic capture
 *     window._p5Ready = true;      // REQUIRED to signal readiness
 *   }
 *   function draw() { ... }
 */

const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

// Parse CLI arguments
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    input: null,
    output: './frames',
    width: 1920,
    height: 1080,
    frames: 1,
    fps: 30,
    wait: 2000,
    selector: 'canvas',
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2);
      const val = args[i + 1];
      if (key in opts && val !== undefined) {
        opts[key] = isNaN(Number(val)) ? val : Number(val);
        i++;
      }
    } else if (!opts.input) {
      opts.input = args[i];
    }
  }

  if (!opts.input) {
    console.error('Usage: node export-frames.js <sketch.html> [options]');
    process.exit(1);
  }

  return opts;
}

// Keep the host policy aligned with tools/browser_tool_session.py::
// _needs_chromium_sandbox_bypass / CHROMIUM_SANDBOX_BYPASS_ARGS; do not import the core.
function sandboxBypassReason() {
  if (process.geteuid && process.geteuid() === 0) return 'running as root';
  if (fs.existsSync('/.dockerenv')) return 'running in Docker';
  try {
    if (fs.readFileSync('/proc/1/cgroup', 'utf8').includes('docker')) return 'running in Docker';
  } catch {}
  try {
    if (fs.readFileSync('/proc/sys/kernel/apparmor_restrict_unprivileged_userns', 'utf8').trim() === '1') {
      return 'AppArmor restricts unprivileged user namespaces';
    }
  } catch {}
  return null;
}

async function browserLaunchOptions() {
  const options = { headless: 'new', args: ['--disable-gpu'] };
  // Puppeteer 24 returns an array from defaultArgs(); 25.x returns a Promise.
  // `await` accepts both.
  const nativeArgs = await puppeteer.defaultArgs(options);
  // A user-supplied binary may have a working setuid sandbox: never auto-bypass it.
  // The instance's configuration is an object on Puppeteer 24 and a method on
  // 25.x; the environment variable is authoritative on every version.
  const puppeteerInstance = (puppeteer.default && typeof puppeteer.default === 'object')
    ? puppeteer.default
    : puppeteer;
  let configExecutable;
  try {
    const resolvedConfig = typeof puppeteerInstance.configuration === 'function'
      ? puppeteerInstance.configuration()
      : puppeteerInstance.configuration;
    if (resolvedConfig && typeof resolvedConfig === 'object' && typeof resolvedConfig.executablePath === 'string') {
      configExecutable = resolvedConfig.executablePath;
    }
  } catch {
    configExecutable = undefined;
  }
  const userExecutable = process.env.PUPPETEER_EXECUTABLE_PATH || configExecutable;
  if (nativeArgs.includes('--no-sandbox')) {
    console.error('Warning: Chrome sandbox disabled by explicit native Puppeteer configuration (PUPPETEER_DANGEROUS_NO_SANDBOX=true on supported versions).');
    console.error('  Use only with independently verified external isolation; this script cannot verify it.');
  } else if (!userExecutable) {
    const reason = sandboxBypassReason();
    if (reason) {
      for (const flag of ['--no-sandbox', '--disable-dev-shm-usage']) {
        if (!nativeArgs.includes(flag)) options.args.push(flag);
      }
      console.error(`Warning: Chrome sandbox disabled automatically: ${reason} (Puppeteer-managed Chrome; same host policy as the core browser launcher).`);
      console.error('  Use a verified sandbox-capable Chrome via PUPPETEER_EXECUTABLE_PATH to avoid automatic bypass.');
    }
  }
  return options;
}

function sandboxFailureHint(err) {
  if (!/sandbox/i.test(err.message)) return;
  console.error('Chrome reported a sandbox startup failure. No unsafe retry is attempted.');
  console.error('Use an existing verified Chrome via PUPPETEER_EXECUTABLE_PATH, or ask an administrator to follow:');
  console.error('https://pptr.dev/troubleshooting#setting-up-chrome-linux-sandbox');
  console.error('Only with independently verified external isolation, use PUPPETEER_DANGEROUS_NO_SANDBOX=true on a supporting Puppeteer version (24.40.0+).');
}

// Native interception adds no listener/server. Other origins retain Chrome's
// normal security rules; this is a local asset root, not a network firewall.
const SKETCH_ORIGIN = 'http://p5-sketch.localhost';
const MIME = {
  '.html': 'text/html', '.htm': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.txt': 'text/plain', '.csv': 'text/csv',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ttf': 'font/ttf', '.otf': 'font/otf',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
  '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm', '.wasm': 'application/wasm',
};

async function serveSketchDir(page, dir, isClosing = () => false) {
  const root = fs.realpathSync(dir);
  let requestError = null;
  await page.setRequestInterception(true);
  page.on('request', req => {
    // EventEmitter does not await async handlers. Catch both native resolution
    // methods, including a request disappearing while the browser is closing.
    (async () => {
      const url = new URL(req.url());
      if (url.origin !== SKETCH_ORIGIN) {
        await req.continue();
        return;
      }
      let response = { status: 404, contentType: 'text/plain', body: 'Not found' };
      try {
        const rel = decodeURIComponent(url.pathname);
        if (!rel.includes('\\') && !rel.includes('\0') &&
            !rel.split('/').some(seg => seg.startsWith('.'))) {
          const real = fs.realpathSync(path.join(root, rel));
          const within = path.relative(root, real);
          if (within && !path.isAbsolute(within) &&
              !within.split(path.sep).some(seg => seg.startsWith('.')) && fs.statSync(real).isFile()) {
            response = { status: 200,
              contentType: MIME[path.extname(real).toLowerCase()] || 'application/octet-stream',
              body: fs.readFileSync(real) };
          }
        }
      } catch (err) {
        // Missing/invalid paths (including a file removed after realpath) are
        // ordinary 404s. Unexpected I/O errors must not become a successful run.
        if (!(err instanceof URIError) && !['ENOENT', 'ENOTDIR', 'ELOOP', 'EINVAL'].includes(err.code)) throw err;
      }
      await req.respond(response);
    })().catch(err => {
      if (isClosing() || page.isClosed()) return;
      if (!requestError) {
        requestError = new Error(`Sketch request interception failed: ${err.message}`);
        console.error(`Error: ${requestError.message}`);
        // A rejected resolution can leave navigation waiting. Closing this
        // run's page makes that failure visible promptly; browser cleanup stays
        // with the caller, never with this handler.
        Promise.resolve().then(() => page.close()).catch(closeErr => {
          console.error(`Warning: failed to close sketch page: ${closeErr.message}`);
        });
      }
    });
  });
  return () => { if (requestError) throw requestError; };
}

async function main() {
  const opts = parseArgs();
  const inputPath = path.resolve(opts.input);

  if (!fs.existsSync(inputPath)) {
    console.error(`File not found: ${inputPath}`);
    process.exit(1);
  }

  // Create output directory
  fs.mkdirSync(opts.output, { recursive: true });

  console.log(`Capturing ${opts.frames} frame(s) from ${opts.input}`);
  console.log(`Resolution: ${opts.width}x${opts.height}`);
  console.log(`Output: ${opts.output}/`);

  let browser;
  try {
    browser = await puppeteer.launch(await browserLaunchOptions());
  } catch (err) {
    sandboxFailureHint(err);
    throw err;
  }
  let closing = false;
  try {
    const page = await browser.newPage();

    await page.setViewport({
      width: opts.width,
      height: opts.height,
      deviceScaleFactor: 1,
    });

    const checkRequests = await serveSketchDir(page, path.dirname(inputPath), () => closing);
    const sketchUrl = `${SKETCH_ORIGIN}/${encodeURIComponent(path.basename(inputPath))}`;
    await page.goto(sketchUrl, { waitUntil: 'networkidle0', timeout: 30000 });
    checkRequests();

    // Wait for canvas to appear
    await page.waitForSelector(opts.selector, { timeout: 10000 });

    // Detect capture mode: deterministic (noLoop+redraw) vs timed (fallback)
    let deterministic = false;
    try {
      await page.waitForFunction('window._p5Ready === true', { timeout: 5000 });
      deterministic = true;
      console.log(`Mode: deterministic (noLoop + redraw)`);
    } catch {
      console.log(`Mode: timed fallback (sketch does not set window._p5Ready)`);
      console.log(`  For frame-perfect capture, add noLoop() and window._p5Ready=true to setup()`);
      await new Promise(r => setTimeout(r, opts.wait));
    }

    const startTime = Date.now();

    for (let i = 0; i < opts.frames; i++) {
      checkRequests();
      if (deterministic) {
        // Advance exactly one frame
        await page.evaluate(() => { redraw(); });
        // Brief settle time for render to complete
        await new Promise(r => setTimeout(r, 20));
      }

      const frameName = `frame-${String(i).padStart(4, '0')}.png`;
      const framePath = path.join(opts.output, frameName);

      // Capture the canvas element
      const canvas = await page.$(opts.selector);
      if (!canvas) {
        console.error('Canvas element not found');
        break;
      }

      await canvas.screenshot({ path: framePath, type: 'png' });
      checkRequests();

      // Progress
      if (i % 30 === 0 || i === opts.frames - 1) {
        const pct = ((i + 1) / opts.frames * 100).toFixed(1);
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        process.stdout.write(`\r  Frame ${i + 1}/${opts.frames} (${pct}%) — ${elapsed}s`);
      }

      // In timed mode, wait between frames
      if (!deterministic && i < opts.frames - 1) {
        await new Promise(r => setTimeout(r, 1000 / opts.fps));
      }
    }

    checkRequests();
    console.log('\n  Done.');

  } finally {
    closing = true;
    await browser.close();
  }
}

main().catch(err => {
  console.error('Error:', err.message);
  process.exit(1);
});
