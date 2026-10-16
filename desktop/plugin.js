/* hermes-board — the desktop pane: the board lives beside the conversation.
 *
 * The pane is a thin shell around the same in-page editor the skill ships (`boardkit`'s
 * `board_editor.js`), loaded from the plugin backend as HTML in a sandboxed frame:
 *
 *   pane  -> GET  /api/plugins/hermes-board/page?id=…   -> editor HTML (data: URL in a frame)
 *   frame -> window.parent.postMessage({type:'ck-board-changed', board})  (the bridge)
 *   pane  -> POST /api/plugins/hermes-board/board        -> saves, broadcasts board.changed
 *   agent tools -> save + broadcast                      -> pane pushes the new board into the
 *                                                          frame (no reload, no lost drag)
 *
 * Nothing here renders charts: the frame is self-contained, so this file only has to keep the
 * frame alive, debounce writes and stay honest about state (version + errors in the header).
 */
import * as sdk from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const host = sdk.host
const atom = sdk.atom
const useValue = sdk.useValue
const useQuery = sdk.useQuery
const useQueryClient = sdk.useQueryClient
/* The UI kit is versioned with the app: fall back to plain elements rather than failing to
 * load on a build that does not export one of these names yet. */
const Button = sdk.Button || 'button'
const Badge = sdk.Badge || 'span'
const GlyphSpinner = sdk.GlyphSpinner
const EmptyState = sdk.EmptyState
const ErrorState = sdk.ErrorState
const SandboxedFrame = sdk.SandboxedFrame
const Input = sdk.Input || 'input'
const cn = sdk.cn || ((...parts) => parts.filter(Boolean).join(' '))
const Popover = sdk.Popover
const PopoverContent = sdk.PopoverContent
const PopoverTrigger = sdk.PopoverTrigger
const RowButton = sdk.RowButton
const SearchField = sdk.SearchField
const Separator = sdk.Separator
const Codicon = sdk.Codicon
const ConfirmDialog = sdk.ConfirmDialog
/* The board window (a popover with a search field) is the app's picker surface; a build that does
 * not export those names falls back to the chip strip rather than failing to load. */
const PICKER = Boolean(Popover && PopoverContent && PopoverTrigger && RowButton && SearchField)

/** The board window's search: id and title, case-insensitive, and an empty query keeps the order. */
export function filterBoards(boards, query) {
  const needle = query.trim().toLowerCase()
  if (!needle) return boards
  return boards.filter((board) => `${board.id} ${board.title || ''}`.toLowerCase().includes(needle))
}

const PLUGIN_ID = 'hermes-board'
const EVENT = `plugin.${PLUGIN_ID}.board.changed`

function base64Utf8(text) {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return window.btoa(binary)
}

const toDataUrl = (html) => `data:text/html;base64,${base64Utf8(html)}`

function BoardPane({ ctx, shared }) {
  const boardId = useValue(shared.$boardId)
  const queryClient = useQueryClient ? useQueryClient() : null
  const [status, setStatus] = useState('')
  const [saveState, setSaveState] = useState('idle')
  const [newId, setNewId] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [layoutDir, setLayoutDir] = useState('down')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [confirmFor, setConfirmFor] = useState(null)
  const saveTimer = useRef(null)
  const saveNow = useRef(false)      // an explicit save: the next frame message is written at once
  const pickerInput = useRef(null)

  /* Radix moves focus into the popover, not into the field inside it: focus the search input by
   * hand so the user can type the moment the board window opens. */
  useEffect(() => {
    if (!pickerOpen) return undefined
    if (pickerInput.current && pickerInput.current.focus) pickerInput.current.focus()
    setActive(0)
    return undefined
  }, [pickerOpen])

  /* The pane is chrome around the board: every row of controls it keeps costs the canvas, so a
   * status message is shown while it is news and then gets out of the way — in the SAVE control and
   * in a live region for assistive tech, never as a row of its own. */
  useEffect(() => {
    if (!status && saveState === 'idle') return undefined
    const clear = window.setTimeout(() => {
      setStatus('')
      setSaveState('idle')
    }, 4000)
    return () => window.clearTimeout(clear)
  }, [status, saveState])

  const list = useQuery({
    queryKey: ['hermes-board', 'list'],
    queryFn: () => ctx.rest('/boards'),
    refetchInterval: 15000
  })
  const page = useQuery({
    queryKey: ['hermes-board', 'page', boardId],
    queryFn: () => ctx.rest(`/page?id=${encodeURIComponent(boardId)}`),
    enabled: !!boardId
  })

  useEffect(() => {
    if (page.data && page.data.version) shared.versionRef.current = page.data.version
  }, [page.data, shared.versionRef])

  const pushBoard = useCallback((board) => {
    const frame = shared.frameRef.current
    const win = frame && frame.contentWindow
    if (win) {
      win.postMessage({ type: 'ck-board-replace', board }, '*')
      return true
    }
    return false
  }, [shared.frameRef])

  const save = useCallback((board) => {
    setSaveState('saving')
    ctx
      .rest('/board', {
        method: 'POST',
        body: { id: boardId, board, based_on: shared.versionRef.current, origin: 'pane' }
      })
      .then((res) => {
        if (res && res.error === 'stale') {
          shared.versionRef.current = res.version
          pushBoard(res.board)
          setSaveState('merged')
          setStatus('the agent changed this board — merged its version')
        } else if (res && res.version) {
          shared.versionRef.current = res.version
          setSaveState('saved')
          setStatus(`saved ${res.version}`)
          if (queryClient) queryClient.invalidateQueries({ queryKey: ['hermes-board', 'list'] })
        } else {
          setSaveState('error')
          setStatus('save refused by the backend')
        }
      })
      .catch((err) => {
        setSaveState('error')
        setStatus(`save failed: ${err && err.message ? err.message : err}`)
      })
  }, [boardId, ctx, pushBoard, queryClient, shared.versionRef])

  /* The frame bridges every edit; writing on each frame would hammer the backend, so the pane
   * coalesces them and only reports the settled state. The SAVE control asks the frame for its
   * state and then writes at once — an explicit save must not wait out the debounce. */
  useEffect(() => {
    const onMessage = (evt) => {
      const frame = shared.frameRef.current
      if (!frame || evt.source !== frame.contentWindow) return
      const data = evt.data || {}
      if (data.type !== 'ck-board-changed' || !data.board) return
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      if (saveNow.current) {
        saveNow.current = false
        save(data.board)
        return
      }
      saveTimer.current = window.setTimeout(() => save(data.board), 600)
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
    }
  }, [save, shared.frameRef])

  /* Ask the frame for the board it is holding (the bridge answers with `ck-board-changed`). */
  const requestSave = useCallback(() => {
    const frame = shared.frameRef.current
    const win = frame && frame.contentWindow
    if (!win) {
      setSaveState('error')
      setStatus('no editor frame to read')
      return
    }
    saveNow.current = true
    win.postMessage({ type: 'ck-board-request' }, '*')
  }, [shared.frameRef])

  const choose = (id) => {
    shared.$boardId.set(id)
    ctx.storage.set('boardId', id)
  }

  const refreshList = () => {
    if (queryClient) queryClient.invalidateQueries({ queryKey: ['hermes-board', 'list'] })
  }

  /* Board lifecycle from the pane: creating and deleting should not need the agent. */
  const createBoard = () => {
    const id = newId.trim() || `board-${boards.length + 1}`
    ctx
      .rest('/boards', { method: 'POST', body: { id } })
      .then((res) => {
        if (res && res.ok) {
          setNewId('')
          choose(res.id)
          refreshList()
          setStatus(`created ${res.id}`)
        } else {
          setStatus(`create failed: ${(res && res.error) || 'unknown error'}`)
        }
      })
      .catch((err) => setStatus(`create failed: ${err && err.message ? err.message : err}`))
  }

  /* Auto-layout runs on the backend (the same Sugiyama-lite the agent's `graph` op uses) and the
   * result is pushed into the frame — the pane does not try to re-implement the algorithm. */
  const runLayout = () => {
    setStatus('laying out…')
    ctx
      .rest('/layout', { method: 'POST', body: { id: boardId, direction: layoutDir, bands: true } })
      .then((res) => {
        if (!res || !res.ok) {
          setStatus(`layout failed: ${(res && res.error) || 'unknown error'}`)
          return
        }
        shared.versionRef.current = res.version
        pushBoard(res.board)
        setStatus(`auto-layout: ${res.info.ranks} rank(s), ${res.info.crossings} crossing(s)`)
      })
      .catch((err) => setStatus(`layout failed: ${err && err.message ? err.message : err}`))
  }

  const pick = (id) => {
    choose(id)
    setPickerOpen(false)
    setQuery('')
  }

  /** Arrow keys and Enter walk the *filtered* list, so the keyboard and the search agree. */
  const onPickerKeys = (evt) => {
    const rows = filterBoards(boards, query)
    if (evt.key === 'Escape') {
      setPickerOpen(false)
      return
    }
    if (evt.key === 'ArrowDown' || evt.key === 'ArrowUp') {
      evt.preventDefault()
      const step = evt.key === 'ArrowDown' ? 1 : -1
      setActive((index) => Math.max(0, Math.min(index + step, rows.length - 1)))
      return
    }
    if (evt.key === 'Enter' && rows[active]) {
      evt.preventDefault()
      pick(rows[active].id)
    }
  }

  /* `ask` is off when a ConfirmDialog already asked — one confirmation per delete. */
  const deleteBoard = (id, { ask = true } = {}) => {
    if (ask) {
      const ok = typeof window.confirm === 'function' ? window.confirm(`Delete board "${id}"?`) : true
      if (!ok) return
    }
    ctx
      .rest(`/board?id=${encodeURIComponent(id)}`, { method: 'DELETE' })
      .then((res) => {
        if (!res || !res.ok) {
          setStatus(`delete failed: ${(res && res.error) || 'unknown error'}`)
          return
        }
        const rest = boards.filter((board) => board.id !== id)
        if (id === boardId) choose(rest.length ? rest[0].id : '')
        refreshList()
        setStatus(`deleted ${id}`)
      })
      .catch((err) => setStatus(`delete failed: ${err && err.message ? err.message : err}`))
  }

  const boards = (list.data && list.data.boards) || []
  const version = (page.data && page.data.version) || ''
  const error = (list.error && String(list.error.message || list.error)) ||
    (page.error && String(page.error.message || page.error)) || ''

  const current = boards.find((board) => board.id === boardId) || null
  const label = current ? current.title || current.id : 'no board'
  const rows = filterBoards(boards, query)
  const rowClass = (isActive, extra) =>
    cn(
      'relative flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs outline-hidden select-none',
      'hover:bg-(--ui-control-active-background) hover:text-foreground',
      isActive && 'bg-(--ui-control-active-background)',
      extra
    )
  const check = Codicon
    ? jsx(Codicon, { name: 'check', size: '0.8rem', className: 'shrink-0' })
    : jsx('span', { className: 'shrink-0 text-(--ui-text-tertiary)', children: '✓' })
  const plusIcon = Codicon
    ? jsx(Codicon, { name: 'add', size: '0.8rem', className: 'shrink-0 text-(--ui-text-tertiary)' })
    : null
  const trashIcon = Codicon
    ? jsx(Codicon, { name: 'trash', size: '0.8rem', className: 'shrink-0 text-destructive' })
    : null

  /* The boards live in their own window: a pane this narrow cannot show a list worth scanning as
   * a scrolling strip, and a search field is how the app's own pickers work. */
  const boardWindow = jsxs(Popover, {
    open: pickerOpen,
    onOpenChange: (next) => {
      setPickerOpen(next)
      if (!next) setQuery('')
    },
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsxs(Button, {
          variant: 'ghost', size: 'sm',
          className: 'min-w-0 max-w-full gap-1.5 px-2',
          'aria-label': `boards: ${label}`,
          title: 'boards — switch, create, delete',
          children: [
            Codicon
              ? jsx(Codicon, { name: 'project', size: '0.8125rem', className: 'shrink-0 text-(--ui-text-tertiary)' })
              : null,
            jsx('span', { className: 'shrink-0 text-[0.6875rem] font-medium text-(--ui-text-tertiary)', children: 'Board' }),
            jsx('span', { className: 'min-w-0 flex-1 truncate text-xs font-medium leading-none', children: label }),
            Codicon
              ? jsx(Codicon, { name: 'chevron-down', size: '0.8125rem', className: 'shrink-0 text-(--ui-text-tertiary)' })
              : null
          ]
        })
      }),
      jsx(PopoverContent, {
        variant: 'menu', align: 'start', sideOffset: 4, className: 'w-72 p-1',
        children: jsxs('div', {
          className: 'flex flex-col',
          onKeyDown: onPickerKeys,
          children: [
            jsx('div', {
              className: 'px-1 pb-1',
              children: jsx(SearchField, {
                placeholder: 'Search boards…',
                value: query,
                'aria-label': 'search boards',
                containerClassName: 'w-full',
                inputRef: pickerInput,
                onChange: (value) => {
                  setQuery(value)
                  setActive(0)
                }
              })
            }),
            jsx(Separator, {}),
            jsxs('div', {
              className: 'max-h-64 min-h-0 overflow-y-auto py-1',
              children: rows.length
                ? rows.map((board, index) =>
                    jsxs(RowButton, {
                      key: board.id,
                      className: rowClass(index === active),
                      onMouseEnter: () => setActive(index),
                      onClick: () => pick(board.id),
                      children: [
                        jsx('span', { className: 'min-w-0 flex-1 truncate', children: board.title || board.id }),
                        jsx('span', {
                          className: 'shrink-0 font-mono text-[0.625rem] text-(--ui-text-quaternary)',
                          children: board.id
                        }),
                        board.id === boardId ? check : null
                      ]
                    })
                  )
                : jsx('div', {
                    className: 'px-2 py-3 text-center text-(--ui-text-tertiary)',
                    children: boards.length ? `no board matches “${query}”` : 'no boards yet'
                  })
            }),
            jsx(Separator, {}),
            ...(showCreate
              ? [
                  jsxs('div', {
                    className: 'flex items-center gap-1 p-1',
                    children: [
                      jsx(Input, {
                        className: 'min-w-0 flex-1',
                        placeholder: 'new board id (letters, digits, dash)',
                        value: newId,
                        autoFocus: true,
                        onChange: (evt) => setNewId(evt.target.value),
                        onKeyDown: (evt) => {
                          if (evt.key === 'Enter') { createBoard(); setShowCreate(false) }
                          if (evt.key === 'Escape') setShowCreate(false)
                        }
                      }),
                      jsx(Button, {
                        variant: 'outline', size: 'xs',
                        onClick: () => { createBoard(); setShowCreate(false) },
                        children: 'create'
                      })
                    ]
                  })
                ]
              : [
                  jsxs(RowButton, {
                    className: rowClass(false),
                    onClick: () => setShowCreate(true),
                    children: [plusIcon, jsx('span', { className: 'flex-1', children: 'New board…' })]
                  }),
                  boardId
                    ? jsxs(RowButton, {
                        className: rowClass(false, 'text-destructive'),
                        onClick: () => {
                          setPickerOpen(false)
                          setConfirmFor(boardId)
                        },
                        children: [trashIcon, jsx('span', { className: 'flex-1', children: 'Delete board…' })]
                      })
                    : null
                ])
          ]
        })
      })
    ]
  })

  /* Save: ONE permanent control in the header row, so nothing about saving can move the board. The
   * state rides in the glyph, the short label, the title and a live region — never in a message that
   * appears and disappears: that is what the old status row did, and it pushed the whole frame (and
   * with it the whole board) about 20 px down on every save, then pulled it back four seconds later,
   * while the user was drawing. The label may grow into the switcher's flex space (the switcher is
   * flex-1 and truncates), so the controls to its right never move either. */
  const SAVE_FACE = {
    idle: { glyph: 'save', label: 'save', tint: 'text-(--ui-text-tertiary)' },
    saving: { glyph: 'sync', label: 'saving…', tint: 'text-(--ui-text-tertiary)' },
    saved: { glyph: 'check', label: 'saved', tint: 'text-(--ui-text-secondary)' },
    merged: { glyph: 'arrow-swap', label: 'merged', tint: 'text-(--ui-text-secondary)' },
    error: { glyph: 'error', label: 'failed', tint: 'text-destructive' }
  }
  const face = SAVE_FACE[saveState] || SAVE_FACE.idle

  /* One row of chrome, and every control a quiet variant — a row of filled primary buttons reads
   * as a wall and hides which action matters. */
  const header = jsxs('div', {
    className: 'flex items-center gap-1 min-w-0',
    children: [
      jsxs('div', {
        className: 'flex min-w-0 flex-1 items-center gap-1',
        children: [
          PICKER
            ? boardWindow
            : boards.map((board) =>
                jsxs('span', {
                  key: board.id,
                  className: 'inline-flex shrink-0 items-center',
                  children: [
                    jsx(Button, {
                      variant: board.id === boardId ? 'secondary' : 'ghost',
                      size: 'xs',
                      className: 'max-w-[9rem]',
                      title: `${board.id}${board.title ? ` · ${board.title}` : ''}`,
                      onClick: () => choose(board.id),
                      children: jsx('span', { className: 'block truncate', children: board.title || board.id })
                    }),
                    jsx(Button, {
                      variant: 'ghost', size: 'icon-xs',
                      className: 'text-(--ui-text-quaternary) hover:text-(--ui-text-secondary)',
                      'aria-label': `delete board ${board.id}`,
                      title: `delete board ${board.id}`,
                      onClick: () => deleteBoard(board.id),
                      children: '×'
                    })
                  ]
                })
              ),
          jsx(Button, {
            variant: 'ghost', size: 'icon-xs',
            'aria-label': 'new board', title: 'new board',
            onClick: () => {
              setShowCreate(true)
              if (PICKER) setPickerOpen(true)
            },
            children: '+'
          })
        ]
      }),
      jsx(Button, {
        variant: face === 'idle' ? 'ghost' : 'secondary', size: 'xs',
        className: 'shrink-0 gap-1',
        disabled: !boardId,
        'aria-label': `save the board${status ? ` — ${status}` : ''}`,
        title: status || 'save the board now (the pane also saves by itself while you draw)',
        onClick: requestSave,
        children: [
          Codicon ? jsx(Codicon, { name: face.glyph, size: '0.8125rem', className: 'shrink-0 ' + face.tint }) : null,
          jsx('span', { className: 'shrink-0', children: face.label })
        ]
      }),
      /* The same news the control shows, for a screen reader: a live region costs no geometry, which
       * is the whole point — the board must not move when the pane has something to say. */
      jsx('span', {
        role: 'status',
        'aria-live': 'polite',
        style: { position: 'absolute', width: 1, height: 1, overflow: 'hidden',
                 clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' },
        children: status || ''
      }),
      jsx(Button, {
        variant: 'ghost', size: 'xs',
        className: 'shrink-0',
        'aria-label': 'auto-layout',
        title: 'place the graph automatically (ranks from the edges)',
        disabled: !boardId,
        onClick: runLayout,
        children: 'auto-layout'
      }),
      jsx(Button, {
        variant: 'ghost', size: 'xs',
        className: 'shrink-0',
        'aria-label': 'layout direction',
        title: layoutDir === 'down'
          ? 'laying out top-to-bottom — click for left-to-right'
          : 'laying out left-to-right — click for top-to-bottom',
        onClick: () => setLayoutDir((dir) => (dir === 'down' ? 'right' : 'down')),
        children: layoutDir === 'down' ? '↓' : '→'
      }),
      jsx(Button, {
        variant: 'ghost', size: 'xs',
        className: 'shrink-0',
        'aria-label': 'reload',
        title: 're-read this board from the backend',
        onClick: () => page.refetch && page.refetch(),
        children: 'reload'
      }),
      jsx(Badge, {
        className: 'shrink-0 text-[0.6875rem] text-(--ui-text-quaternary)',
        title: version ? `board version ${version}` : 'no board loaded',
        children: version ? version.slice(0, 7) : 'no board'
      })
    ]
  })

  /* Hidden until asked for: a pane this narrow cannot spend a whole row on a field that is used
   * once per board. In the board window it lives inside the popover instead. */
  const creator = showCreate && !PICKER
    ? jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          jsx(Input, {
            className: 'min-w-0 flex-1',
            placeholder: 'new board id (letters, digits, dash)',
            value: newId,
            autoFocus: true,
            onChange: (evt) => setNewId(evt.target.value),
            onKeyDown: (evt) => {
              if (evt.key === 'Enter') { createBoard(); setShowCreate(false) }
              if (evt.key === 'Escape') setShowCreate(false)
            }
          }),
          jsx(Button, {
            variant: 'outline', size: 'xs',
            onClick: () => { createBoard(); setShowCreate(false) },
            children: 'create'
          })
        ]
      })
    : null

  /* The canvas deserves the pane: an empty state that names the next step beats a line of grey
   * instructions under the controls. */
  const frame = !boardId
    ? jsxs('div', {
        className: 'flex h-full flex-col items-center justify-center gap-2 p-4 text-center',
        children: [
          jsx('div', {
            className: 'text-(--ui-text-tertiary)',
            children: boards.length
              ? 'No board selected.'
              : 'No boards yet — ask the agent to draw one (board_new + board_apply), or make one here.'
          }),
          jsx(Button, {
            variant: 'outline', size: 'xs',
            onClick: () => setShowCreate(true),
            children: 'new board'
          })
        ]
      })
    : page.data
    ? jsx(SandboxedFrame, {
        src: toDataUrl(page.data.html),
        title: `board ${boardId}`,
        className: 'h-full w-full min-h-0',
        ref: shared.frameRef,
        onLoad: () => setStatus('editor ready')
      })
    : jsx('div', {
        className: 'flex h-full items-center justify-center text-(--ui-text-tertiary)',
        children: GlyphSpinner
          ? jsx(GlyphSpinner, {})
          : 'loading board…'
      })

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-1.5 p-1.5 text-xs',
    children: [
      header,
      creator,
      error
        ? jsx('div', {
            className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-[0.6875rem]',
            children: ErrorState
              ? jsx(ErrorState, { message: error })
              : `board unavailable: ${error}`
          })
        : jsxs('div', {
            className: 'min-h-0 flex-1 overflow-hidden rounded-md border border-(--ui-stroke-secondary)',
            children: [frame]
          }),
      ConfirmDialog && confirmFor
        ? jsx(ConfirmDialog, {
            destructive: true,
            open: true,
            title: `Delete board “${confirmFor}”?`,
            description: 'The board file is deleted from disk — the agent’s board tools will not find it afterwards.',
            confirmLabel: 'delete',
            onClose: () => setConfirmFor(null),
            onConfirm: () => {
              deleteBoard(confirmFor, { ask: false })
              setConfirmFor(null)
            }
          })
        : null
    ]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Board',
  defaultEnabled: false,

  register(ctx) {
    const shared = {
      $boardId: atom(ctx.storage.get('boardId') || 'default'),
      frameRef: { current: null },
      versionRef: { current: '' }
    }

    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'board',
      data: { placement: 'right', dock: { pane: 'workspace', pos: 'right' }, width: '440px' },
      render: () => jsx(BoardPane, { ctx, shared })
    })

    /* Closing the pane is a persisted choice the app remembers, and a plugin cannot undo it at
     * load without overriding the user. So the way back is a row of their own: ⌘K → "Board: show
     * pane". Feature-detected — an older desktop has no reveal API, and a row that cannot work is
     * worse than no row. */
    if (typeof host.revealPane === 'function') {
      ctx.register({
        id: 'show-pane',
        area: sdk.PALETTE_AREA || 'palette',
        data: {
          id: `${PLUGIN_ID}.showPane`,
          label: 'Board: show pane',
          keywords: ['board', 'pane', 'panel', 'show', 'open', 'display'],
          run: () => host.revealPane(`${PLUGIN_ID}:pane`)
        }
      })
    }

    /* The agent wrote to the board: push it into the editor instead of reloading the frame,
     * so a half-finished drag in the pane is not thrown away. */
    const dispose = host.onEvent(EVENT, ({ payload }) => {
      if (!payload) return
      if (payload.deleted) {
        /* Someone removed a board: move to another one rather than showing a broken frame. */
        ctx.rest('/boards').then((res) => {
          const rest = ((res && res.boards) || []).filter((board) => board.id !== payload.id)
          if (payload.id === shared.$boardId.get()) {
            shared.$boardId.set(rest.length ? rest[0].id : '')
          }
          host.notify({ kind: 'info', message: `board ${payload.id} was deleted` })
        }).catch(() => {})
        return
      }
      if (payload.origin === 'pane') return
      if (payload.id !== shared.$boardId.get()) return
      ctx
        .rest(`/board?id=${encodeURIComponent(payload.id)}`)
        .then((res) => {
          shared.versionRef.current = res.version
          const frame = shared.frameRef.current
          const win = frame && frame.contentWindow
          if (win) win.postMessage({ type: 'ck-board-replace', board: res.board }, '*')
          host.notify({ kind: 'info', message: `board ${payload.id} updated by the agent` })
        })
        .catch(() => {/* the next query refetch picks it up */})
    })
    if (typeof dispose === 'function' && ctx.onDispose) ctx.onDispose(dispose)
  }
}
