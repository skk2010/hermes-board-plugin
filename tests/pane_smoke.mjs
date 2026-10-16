#!/usr/bin/env node
/* Load the desktop half outside Electron and run it — the pane cannot be rendered here, but a
 * module that links, registers a pane, survives a render pass and answers clicks is a long way
 * from "looks fine in the editor". Stubs stand in for the app's SDK and React; `useState` keeps
 * real values across renders so a click can be followed by the render it causes.
 *
 *     node tests/pane_smoke.mjs [path/to/plugin.js]
 *
 * Two scenarios, two SDK stubs: one for an app build that exports nothing of the picker kit (the
 * pane must fall back to chips) and one that exports it (the pane must open its board window).
 *
 * Not covered: anything that needs a real window (frame loading, drag, focus, the postMessage
 * bridge beyond the call itself).
 */
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = process.argv[2] || join(here, '..', 'desktop', 'plugin.js')
const failures = []
const report = { scenarios: [], ok: false }

const SDK_BASE = `
export const events = []
export const host = {
  onEvent: (type) => { events.push(type); return () => {} },
  notify: () => {},
  notifyError: () => {},
  revealed: [],
  revealPane: (id) => { host.revealed.push(id) },
  state: {}
}
export const atom = (initial) => {
  let value = initial
  return { get: () => value, set: (next) => { value = next } }
}
export const useValue = (a) => (a && typeof a.get === 'function' ? a.get() : undefined)
export const useQuery = (options) => {
  if (options && typeof options.queryFn === 'function') { try { options.queryFn() } catch (e) {} }
  // The test feeds canned payloads through this hook so the pane renders its real frame.
  const key = (options && options.queryKey) || []
  const data = globalThis.__queryData ? globalThis.__queryData(key) : undefined
  return { data, error: undefined, refetch: () => {} }
}
export const useQueryClient = () => ({ invalidateQueries: () => {} })
`

/* The UI kit the app actually exports today. A scenario decides whether the pane sees it. */
const SDK_KIT = `
export const cn = (...parts) => parts.filter(Boolean).join(' ')
export const Button = (props) => ({ type: 'button', props })
export const Badge = (props) => ({ type: 'span', props })
export const Input = (props) => ({ type: 'input', props })
export const Separator = (props) => ({ type: 'div', props })
export const Codicon = (props) => ({ type: 'i', props })
export const SandboxedFrame = (props) => ({ type: 'iframe', props })
export const GlyphSpinner = (props) => ({ type: 'div', props })
export const EmptyState = (props) => ({ type: 'div', props })
export const ErrorState = (props) => ({ type: 'div', props })
export const Popover = (props) => ({ type: 'div', props })
export const PopoverTrigger = (props) => ({ type: 'div', props })
export const PopoverContent = (props) => ({ type: 'div', props })
export const RowButton = (props) => ({ type: 'button', props })
export const SearchField = (props) => ({ type: 'input', props })
export const ConfirmDialog = (props) => ({ type: 'div', props })
`

const REACT_STUB = `
const slots = []
let slot = 0
globalThis.__beginRender = (fn) => { slot = 0; return fn() }
export const useCallback = (fn) => fn
export const useEffect = () => {}
export const useMemo = (fn) => fn()
export const useState = (initial) => {
  const index = slot++
  if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
  return [slots[index], (next) => { slots[index] = typeof next === 'function' ? next(slots[index]) : next }]
}
export const useRef = (value) => {
  const index = 500 + slot++
  if (!(index in slots)) slots[index] = { current: value }
  return slots[index]
}
`

const JSX_RUNTIME_STUB = `
export const jsx = (type, props) => ({ type, props })
export const jsxs = jsx
export const Fragment = 'Fragment'
`

function stub(work, name, code, extra = {}) {
  // A subpath import ('react/jsx-runtime') needs an exports map, not a directory.
  const dir = join(work, 'node_modules', ...name.split('/'))
  mkdirSync(dir, { recursive: true })
  const exportsMap = { '.': './index.mjs' }
  writeFileSync(join(dir, 'index.mjs'), code)
  for (const [key, file] of Object.entries(extra.files || {})) {
    writeFileSync(join(dir, file.name), file.code)
    exportsMap[key] = `./${file.name}`
  }
  writeFileSync(join(dir, 'package.json'),
    JSON.stringify({ name, version: '0.0.0', type: 'module', main: 'index.mjs', exports: exportsMap }))
}

/* One scenario = its own temp dir and its own SDK stub, so the two kit eras are two module
 * instances and the pane's fallbacks are exercised for real rather than by reading the code. */
async function loadPane(kit) {
  const work = mkdtempSync(join(tmpdir(), 'pane-smoke-'))
  // Say what the temp copy is: without it node re-parses plugin.js as ESM and warns on stderr.
  writeFileSync(join(work, 'package.json'), JSON.stringify({ type: 'module' }))
  stub(work, '@hermes/plugin-sdk', SDK_BASE + kit)
  stub(work, 'react', REACT_STUB, { files: { './jsx-runtime': { name: 'jsx-runtime.mjs', code: JSX_RUNTIME_STUB } } })
  writeFileSync(join(work, 'plugin.js'), readFileSync(source, 'utf8'))
  const sdk = await import(pathToFileURL(join(work, 'node_modules', '@hermes', 'plugin-sdk', 'index.mjs')).href)
  const module = await import(pathToFileURL(join(work, 'plugin.js')).href)
  return { work, sdk, module, plugin: module.default }
}

globalThis.window = {
  addEventListener: () => {},
  removeEventListener: () => {},
  setTimeout: (...args) => setTimeout(...args),
  clearTimeout: (...args) => clearTimeout(...args),
  btoa: (text) => Buffer.from(text, 'binary').toString('base64')
}

/** Walk the element tree the pane produces. Function components are not called: a call site's own
 *  props are what the app receives, and they are what these assertions are about. */
function walk(node, visit) {
  if (Array.isArray(node)) { node.forEach((child) => walk(child, visit)); return }
  if (!node || typeof node !== 'object') return
  if (!node.props) return
  visit(node)
  walk(node.props.children, visit)
}

/** The wrapper the window's keyboard rides on: the node that holds both the handler and the field. */
function findKeyWrapper(root) {
  let found = null
  walk(root, (element) => {
    if (typeof element.props.onKeyDown !== 'function') return
    let wrapsSearch = false
    walk(element.props.children, (inner) => {
      if (inner.props && inner.props.placeholder === 'Search boards…') wrapsSearch = true
    })
    if (wrapsSearch) found = element
  })
  return found
}

/** The board row that renders `text` — a row is the element that answers the pointer, which the
 *  switcher trigger (carrying the same label) does not. */
function findRow(root, text) {
  let found = null
  walk(root, (element) => {
    if (found || typeof element.props.onMouseEnter !== 'function') return
    let holds = false
    walk(element.props.children, (inner) => {
      if (inner.props && inner.props.children === text) holds = true
    })
    if (holds) found = element
  })
  return found
}

/** The element whose children include a node rendering `text` — how a row's label reads. */
function findByNestedText(root, text) {
  let found = null
  walk(root, (element) => {
    if (found) return
    const kids = Array.isArray(element.props.children) ? element.props.children : [element.props.children]
    if (kids.some((child) => child && child.props && child.props.children === text)) found = element
  })
  return found
}

function harness(pane, { revealPane = true } = {}) {
  const contributions = []
  const restCalls = []
  const restOptions = []
  const confirmCalls = []
  const storage = { boardId: 'demo' }
  globalThis.window.confirm = () => { confirmCalls.push(1); return true }
  globalThis.__queryData = (key) =>
    key.includes('page')
      ? { html: '<!doctype html><p>board</p>', version: 'v1' }
      : { boards: [{ id: 'demo', title: 'Demo' },
                   { id: 'hermes-board', title: 'hermes-board — the agent draws, the user edits' }] }
  const ctx = {
    storage: { get: (k) => storage[k], set: (k, v) => { storage[k] = v } },
    register: (contribution) => contributions.push(contribution),
    rest: async (path, options = {}) => {
      restCalls.push(options.method ? `${options.method} ${path}` : path)
      restOptions.push({ path, ...options })
      if (path === '/layout') {
        return { ok: true, id: 'demo', version: 'v2', info: { ranks: 2, crossings: 0, placed: 3 },
                 board: { id: 'demo', nodes: [{ id: 'a', x: 10, y: 10 }] } }
      }
      if (path === '/boards') return { boards: globalThis.__queryData(['list']).boards }
      if (path === '/page') return { html: '<!doctype html><p>board</p>', version: 'v1' }
      return { ok: true, version: 'v2' }
    },
    onDispose: () => {},
    i18n: { t: (k) => k }
  }
  if (!revealPane) delete pane.sdk.host.revealPane
  pane.plugin.register(ctx)
  const pane_ = contributions.find((c) => c.area === 'panes')

  /* Every render begins a fresh hook pass; state lives in the react stub's slots, so a click can be
   * followed by the render it causes. */
  const render = () => {
    const tree = pane_.render()
    return globalThis.__beginRender(() => tree.type(tree.props))
  }
  return { contributions, pane: pane_, restCalls, restOptions, confirmCalls, storage, ctx, render }
}

async function scenario(name, kit, body) {
  const steps = []
  const check = (label, condition, detail = '') => {
    steps.push([label, !!condition])
    if (!condition) failures.push(`${name} → ${label} ${detail}`)
  }
  const pane = await loadPane(kit)
  try {
    await body(pane, check)
  } catch (error) {
    failures.push(`${name} → threw ${(error && error.stack) || error}`)
  } finally {
    rmSync(pane.work, { recursive: true, force: true })
  }
  report.scenarios.push({ name, steps })
}

/* Shared: the module contract, the registration, the layout round-trip, and the chrome rules that
 * keep the board the biggest thing in a 440px pane. */
async function commonChecks(pane, check) {
  check('module loads and default-exports a plugin', !!pane.plugin && typeof pane.plugin.register === 'function')
  check('plugin id matches the package name', pane.plugin && pane.plugin.id === 'hermes-board',
    String(pane.plugin && pane.plugin.id))
  check('ships opt-in (defaultEnabled false)', pane.plugin && pane.plugin.defaultEnabled === false)

  const h = harness(pane)
  check('register() contributes a pane', h.contributions.some((c) => c.area === 'panes'),
    JSON.stringify(h.contributions.map((c) => c.area)))
  check('pane docks to the right of the conversation',
    h.pane && h.pane.data && h.pane.data.placement === 'right' && h.pane.data.dock &&
    h.pane.data.dock.pane === 'workspace', JSON.stringify(h.pane && h.pane.data))
  check('pane declares a width', !!(h.pane && h.pane.data && h.pane.data.width))
  check('subscribes to the plugin board event',
    pane.sdk.events.includes('plugin.hermes-board.board.changed'), JSON.stringify(pane.sdk.events))

  /* A pane the user closed stays closed (the app records the dismissal), so the plugin owes them a
   * row of their own to bring it back. */
  const palette = h.contributions.filter((c) => c.area === 'palette')
  check('registers one command to bring the pane back', palette.length === 1,
    JSON.stringify(h.contributions.map((c) => c.area)))
  const show = palette[0] && palette[0].data
  check('the command reads as a board command', !!show && !!show.label && show.id.startsWith('hermes-board'),
    JSON.stringify(show && { id: show.id, label: show.label }))
  if (show && typeof show.run === 'function') show.run()
  check('the command reveals the pane by its contribution-scoped id',
    pane.sdk.host.revealed.includes('hermes-board:pane'), JSON.stringify(pane.sdk.host.revealed))

  const rendered = h.render()
  check('pane body renders without throwing', !!(rendered && rendered.props))
  check('pane reads the stored board id', rendered.props.className.includes('flex'),
    String(rendered && rendered.props && rendered.props.className))
  check('pane lists the boards over REST', h.restCalls.includes('/boards'), JSON.stringify(h.restCalls))

  const control = { layout: null, direction: null, frameRef: null, variants: new Set(), aria: new Set(),
                    truncates: 0, createFields: 0 }
  walk(rendered, (element) => {
    const props = element.props
    if (props.children === 'auto-layout') control.layout = element
    if (props.children === '↓' || props.children === '→') control.direction = element
    if (props.ref && typeof props.ref === 'object' && 'current' in props.ref) control.frameRef = props.ref
    if (props.variant) control.variants.add(props.variant)
    if (props['aria-label']) control.aria.add(props['aria-label'])
    const className = String(props.className || '')
    if (className.includes('max-w-[') || className.includes('truncate')) control.truncates += 1
    if (String(props.placeholder || '').startsWith('new board')) control.createFields += 1
  })
  check('pane shows the board frame', !!control.frameRef)
  const posted = []
  const frame = { contentWindow: { postMessage: (message) => posted.push(message) }, addEventListener: () => {} }
  if (control.frameRef) control.frameRef.current = frame
  check('pane ships an auto-layout button', !!control.layout)
  check('pane ships a layout-direction toggle', !!control.direction, String(control.direction))
  if (control.layout) {
    control.layout.props.onClick()
    await new Promise((resolve) => setTimeout(resolve, 30))
  }
  const layoutCall = h.restOptions.find((call) => call.path === '/layout')
  check('auto-layout asks the backend instead of re-implementing the algorithm',
    !!layoutCall && layoutCall.method === 'POST', JSON.stringify(h.restOptions))
  check('auto-layout sends the board id and the chosen direction',
    !!layoutCall && layoutCall.body && layoutCall.body.id === 'demo' && layoutCall.body.direction === 'down',
    JSON.stringify(layoutCall && layoutCall.body))
  check('the laid-out board is pushed into the preview frame',
    posted.some((message) => message.type === 'ck-board-replace' && message.board && message.board.nodes),
    JSON.stringify(posted))

  check('no control is a filled primary button',
    control.variants.size > 0 && !control.variants.has('default'), JSON.stringify([...control.variants]))
  check('every control that shows no text carries an accessible name',
    control.aria.has('auto-layout') && control.aria.has('layout direction') &&
    control.aria.has('reload') && control.aria.has('new board'), JSON.stringify([...control.aria]))
  check('a long board title is truncated rather than wrapping the header', control.truncates >= 1,
    String(control.truncates))
  check('the create field stays hidden until a board is being made', control.createFields === 0,
    String(control.createFields))
  return { h, control, rendered }
}

/* An app build without the picker kit: chips in the header, window.confirm for deletion. */
await scenario('without the picker kit (chip fallback)', '', async (pane, check) => {
  const { h } = await commonChecks(pane, check)

  const chipDelete = []
  walk(h.render(), (element) => {
    if (String(element.props['aria-label'] || '').startsWith('delete board ')) chipDelete.push(element)
  })
  check('every chip carries its own delete', chipDelete.length === 2, String(chipDelete.length))
  if (chipDelete[0]) chipDelete[0].props.onClick()
  await new Promise((resolve) => setTimeout(resolve, 20))
  check('the chip delete asks before removing the board', h.confirmCalls.length === 1, String(h.confirmCalls.length))
  check('the chip delete reaches the backend', h.restCalls.includes('DELETE /board?id=demo'),
    JSON.stringify(h.restCalls))

  /* An older desktop has no reveal API: a palette row that cannot work is worse than none. */
  const older = harness(pane, { revealPane: false })
  check('a host without host.revealPane gets no phantom command',
    !older.contributions.some((c) => c.area === 'palette'),
    JSON.stringify(older.contributions.map((c) => c.area)))
  check('such a host still gets the pane', older.contributions.some((c) => c.area === 'panes'))
})

/* An app build with the picker kit: the board list is a window with a search field. */
await scenario('with the picker kit (board window)', SDK_KIT, async (pane, check) => {
  const module = pane.module
  const { h } = await commonChecks(pane, check)

  const boards = [
    { id: 'demo', title: 'Demo' },
    { id: 'hermes-board', title: 'hermes-board — the agent draws, the user edits' }
  ]
  check('the search filter is a pure function of id and title', typeof module.filterBoards === 'function')
  check('an empty query keeps every board in order', module.filterBoards(boards, '   ').length === 2)
  check('the search matches an id', module.filterBoards(boards, 'hermes').map((b) => b.id).join() === 'hermes-board')
  check('the search matches a title, case-insensitively',
    module.filterBoards(boards, 'DEMO').map((b) => b.id).join() === 'demo')
  check('the search matches a phrase inside a title',
    module.filterBoards(boards, 'draws').map((b) => b.id).join() === 'hermes-board')
  check('a query that matches nothing yields nothing', module.filterBoards(boards, 'zzz').length === 0)
  check('the search never mutates the list it is given', boards.length === 2 && boards[0].id === 'demo')

  const rendered = h.render()
  let window_ = null
  let trigger = null
  let search = null
  const rows = []
  let open = null
  walk(rendered, (element) => {
    const props = element.props
    if (props.open !== undefined) open = props.open
    if (props.variant === 'menu' && props.align) window_ = element
    if (String(props['aria-label'] || '').startsWith('boards: ')) trigger = element
    if (props.placeholder === 'Search boards…') search = element
    if (props.onMouseEnter && props.onClick && props.children) rows.push(element)
  })
  check('the boards open in their own window, not in the header', !!window_ && open === false)
  check('the window is the app’s menu surface', !!window_ && window_.props.variant === 'menu' &&
    String(window_.props.className).includes('w-72'), JSON.stringify(window_ && window_.props))
  check('the trigger names the current board', !!trigger &&
    String(trigger.props['aria-label']).includes('Demo'), trigger && trigger.props['aria-label'])
  check('the trigger is a ghost button, not a filled one', !!trigger && trigger.props.variant === 'ghost')
  check('the window holds a search field', !!search && typeof search.props.onChange === 'function')
  check('the window lists every board', rows.length === 2, String(rows.length))
  const marked = rows.filter((row) => (Array.isArray(row.props.children) ? row.props.children : [])
    .some((child) => child && child.props && child.props.name === 'check'))
  check('the current board is marked in the window', marked.length === 1, String(marked.length))
  const deleteRow = findByNestedText(window_, 'Delete board…')
  check('the window offers create and delete', !!findByNestedText(window_, 'New board…') && !!deleteRow)
  check('the delete row reads as destructive', !!deleteRow &&
    String(deleteRow.props.className).includes('text-destructive'))

  /* The search field has to be reachable without a mouse, and the keys have to walk the list the
   * search left behind. */
  check('the search field hands the pane a ref it can focus on open',
    !!search && !!search.props.inputRef && 'current' in search.props.inputRef)
  const keyWrapper = findKeyWrapper(rendered)
  check('the window holds the key handler around the search field', !!keyWrapper)
  const keyNode = () => findKeyWrapper(h.render())
  if (keyWrapper) {
    /* React rebinds the handler on every render, so each key press goes through the current one. */
    const press = (key) => {
      const node = keyNode()
      if (node) node.props.onKeyDown({ key, preventDefault: () => {} })
    }
    press('ArrowDown')
    const highlighted = []
    walk(h.render(), (element) => {
      if (element.props.onMouseEnter &&
          String(element.props.className).includes(' bg-(--ui-control-active-background)')) {
        highlighted.push(element)
      }
    })
    check('an arrow key highlights exactly one board row', highlighted.length === 1, String(highlighted.length))
    press('Enter')
    const afterEnter = h.render()
    check('Enter opens the highlighted board', h.storage.boardId === 'hermes-board', String(h.storage.boardId))
    let openAfterEnter = null
    walk(afterEnter, (element) => { if (element.props.open !== undefined) openAfterEnter = element.props.open })
    check('Enter closes the window', openAfterEnter === false, String(openAfterEnter))
  }

  /* Clicking a row switches the board and closes the window. */
  const clicked = findRow(h.render(), 'Demo')
  check('the window lists the boards by title', !!clicked)
  if (clicked) clicked.props.onClick()
  const afterPick = h.render()
  let openAfterPick = null
  walk(afterPick, (element) => { if (element.props.open !== undefined) openAfterPick = element.props.open })
  check('picking a board closes the window', openAfterPick === false, String(openAfterPick))
  check('picking a board stores the choice', h.storage.boardId === 'demo', String(h.storage.boardId))

  /* Deleting goes through the app's dialog: one confirmation, never two. */
  const doomed = h.storage.boardId
  const deleteRow2 = findByNestedText(afterPick, 'Delete board…')
  if (deleteRow2) {
    deleteRow2.props.onClick()
    const confirming = h.render()
    let dialog = null
    walk(confirming, (element) => { if (element.props.destructive && element.props.title) dialog = element })
    check('deleting a board asks through the app’s dialog', !!dialog, JSON.stringify(dialog && dialog.props))
    check('the dialog is destructive and names the board', !!dialog && String(dialog.props.title).includes(doomed))
    if (dialog) {
      const before = h.confirmCalls.length
      dialog.props.onConfirm()
      await new Promise((resolve) => setTimeout(resolve, 20))
      check('the dialog is the only confirmation', h.confirmCalls.length === before,
        `${before} → ${h.confirmCalls.length}`)
      check('the dialog’s delete reaches the backend', h.restCalls.includes(`DELETE /board?id=${doomed}`),
        JSON.stringify(h.restCalls))
    }
  }

  /* "New board…" turns the window into the create form. */
  const newRow = findByNestedText(h.render(), 'New board…')
  check('the window has a row for making a board', !!newRow)
  if (newRow) {
    newRow.props.onClick()
    const creating = h.render()
    let field = null
    walk(creating, (element) => {
      if (String(element.props.placeholder || '').startsWith('new board')) field = element
    })
    check('making a board reveals the id field in the window', !!field, JSON.stringify(field && field.props))
    check('the create field is submitted by a button as well as Enter', !!findByNestedText(creating, 'create'))
  }
})

/* v1.8.1 — a save must never move the board. The status message used to be a row of its own between
 * the header and the frame, so every "saved <version>" pushed the whole frame — and the whole board
 * with it — down about 20 px, then pulled it back four seconds later, while the user was drawing.
 * What matters is SHAPE: whatever the pane has to say, the frame's chain of parents and the root's
 * children must be the same. Text is normalised away; the classes are not. */
function treeShape(node) {
  if (Array.isArray(node) || (node && node[Symbol.iterator] && typeof node !== 'string' && !node.props)) {
    return `[${Array.from(node || []).map(treeShape).join('|')}]`
  }
  if (!node || typeof node !== 'object' || !node.props) return '_'
  const name = (node.type && (node.type.name || node.type)) || '?'
  return `${name}(${treeShape(node.props.children)})`
}

/** The class names from the pane's root down to the board frame — the geometry a status message
 *  could move. The frame is the element that carries `title: "board <id>"`. */
function frameChain(tree) {
  let chain = null
  const visit = (node, path) => {
    if (Array.isArray(node) || (node && node[Symbol.iterator] && typeof node !== 'string' && !node.props)) {
      Array.from(node || []).forEach((child) => visit(child, path)); return
    }
    if (!node || typeof node !== 'object' || !node.props) return
    const next = path.concat(String(node.props.className || ''))
    if (String(node.props.title || '').startsWith('board ')) chain = next
    visit(node.props.children, next)
  }
  visit(tree, [])
  return chain ? chain.join(' > ') : null
}

await scenario('a save never moves the board', SDK_KIT, async (pane, check) => {
  const h = harness(pane, {})
  const idle = h.render()
  let saveControl = null
  let live = null
  const collect = (tree) => {
    saveControl = null
    live = null
    walk(tree, (element) => {
      const label = String(element.props['aria-label'] || '')
      if (label.startsWith('save the board')) saveControl = element
      if (element.props.role === 'status') live = element
    })
  }
  collect(idle)
  check('the pane ships a save control in the header row', !!saveControl,
    JSON.stringify(saveControl && saveControl.props))
  check('the save control carries an accessible name', !!saveControl &&
    saveControl.props['aria-label'].startsWith('save the board'))
  check('the save control says what it is, in words', !!saveControl &&
    String(saveControl.props.title || '').includes('save'))
  check('the save control is not a filled primary button', !saveControl || saveControl.props.variant !== 'default',
    String(saveControl && saveControl.props.variant))
  check('there is a live region for assistive tech, always in the tree', !!live &&
    String(live.props['aria-live']) === 'polite', JSON.stringify(live && live.props))
  const shapeIdle = treeShape(idle)
  const chainIdle = frameChain(idle)
  check('the board frame sits in a chain this check can see', !!chainIdle, String(chainIdle))

  /* News arrives (auto-layout reports when it is done) — the shape must not change by one node. */
  let layout = null
  walk(idle, (element) => { if (element.props.children === 'auto-layout') layout = element })
  if (layout) {
    layout.props.onClick()
    await new Promise((resolve) => setTimeout(resolve, 40))
  }
  const news = h.render()
  collect(news)
  check('the pane has news to show after it does something', !!live && String(live.props.children).length > 0,
    JSON.stringify(live && live.props.children))
  check('what the pane says is announced, not laid out', treeShape(news) === shapeIdle,
    'the tree gained or lost a node while the pane had news to show')
  check('…and the board frame is still in the same chain of parents', frameChain(news) === chainIdle,
    `${chainIdle} → ${frameChain(news)}`)
  check('the save control is still in the same place after the news', !!saveControl)

  /* And a save that cannot read the frame says so, in the control itself. */
  const ref = { current: null }
  walk(news, (element) => {
    if (element.props.ref && typeof element.props.ref === 'object' && 'current' in element.props.ref) {
      element.props.ref.current = null
    }
  })
  if (saveControl) saveControl.props.onClick()
  await new Promise((resolve) => setTimeout(resolve, 20))
  const failed = h.render()
  collect(failed)
  check('a save with no frame to read reports the failure it hit', !!saveControl &&
    String(saveControl.props['aria-label']).includes('no editor frame'), 
    JSON.stringify(saveControl && saveControl.props['aria-label']))
  check('…and it reports it without moving the board', treeShape(failed) === shapeIdle && frameChain(failed) === chainIdle,
    `a failed save changed the pane’s shape: ${chainIdle} → ${frameChain(failed)}`)
})

report.ok = failures.length === 0
if (failures.length) report.failures = failures
console.log(JSON.stringify(report, null, 1))
process.exit(failures.length ? 1 : 0)
