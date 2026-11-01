// hermes-todo — the pane (/todo-pane opens it): the tree with its chips, a
// details view over the recorded calls, Esc to close. Install:
//   cp widgets/todo.mjs ~/.hermes/tui-widgets/   then  /todo-pane
//
// The chips are what the route says each row offers (the same offers the
// /todo verbs read), so the pane and the verbs can't drift. A run verb
// (exec, resume, stop…) answers with a note to type its /todo verb in the
// chat — runs belong to the session that owns the todo. Configure the
// three consts below (see widgets/todo-card.mjs for the seam's story).

const BASE = 'http://127.0.0.1:8787'
const DIR = ''
const TOKEN = ''
const POLL_MS = 1000

const MARKS = {
  prompting: ['◔', '#5555f1'], pending: ['●', '#8b919c'], drafting: ['✎', '#55a3f1'],
  planning: ['◇', '#55f1a3'], running: ['▶', '#55f155'], paused: ['⏸', '#f1f155'],
  stopped: ['■', '#ff0000'], done: ['✓', '#a355f1'], failed: ['✗', '#f1a355'],
}
const CHIPS = {
  exec: ['▶', '#55f155'], auto: ['⚡', '#55f155'], rename: ['✎', '#a3f155'],
  delete: ['✕', '#f155a3'], details: ['☰', '#55f1f1'], reset: ['↺', '#f155f1'],
  pause: ['⏸', '#f1f155'], stop: ['■', '#ff0000'], resume: ['⏵', '#55f155'],
  refine: ['✨', '#55a3f1'], restart: ['↻', '#55a3f1'], leave: ['✓', '#f155f1'],
  takeover: ['⇄', '#ff0000'],
}
const RULE = '─'.repeat(74)

export default function register(sdk) {
  const { Text, Dialog, Overlay, defineWidgetApp, updateWidget, h, React } = sdk

  const headers = () => (TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {})
  async function get(path) {
    try {
      const res = await fetch(`${BASE}/api/plugins/hermes-todo${path}`, { headers: headers() })
      return res.ok ? await res.json() : { error: `route answered ${res.status}` }
    } catch {
      return { error: 'no route — is the dashboard server up?' }
    }
  }
  async function post(body) {
    try {
      const res = await fetch(`${BASE}/api/plugins/hermes-todo/action`, {
        method: 'POST',
        headers: { ...headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ dir: DIR, ...body }),
      })
      return res.ok ? await res.json() : { error: `route answered ${res.status}` }
    } catch {
      return { error: 'no route — is the dashboard server up?' }
    }
  }
  const land = (fresh) => updateWidget(app, (s) => ({ ...(s || {}), ...fresh }))
  const refresh = async () => land({ data: await get(`/backlog?dir=${encodeURIComponent(DIR)}`) })

  // A chip press, from the reduce (land the note once the route answers).
  async function press(todo, verb, confirmed) {
    if (verb === 'details') {
      land({ details: await get(`/details?dir=${encodeURIComponent(DIR)}&n=${todo.n}`) })
      return
    }
    const res = await post({ verb, n: todo.n, confirmed })
    const note = res.error || res.note || (res.ok ? `# ${todo.n}: ${verb} done` : `# ${todo.n}: ${res.error || verb} refused`)
    land({ note })
    await refresh()
  }

  defineWidgetApp({
    id: 'todo-pane',
    mode: 'modal',
    help: 'the todo pane: the tree, its chips, the recorded calls',

    async init() {
      land({ data: await get(`/backlog?dir=${encodeURIComponent(DIR)}`) })
      return { row: 0, chip: 0, view: 'list', note: '' }
    },

    reduce: (state, { ch, key }) => {
      if (!state) return state
      if (state.view === 'details') {
        if (key.escape || ch === 'q') return { ...state, view: 'list', note: '' }
        return state
      }
      if (key.escape) return null  // Esc closes the pane, back to the composer
      const rows = (state.data && state.data.todos) || []
      if (!rows.length) return state
      if (key.upArrow) return { ...state, row: Math.max(0, state.row - 1), chip: 0, note: '' }
      if (key.downArrow) {
        return { ...state, row: Math.min(rows.length - 1, state.row + 1), chip: 0, note: '' }
      }
      const todo = rows[state.row]
      const chips = (todo && todo.offers) || []
      if (key.leftArrow) return { ...state, chip: Math.max(0, state.chip - 1) }
      if (key.rightArrow) return { ...state, chip: Math.min(chips.length - 1, state.chip + 1) }
      if (key.return) {
        const verb = chips[state.chip]
        if (!verb) return state
        if (verb === 'details') return { ...state, view: 'details', n: todo.n }
        // A chip that destroys asks first; the second Enter confirms.
        const confirming = state.confirm === `del:${todo.n}`
        if (verb === 'delete' && !confirming) return { ...state, confirm: `del:${todo.n}` }
        press(todo, verb, confirming)
        return { ...state, confirm: null, note: '…' }
      }
      return state
    },

    render: ({ state, t }) => h(Pane, { t, state: state || { view: 'list', row: 0, chip: 0, note: '' } }),
  })

  function Pane({ t, state }) {
    React.useEffect(() => {
      if (state.view !== 'list') return
      const timer = setInterval(refresh, POLL_MS)
      return () => clearInterval(timer)
    }, [state.view])

    const lines = []
    const line = (children) => lines.push(children)
    const data = state.data

    if (!data) {
      line([h(Text, { key: 'w', color: t.color.muted }, 'the pane is loading…')])
    } else if (data.error) {
      line([h(Text, { key: 'e', color: t.color.error }, data.error)])
    } else if (state.view === 'details') {
      const d = state.details || { todo: { n: state.n, title: '', status: '?' }, agents: [] }
      line([h(Text, { key: 'h', color: t.color.label },
        `☰ #${d.todo.n} ${d.todo.title} — ${d.todo.status}`)])
      if (d.todo.prompt) line([h(Text, { key: 'p', color: t.color.label }, `prompt: ${d.todo.prompt}`)])
      if (d.todo.summary) line([h(Text, { key: 's', color: t.color.muted }, `summary: ${d.todo.summary}`)])
      if (d.todo.branch) line([h(Text, { key: 'b', color: t.color.muted },
        `branch: ${d.todo.branch}${d.todo.landed ? `, landed ${d.todo.landed}` : ''}`)])
      if (d.error) line([h(Text, { key: 'de', color: t.color.error }, d.error)])
      for (const a of d.agents || []) {
        line([h(Text, { key: `a${a.id}`, color: t.color.muted },
          `${a.status} worker ${String(a.id).slice(0, 12)}, ${a.calls.length} calls`)])
        for (const c of a.calls.slice(-6)) {
          line([h(Text, { key: `c${c.id}`, color: c.state === 'err' ? t.color.error : t.color.muted },
            `  ${c.state === 'err' ? '✗' : '⏺'} ${c.tool} ${String(c.args || '').slice(0, 48)}`)])
        }
      }
      line([h(Text, { key: 'back', color: t.color.muted }, 'Esc: back to the list')])
    } else {
      const rows = data.todos || []
      if (state.note) line([h(Text, { key: 'n', color: t.color.ok }, state.note)])
      if (!rows.length) {
        line([h(Text, { key: 'z', color: t.color.muted },
          'Nothing in this view. ＋ add one with /todo add <title>')])
      }
      rows.forEach((todo, i) => {
        const [glyph, color] = MARKS[todo.status] || ['·', t.color.muted]
        if (i > 0) line([h(Text, { key: `r${todo.n}`, color: t.color.subtle }, RULE)])
        const focused = i === state.row
        line([
          h(Text, { key: 'f', color: focused ? t.color.label : t.color.subtle }, focused ? '❯' : ' '),
          h(Text, { key: 'g', color }, `${glyph} #${todo.n} `),
          h(Text, { key: 't', color: focused ? t.color.label : color }, todo.title),
        ])
        if (focused) {
          line((todo.offers || []).map((verb, j) => {
            const [sym, tone] = CHIPS[verb] || ['·', t.color.muted]
            const lit = j === state.chip
            return h(Text, {
              key: verb,
              backgroundColor: lit ? tone : undefined,
              color: lit ? '#0d0f14' : tone,
            }, ` ${sym} `)
          }))
          if (state.confirm === `del:${todo.n}`) {
            line([h(Text, { key: 'q', color: MARKS.stopped[1] },
              `✕ remove #${todo.n}? Enter again to confirm, an arrow key to keep`)])
          }
        }
      })
      line([h(Text, { key: 'keys', color: t.color.muted },
        '↑↓ todo · ←→ chip · Enter press · Esc close')])
    }
    return h(Overlay, {}, h(Dialog, { width: 76 }, ...lines))
  }

  const app = { id: 'todo-pane' }
}
