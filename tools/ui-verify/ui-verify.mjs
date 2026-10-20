#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { chromium } from 'playwright'

const EXIT = { pass: 0, fail: 1, spec: 2, env: 3 }
const DEFAULT_BASE = 'https://gba-console-dev.85.17.167.167.nip.io'
const DEFAULT_CREDENTIALS = '/private/qa-desk-verifier/credentials.private.json'
const AUTH_PATHS = [
  /\/api\/v1\/uk\/usermanagement\/token(\/refresh|\/logout)?$/,
  /\/hubs\/[^?]+\/negotiate$/,
]
const READ_ONLY_POSTS = [
  /\/batch$/,
  /\/preview$/,
  /\/choices(\/[a-z]+)?$/,
  /\/readiness$/,
  /\/recommendations\/client\/get$/,
  /\/procurement\/(cart|producer\/plan|purchase-cockpit\/cart)$/,
  /\/report\/(get\/all\/filtered|stocks\/preview|stocks\/generate)$/,
  /\/print\/documents$/,
  /\/documents?\/export$/,
]
const DEFAULT_CONSOLE_IGNORE = [
  /Failed to load resource: the server responded with a status of 404/,
  /favicon/i,
]

class SpecError extends Error {}
class EnvError extends Error {}

function usage() {
  return [
    'Usage: node ui-verify.mjs <spec.json|-> [more specs...] [options]',
    '',
    'Options:',
    '  --base <url>          console origin (default UI_VERIFY_BASE_URL or ' + DEFAULT_BASE + ')',
    '  --env dev|qa          data environment switch cookie (default from spec, else dev)',
    '  --out <dir>           evidence root (default UI_VERIFY_OUT or ./evidence)',
    '  --credentials <file>  verifier credentials JSON (default QA_VERIFIER_CREDENTIALS or ' + DEFAULT_CREDENTIALS + ')',
    '  --timeout <ms>        default action/check timeout (default 20000)',
    '  --trace               also write a Playwright trace.zip',
    '  --headed              run with a visible browser',
    '',
    'Exit codes: 0 all checks passed, 1 a check failed, 2 spec or usage error, 3 login/browser/navigation error.',
  ].join('\n')
}

function parseArgs(argv) {
  const options = { specs: [], trace: false, headed: false, timeout: 20000 }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    const next = () => {
      if (i + 1 >= argv.length) throw new SpecError(`missing value for ${arg}`)
      i += 1
      return argv[i]
    }
    if (arg === '--base') options.base = next()
    else if (arg === '--env') options.env = next()
    else if (arg === '--out') options.out = next()
    else if (arg === '--credentials') options.credentials = next()
    else if (arg === '--timeout') options.timeout = Number(next())
    else if (arg === '--trace') options.trace = true
    else if (arg === '--headed') options.headed = true
    else if (arg === '--help' || arg === '-h') options.help = true
    else if (arg.startsWith('--')) throw new SpecError(`unknown option ${arg}`)
    else options.specs.push(arg)
  }
  if (!options.help && options.specs.length === 0) throw new SpecError('no spec given')
  if (!Number.isFinite(options.timeout) || options.timeout <= 0) throw new SpecError('--timeout must be a positive number')
  if (options.env && !['dev', 'qa'].includes(options.env)) throw new SpecError('--env must be dev or qa')
  return options
}

function readSpec(source) {
  let text
  try {
    text = source === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(source, 'utf8')
  } catch (error) {
    throw new SpecError(`cannot read spec ${source}: ${error.message}`)
  }
  let spec
  try {
    spec = JSON.parse(text)
  } catch (error) {
    throw new SpecError(`spec ${source} is not valid JSON: ${error.message}`)
  }
  validateSpec(spec, source)
  return spec
}

const STEP_KINDS = ['goto', 'click', 'fill', 'press', 'select', 'hover', 'waitFor', 'waitForResponse', 'wait', 'screenshot']
const CHECK_KINDS = ['visible', 'absent', 'text', 'count', 'rowCount', 'noConsoleErrors', 'noFailedRequests', 'noBlockedRequests', 'response', 'url']

function validateSpec(spec, source) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) throw new SpecError(`${source}: spec must be a JSON object`)
  if (typeof spec.name !== 'string' || !/^[A-Za-z0-9._-]{1,80}$/.test(spec.name)) {
    throw new SpecError(`${source}: "name" is required and may contain only letters, digits, dot, underscore and dash`)
  }
  if (typeof spec.url !== 'string' || !spec.url.startsWith('/')) throw new SpecError(`${source}: "url" must be a console path starting with /`)
  if (spec.env !== undefined && !['dev', 'qa'].includes(spec.env)) throw new SpecError(`${source}: "env" must be dev or qa`)
  for (const [index, step] of (spec.steps || []).entries()) {
    const kinds = Object.keys(step).filter((key) => STEP_KINDS.includes(key))
    if (kinds.length !== 1) throw new SpecError(`${source}: steps[${index}] needs exactly one of ${STEP_KINDS.join(', ')}`)
  }
  if (!Array.isArray(spec.checks) || spec.checks.length === 0) throw new SpecError(`${source}: "checks" must be a non-empty array`)
  for (const [index, check] of spec.checks.entries()) {
    if (!CHECK_KINDS.includes(check.type)) throw new SpecError(`${source}: checks[${index}].type must be one of ${CHECK_KINDS.join(', ')}`)
    if (['visible', 'absent', 'count'].includes(check.type) && check.locator === undefined) {
      throw new SpecError(`${source}: checks[${index}] (${check.type}) needs "locator"`)
    }
    if (check.type === 'text' && typeof check.text !== 'string') throw new SpecError(`${source}: checks[${index}] (text) needs "text"`)
    if (check.type === 'response' && typeof check.urlIncludes !== 'string') throw new SpecError(`${source}: checks[${index}] (response) needs "urlIncludes"`)
    if (check.type === 'url' && typeof check.matches !== 'string') throw new SpecError(`${source}: checks[${index}] (url) needs "matches"`)
  }
}

function loadCredentials(file) {
  if (process.env.QA_VERIFIER_LOGIN && process.env.QA_VERIFIER_PASSWORD) {
    return { Login: process.env.QA_VERIFIER_LOGIN, Password: process.env.QA_VERIFIER_PASSWORD }
  }
  try {
    const credentials = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (!credentials.Login || !credentials.Password) throw new Error('Login/Password missing')
    return credentials
  } catch (error) {
    throw new EnvError(`cannot read verifier credentials from ${file}: ${error.message}`)
  }
}

function resolveLocator(scope, spec) {
  if (spec === undefined || spec === null) throw new SpecError('locator is missing')
  if (typeof spec === 'string') return scope.locator(spec)
  let locator
  if (spec.role) locator = scope.getByRole(spec.role, { name: spec.name, exact: spec.exact ?? false })
  else if (spec.testId) locator = scope.getByTestId(spec.testId)
  else if (spec.label) locator = scope.getByLabel(spec.label, { exact: spec.exact ?? false })
  else if (spec.placeholder) locator = scope.getByPlaceholder(spec.placeholder, { exact: spec.exact ?? false })
  else if (spec.text) locator = scope.getByText(spec.text, { exact: spec.exact ?? false })
  else if (spec.css) locator = scope.locator(spec.css)
  else throw new SpecError(`locator ${JSON.stringify(spec)} needs one of role, testId, label, placeholder, text, css`)
  if (spec.within) locator = resolveLocator(scope, spec.within).locator(locator)
  if (spec.hasText) locator = locator.filter({ hasText: spec.hasText })
  if (Number.isInteger(spec.nth)) locator = locator.nth(spec.nth)
  return locator
}

function describe(spec) {
  return typeof spec === 'string' ? spec : JSON.stringify(spec)
}

function isAllowedNonGet(method, url, allowList) {
  if (method !== 'POST') return false
  const pathname = new URL(url).pathname
  return [...AUTH_PATHS, ...READ_ONLY_POSTS, ...allowList].some((pattern) => pattern.test(pathname))
}

async function login(context, base, credentials, timeout) {
  let response
  try {
    response = await context.request.post(`${base}/api/v1/uk/usermanagement/token`, {
      data: { Username: credentials.Login, Password: credentials.Password },
      timeout,
    })
  } catch (error) {
    throw new EnvError(`login request failed: ${error.message}`)
  }
  if (response.status() !== 200) throw new EnvError(`login returned HTTP ${response.status()}`)
  const cookies = await context.cookies(base)
  if (!cookies.some((cookie) => cookie.name === '__Host-gba_at')) {
    throw new EnvError('login did not set the __Host-gba_at session cookie; use an https origin or http://localhost')
  }
}

async function runStep(page, step, index, evidenceDir, timeout, record) {
  const kind = STEP_KINDS.find((key) => key in step)
  const value = step[kind]
  const started = Date.now()
  const marker = record.responses.length
  const entry = { index, kind, target: describe(value) }
  try {
    if (kind === 'goto') {
      await page.goto(new URL(value, page.url()).toString(), { waitUntil: 'load', timeout: timeout * 3 })
    } else if (kind === 'click') {
      await resolveLocator(page, value).first().click({ timeout })
    } else if (kind === 'fill') {
      await resolveLocator(page, value.locator).first().fill(String(value.value), { timeout })
    } else if (kind === 'press') {
      await resolveLocator(page, value.locator).first().press(value.key, { timeout })
    } else if (kind === 'select') {
      await resolveLocator(page, value.locator).first().selectOption(value.value, { timeout })
    } else if (kind === 'hover') {
      await resolveLocator(page, value).first().hover({ timeout })
    } else if (kind === 'waitFor') {
      const target = value.locator ?? value
      await resolveLocator(page, target).first().waitFor({ state: value.state || 'visible', timeout: value.timeout || timeout })
    } else if (kind === 'waitForResponse') {
      const matches = (url, status) => url.includes(value.urlIncludes) && (value.status === undefined || status === value.status)
      const seen = record.responses.slice(record.marker || 0).some((response) => matches(response.url, response.status))
      if (!seen) {
        await page.waitForResponse((response) => matches(response.url(), response.status()), { timeout: value.timeout || timeout })
      }
    } else if (kind === 'wait') {
      await page.waitForTimeout(Number(value))
    } else if (kind === 'screenshot') {
      const file = path.join(evidenceDir, `${String(value).replace(/[^A-Za-z0-9._-]/g, '_')}.png`)
      await page.screenshot({ path: file, fullPage: true })
      entry.file = file
    }
    if (step.settle !== false && kind !== 'wait' && kind !== 'screenshot') {
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {})
    }
    entry.ok = true
  } catch (error) {
    if (error instanceof SpecError) throw error
    entry.ok = false
    entry.error = error.message.split('\n')[0]
  }
  entry.ms = Date.now() - started
  record.marker = kind === 'waitForResponse' ? record.responses.length : marker
  record.steps.push(entry)
  return entry.ok
}

async function runCheck(page, check, timeout, record) {
  const result = { type: check.type, description: check.description || null }
  try {
    if (check.type === 'visible') {
      const locator = resolveLocator(page, check.locator)
      await locator.first().waitFor({ state: 'visible', timeout: check.timeout || timeout })
      result.target = describe(check.locator)
      result.pass = true
      if (typeof check.enabled === 'boolean') {
        const enabled = await locator.first().isEnabled()
        result.enabled = enabled
        result.pass = enabled === check.enabled
      }
    } else if (check.type === 'absent') {
      const locator = resolveLocator(page, check.locator)
      if (check.waitMs) await page.waitForTimeout(check.waitMs)
      const visible = await locator.evaluateAll((nodes) => nodes.filter((node) => {
        const rect = node.getBoundingClientRect()
        const style = window.getComputedStyle(node)
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
      }).length)
      result.target = describe(check.locator)
      result.visibleCount = visible
      result.pass = visible === 0
    } else if (check.type === 'text') {
      const scope = check.within ? resolveLocator(page, check.within).first() : page.locator('body')
      const present = check.present !== false
      const deadline = Date.now() + (check.timeout || timeout)
      let found = false
      do {
        const text = await scope.innerText({ timeout: check.timeout || timeout }).catch(() => '')
        found = check.exact ? text.split('\n').some((line) => line.trim() === check.text) : text.includes(check.text)
        if (found === present) break
        await page.waitForTimeout(250)
      } while (Date.now() < deadline)
      result.text = check.text
      result.expectedPresent = present
      result.found = found
      result.pass = found === present
    } else if (check.type === 'count' || check.type === 'rowCount') {
      let locator
      if (check.type === 'count') {
        locator = resolveLocator(page, check.locator)
      } else {
        const table = check.table ? resolveLocator(page, check.table).first() : page.locator('table:visible').first()
        locator = table.locator(check.rowSelector || 'tbody tr')
        if (check.rowHasText) locator = locator.filter({ hasText: check.rowHasText })
        if (check.rowMatches) locator = locator.filter({ hasText: new RegExp(check.rowMatches) })
        result.table = check.table ? describe(check.table) : 'first visible table'
        result.rowSelector = check.rowSelector || 'tbody tr'
        result.rowHasText = check.rowHasText || null
        result.rowMatches = check.rowMatches || null
      }
      const deadline = Date.now() + (check.timeout || timeout)
      let count = 0
      const satisfied = (value) => (check.equals === undefined || value === check.equals)
        && (check.min === undefined || value >= check.min)
        && (check.max === undefined || value <= check.max)
      do {
        count = await locator.count()
        if (satisfied(count)) break
        await page.waitForTimeout(250)
      } while (Date.now() < deadline)
      result.count = count
      result.expected = { equals: check.equals, min: check.min, max: check.max }
      result.pass = satisfied(count)
      if (check.sampleRows !== false && check.type === 'rowCount') {
        result.sample = (await locator.evaluateAll((rows) => rows.slice(0, 5).map((row) => row.innerText.replace(/\s+/g, ' ').trim()))).map((text) => text.slice(0, 300))
      }
    } else if (check.type === 'noConsoleErrors') {
      const ignore = [...DEFAULT_CONSOLE_IGNORE, ...(check.ignore || []).map((pattern) => new RegExp(pattern))]
      const errors = record.console.filter((message) => message.type === 'error' || message.type === 'pageerror')
        .filter((message) => !ignore.some((pattern) => pattern.test(message.text)))
      result.errors = errors.slice(0, 20)
      result.pass = errors.length === 0
    } else if (check.type === 'noFailedRequests') {
      const statuses = check.statuses || [400, 599]
      const ignore = (check.ignore || []).map((pattern) => new RegExp(pattern))
      const failed = record.responses.filter((response) => response.status >= statuses[0] && response.status <= statuses[1])
        .filter((response) => !ignore.some((pattern) => pattern.test(response.url)))
      result.failed = failed.slice(0, 20)
      result.pass = failed.length === 0
    } else if (check.type === 'noBlockedRequests') {
      result.blocked = record.blockedRequests.slice(0, 20)
      result.pass = record.blockedRequests.length === 0
    } else if (check.type === 'response') {
      const matches = record.responses.filter((response) => response.url.includes(check.urlIncludes)
        && (!check.method || response.method === check.method.toUpperCase()))
      result.urlIncludes = check.urlIncludes
      result.seen = matches.map((response) => `${response.status} ${response.method} ${response.url}`).slice(0, 10)
      const expected = check.status ?? 200
      result.expectedStatus = expected
      result.pass = matches.length > 0 && matches.every((response) => response.status === expected)
    } else if (check.type === 'url') {
      result.url = page.url()
      result.pass = new RegExp(check.matches).test(page.url())
    }
  } catch (error) {
    if (error instanceof SpecError) throw error
    result.pass = false
    result.error = error.message.split('\n')[0]
  }
  record.checks.push(result)
  return result.pass
}

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
}

async function runSpec(browser, spec, options, credentials) {
  const env = options.env || spec.env || 'dev'
  const base = (options.base || spec.base || process.env.UI_VERIFY_BASE_URL || DEFAULT_BASE).replace(/\/$/, '')
  const outRoot = options.out || process.env.UI_VERIFY_OUT || path.resolve('evidence')
  const evidenceDir = path.resolve(outRoot, spec.name, timestamp())
  fs.mkdirSync(evidenceDir, { recursive: true })
  const timeout = spec.timeout || options.timeout
  const allowNonGet = (spec.allowNonGet || []).map((pattern) => new RegExp(pattern))
  const record = {
    spec: spec.name,
    description: spec.description || null,
    env,
    base,
    url: spec.url,
    startedAt: new Date().toISOString(),
    status: 'error',
    exitCode: EXIT.env,
    steps: [],
    checks: [],
    console: [],
    responses: [],
    blockedRequests: [],
    evidenceDir,
    marker: 0,
  }
  const context = await browser.newContext({
    viewport: spec.viewport || { width: 1600, height: 1000 },
    locale: 'uk-UA',
    timezoneId: 'Europe/Kyiv',
    ignoreHTTPSErrors: false,
  })
  if (options.trace) await context.tracing.start({ screenshots: true, snapshots: true })
  const origin = new URL(base).origin
  await context.route('**/*', (route) => {
    const request = route.request()
    const url = request.url()
    if (request.method() !== 'GET' && request.method() !== 'HEAD' && request.method() !== 'OPTIONS'
      && url.startsWith(origin) && (url.includes('/api/') || url.includes('/hubs/'))
      && !isAllowedNonGet(request.method(), url, allowNonGet)) {
      record.blockedRequests.push({ method: request.method(), url: url.replace(origin, '') })
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.on('console', (message) => {
    if (['error', 'warning'].includes(message.type())) record.console.push({ type: message.type(), text: message.text().slice(0, 500) })
  })
  page.on('pageerror', (error) => record.console.push({ type: 'pageerror', text: String(error.message).slice(0, 500) }))
  page.on('response', (response) => {
    const url = response.url()
    if (url.startsWith(origin) && (url.includes('/api/') || url.includes('/qa-desk/'))) {
      record.responses.push({ status: response.status(), method: response.request().method(), url: url.replace(origin, '') })
    }
  })
  let exitCode = EXIT.env
  try {
    await login(context, base, credentials, timeout)
    if (env === 'qa') {
      await context.addCookies([{ name: 'gba_env', value: 'qa', url: base }])
    }
    try {
      await page.goto(base + spec.url, { waitUntil: 'load', timeout: timeout * 3 })
    } catch (error) {
      throw new EnvError(`navigation to ${spec.url} failed: ${error.message.split('\n')[0]}`)
    }
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {})
    if (spec.settleMs) await page.waitForTimeout(spec.settleMs)
    if (env === 'qa') {
      const active = await page.evaluate(async () => {
        const response = await fetch('/gba-env.json', { credentials: 'include' })
        return response.ok ? (await response.json()).active : null
      }).catch(() => null)
      record.activeEnv = active
      if (active !== 'qa') throw new EnvError(`console did not switch to QA (active=${active})`)
    }
    let stepsOk = true
    for (const [index, step] of (spec.steps || []).entries()) {
      if (!(await runStep(page, step, index, evidenceDir, timeout, record))) {
        stepsOk = false
        break
      }
    }
    if (stepsOk) {
      for (const check of spec.checks) await runCheck(page, check, timeout, record)
    }
    const failedChecks = record.checks.filter((check) => !check.pass)
    record.status = stepsOk && failedChecks.length === 0 ? 'pass' : 'fail'
    exitCode = record.status === 'pass' ? EXIT.pass : EXIT.fail
  } catch (error) {
    if (error instanceof SpecError) throw error
    record.status = 'error'
    record.error = error.message
    exitCode = EXIT.env
  } finally {
    record.finalUrl = page.url()
    record.screenshot = path.join(evidenceDir, 'screenshot.png')
    await page.screenshot({ path: record.screenshot, fullPage: spec.fullPage !== false }).catch(() => { record.screenshot = null })
    if (options.trace) {
      record.trace = path.join(evidenceDir, 'trace.zip')
      await context.tracing.stop({ path: record.trace }).catch(() => { record.trace = null })
    }
    record.finishedAt = new Date().toISOString()
    record.exitCode = exitCode
    record.resultFile = path.join(evidenceDir, 'result.json')
    fs.writeFileSync(record.resultFile, JSON.stringify(record, null, 2) + '\n')
    await context.close().catch(() => {})
  }
  return record
}

function summarize(record) {
  const failed = record.checks.filter((check) => !check.pass)
  const lines = [`[${record.status.toUpperCase()}] ${record.spec} (${record.env}) ${record.base}${record.url}`]
  for (const step of record.steps.filter((entry) => !entry.ok)) lines.push(`  step ${step.index} ${step.kind} ${step.target}: ${step.error}`)
  for (const check of record.checks) {
    const detail = check.error || (check.count !== undefined ? `count=${check.count}` : '') || (check.found !== undefined ? `found=${check.found}` : '')
    lines.push(`  ${check.pass ? 'ok  ' : 'FAIL'} ${check.type}${check.description ? ` - ${check.description}` : ''}${detail ? ` (${detail})` : ''}`)
  }
  if (record.error) lines.push(`  error: ${record.error}`)
  if (record.blockedRequests.length) lines.push(`  blocked non-GET requests: ${record.blockedRequests.length}`)
  lines.push(`  evidence: ${record.evidenceDir}`)
  return { text: lines.join('\n'), failed }
}

async function main() {
  let options
  try {
    options = parseArgs(process.argv.slice(2))
  } catch (error) {
    process.stderr.write(`${error.message}\n\n${usage()}\n`)
    return EXIT.spec
  }
  if (options.help) {
    process.stdout.write(`${usage()}\n`)
    return EXIT.pass
  }
  let specs
  try {
    specs = options.specs.map(readSpec)
  } catch (error) {
    process.stderr.write(`${error.message}\n`)
    process.stdout.write(`UI_VERIFY_RESULT ${JSON.stringify({ status: 'error', exitCode: EXIT.spec, error: error.message })}\n`)
    return EXIT.spec
  }
  let credentials
  let browser
  try {
    credentials = loadCredentials(options.credentials || process.env.QA_VERIFIER_CREDENTIALS || DEFAULT_CREDENTIALS)
    browser = await chromium.launch({ headless: !options.headed })
  } catch (error) {
    process.stderr.write(`${error.message}\n`)
    process.stdout.write(`UI_VERIFY_RESULT ${JSON.stringify({ status: 'error', exitCode: EXIT.env, error: error.message })}\n`)
    return EXIT.env
  }
  let exitCode = EXIT.pass
  const results = []
  try {
    for (const spec of specs) {
      let record
      try {
        record = await runSpec(browser, spec, options, credentials)
      } catch (error) {
        if (!(error instanceof SpecError)) throw error
        process.stderr.write(`${spec.name}: ${error.message}\n`)
        results.push({ spec: spec.name, status: 'error', exitCode: EXIT.spec, error: error.message })
        exitCode = Math.max(exitCode, EXIT.spec)
        continue
      }
      const { text, failed } = summarize(record)
      process.stdout.write(`${text}\n`)
      results.push({
        spec: record.spec,
        status: record.status,
        exitCode: record.exitCode,
        resultFile: record.resultFile,
        screenshot: record.screenshot,
        failedChecks: [
          ...record.steps.filter((entry) => !entry.ok).map((entry) => `step ${entry.index} ${entry.kind}: ${entry.error}`),
          ...failed.map((check) => check.description || check.type),
        ],
        error: record.error || null,
      })
      exitCode = Math.max(exitCode, record.exitCode)
    }
  } finally {
    await browser.close().catch(() => {})
  }
  const status = exitCode === EXIT.pass ? 'pass' : exitCode === EXIT.fail ? 'fail' : 'error'
  process.stdout.write(`UI_VERIFY_RESULT ${JSON.stringify({ status, exitCode, results })}\n`)
  return exitCode
}

process.exitCode = await main()
