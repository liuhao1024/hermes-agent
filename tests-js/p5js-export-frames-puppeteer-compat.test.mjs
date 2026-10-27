import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import { describe, expect, test } from 'vitest'

const scriptPath = fileURLToPath(
  new URL('../skills/creative/p5js/scripts/export-frames.js', import.meta.url),
)
const docPath = fileURLToPath(
  new URL('../skills/creative/p5js/references/export-pipeline.md', import.meta.url),
)
const source = readFileSync(scriptPath, 'utf8')
const doc = readFileSync(docPath, 'utf8')
const require = createRequire(import.meta.url)

// Loads export-frames.js in a sandbox with a fake puppeteer and fake host
// facts, exporting browserLaunchOptions for direct behavioral probing.
function loadSubject({ uid = 1000, docker = false, apparmor = '0', puppeteer: puppeteerFake }) {
  const logs = []
  const io = {
    existsSync: (p) => docker && p === '/.dockerenv',
    readFileSync: (p) =>
      p === '/proc/1/cgroup'
        ? ''
        : p === '/proc/sys/kernel/apparmor_restrict_unprivileged_userns'
          ? apparmor
          : Buffer.from('{}'),
  }
  const ctx = vm.createContext({
    require: (n) => (n === 'fs' ? io : n === 'puppeteer' ? puppeteerFake : require(n)),
    process: { env: {}, geteuid: () => uid },
    console: { error: (...v) => logs.push(v.join(' ')) },
    URL,
    Buffer,
    setTimeout,
    clearTimeout,
  })
  const stop = source.lastIndexOf('\nmain().catch(')
  expect(stop).toBeGreaterThan(0)
  vm.runInContext(`${source.slice(0, stop)}\nthis.subject={browserLaunchOptions};`, ctx)
  return { browserLaunchOptions: ctx.subject.browserLaunchOptions, logs }
}

// The fake configuration callable answers both probe styles seen across
// Puppeteer generations: called with no argument it returns the whole
// configuration object (25.x method), with a key it returns that key's value.
// Puppeteer >= 25.16 also makes defaultArgs() async.
const puppeteer25Shape = ({ configurationExecutable } = {}) => ({
  default: {
    configuration: (key) =>
      key === undefined
        ? { executablePath: configurationExecutable }
        : key === 'executablePath'
          ? configurationExecutable
          : undefined,
  },
  defaultArgs: async (options) => ['--disable-gpu', ...options.args],
})

// Puppeteer <= 24: defaultArgs() is sync, configuration is an object property.
const puppeteer24Shape = ({ configurationExecutable } = {}) => ({
  default: { configuration: { executablePath: configurationExecutable } },
  defaultArgs: (options) => ['--disable-gpu', ...options.args],
})

describe('p5js export-frames Puppeteer 24/25 compat', () => {
  test('launch option construction awaits defaultArgs and probes both configuration shapes', () => {
    expect(source).toContain('await puppeteer.defaultArgs(options)')
    expect(source).toMatch(/typeof \w+.configuration === 'function'/)
    expect(source).toContain('await puppeteer.launch(await browserLaunchOptions())')
  })

  test('the alignment comment points at the live host-policy home', () => {
    expect(source).toContain('tools/browser_launcher.py')
    expect(source).toContain('needs_chromium_sandbox_bypass')
    expect(source).not.toContain('browser_tool_session.py')
  })

  test('the reference doc example awaits defaultArgs too', () => {
    expect(doc).toContain('(await puppeteer.defaultArgs({ headless: true }))')
  })

  test('async defaultArgs (25.x shape) builds options and keeps the sandbox on a normal host', async () => {
    const { browserLaunchOptions } = loadSubject({ puppeteer: puppeteer25Shape() })
    const options = await browserLaunchOptions()
    expect(options.headless).toBe('new')
    expect(options.args).not.toContain('--no-sandbox')
    expect(options.args).not.toContain('--disable-web-security')
  })

  test('object configuration (24.x shape) counts as a user-supplied binary for root', async () => {
    const { browserLaunchOptions } = loadSubject({
      uid: 0,
      puppeteer: puppeteer24Shape({ configurationExecutable: '/fixture/config-chrome' }),
    })
    const options = await browserLaunchOptions()
    expect(options.args).not.toContain('--no-sandbox')
  })

  test('functional configuration (25.x shape) counts as a user-supplied binary for root', async () => {
    const { browserLaunchOptions } = loadSubject({
      uid: 0,
      puppeteer: puppeteer25Shape({ configurationExecutable: '/fixture/config-chrome' }),
    })
    const options = await browserLaunchOptions()
    expect(options.args).not.toContain('--no-sandbox')
  })

  test('root without any user executable still gets the documented automatic bypass', async () => {
    const { browserLaunchOptions, logs } = loadSubject({
      uid: 0,
      puppeteer: puppeteer25Shape(),
    })
    const options = await browserLaunchOptions()
    expect(options.args).toContain('--no-sandbox')
    expect(options.args).toContain('--disable-dev-shm-usage')
    expect(options.args).not.toContain('--disable-web-security')
    expect(logs.join('\n')).toMatch(/sandbox disabled automatically/)
  })
})
