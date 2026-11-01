// hermes-todo — the ambient rail card: the tree's summary, docked top-right.
// One row of counts with marks, the pending and running todos, the latest
// landing. Click-only (ambient captures no keys). Install:
//   cp widgets/todo-card.mjs ~/.hermes/tui-widgets/   then  /todo-card
//
// Configure the three consts below: the dashboard server that serves the
// plugin's route (hermes dashboard, or the Desktop app), the repo whose
// list the card shows, and that server's auth token. A plain
// `hermes --tui` session serves no route — that seam is the open gap
// tracked in docs/hermes-orchestration.md §5.

const BASE = 'http://127.0.0.1:8787'   // where `hermes dashboard` listens
const DIR = ''                         // the repo path (blank: server's cwd)
const TOKEN = ''                       // the dashboard auth token

const MARKS = {
  prompting: ['◔', '#5555f1'], pending: ['●', '#8b919c'], drafting: ['✎', '#55a3f1'],
  planning: ['◇', '#55f1a3'], running: ['▶', '#55f155'], paused: ['⏸', '#f1f155'],
  stopped: ['■', '#ff0000'], done: ['✓', '#a355f1'], failed: ['✗', '#f1a355'],
}
const ORDER = ['running', 'prompting', 'pending', 'failed', 'paused', 'stopped', 'done']

export default function register(sdk) {
  const { Text, Dialog, defineWidgetApp, updateWidget, h, React } = sdk

  const headers = () => (TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {})
  async function poll() {
    try {
      const res = await fetch(
        `${BASE}/api/plugins/hermes-todo/backlog?dir=${encodeURIComponent(DIR)}`,
        { headers: headers() },
      )
      if (!res.ok) return { phase: 'error', note: `route answered ${res.status}` }
      return { phase: 'ok', ...(await res.json()) }
    } catch {
      return { phase: 'error', note: 'no route — is the dashboard server up?' }
    }
  }
  const land = (fresh) => updateWidget(app, (s) => ({ ...(s || {}), ...fresh }))

  defineWidgetApp({
    id: 'todo-card',
    mode: 'ambient',
    zone: 'top-right',
    width: 44,
    help: 'the todo rail card: counts, pending and running, latest landing',

    async init() {
      land(await poll())  // first fetch lands now; a closed card no-ops
      return {}
    },
    reduce: (state) => state,
    render: ({ state, t }) => h(Panel, { t, state: state || { phase: 'loading' } }),
  })

  function Panel({ t, state }) {
    React.useEffect(() => {
      const timer = setInterval(async () => land(await poll()), 1000)
      return () => clearInterval(timer)
    }, [])
    const rows = []
    if (state.phase === 'loading' || state.phase === 'error') {
      rows.push(h(Text, { key: 'p', color: state.phase === 'error' ? t.color.error : t.color.muted },
        state.phase === 'error' ? `Todos: ${state.note}` : 'Todos …'))
    } else {
      const counts = state.counts || {}
      rows.push(h(Text, { key: 'h', color: t.color.label }, 'Todos '))
      for (const s of ORDER.filter((x) => counts[x])) {
        rows.push(h(Text, { key: s, color: MARKS[s][1] }, `${MARKS[s][0]}${counts[s]} `))
      }
      for (const todo of (state.todos || [])
        .filter((x) => x.status === 'pending' || x.status === 'running').slice(0, 3)) {
        const [glyph, color] = MARKS[todo.status] || ['·', t.color.muted]
        rows.push(h(Text, { key: `t${todo.n}`, color: t.color.label },
          `${glyph} ${todo.n} ${todo.title}`.slice(0, 44)))
      }
      const last = (state.todos || []).find((x) => x.status === 'done' && x.landed)
      if (last) {
        rows.push(h(Text, { key: 'z', color: t.color.muted },
          `last: ✓ #${last.n} landed ${String(last.landed).slice(0, 7)}`))
      }
    }
    return h(Dialog, { width: 44 }, ...rows)
  }

  const app = { id: 'todo-card' }
}
