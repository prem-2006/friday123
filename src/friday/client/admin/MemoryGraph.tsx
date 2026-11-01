// Friday: the memory graph on the Admin page, like Obsidian's graph view, in 3D.
// Every note in Friday's Obsidian vault is a dot; lines join it to the people,
// topics and days it is about (see src/friday/server/memory-graph.ts). Drag
// to turn it, right-drag to move it, scroll to zoom, hover to light up a
// dot's connections, click a note to read it or open it in Obsidian.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, Loader2, RefreshCw, X } from 'lucide-react';
import { ownerHeaders } from '../stage/activity';

type Kind = 'memory' | 'conversation' | 'note' | 'person' | 'topic' | 'day';

interface GraphNode {
  id: string;
  label: string;
  kind: Kind;
  path?: string;
  degree: number;
}
interface Graph {
  vault: string;
  obsidianUrl: string;
  nodes: GraphNode[];
  links: { source: string; target: string }[];
}
interface Note {
  path: string;
  title: string;
  content: string;
  obsidianUrl: string;
}
interface Dot extends GraphNode {
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  r: number;
}

const KINDS: { kind: Kind; label: string; color: string }[] = [
  { kind: 'memory', label: 'Memories', color: '#f5b84b' },
  { kind: 'conversation', label: 'Conversations', color: '#8f9bb3' },
  { kind: 'note', label: 'Other notes', color: '#c7ccd6' },
  { kind: 'person', label: 'People', color: '#4ca7a1' },
  { kind: 'topic', label: 'Topics', color: '#e6d6ea' },
  { kind: 'day', label: 'Days', color: '#58c9f3' },
];
const COLOR = Object.fromEntries(KINDS.map((k) => [k.kind, k.color])) as Record<
  Kind,
  string
>;
const ACCENT = '#58c9f3';

export function MemoryGraph() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [hidden, setHidden] = useState<Set<Kind>>(new Set());
  const [query, setQuery] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [hub, setHub] = useState<GraphNode | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/friday/memory/graph', {
        headers: ownerHeaders(),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setGraph((await response.json()) as Graph);
      setError('');
    } catch (e) {
      setError(
        `The memory graph did not load (${e instanceof Error ? e.message : e}).`,
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => void load(), [load]);

  const open = useCallback(async (node: GraphNode) => {
    if (!node.path) {
      setNote(null);
      setHub(node);
      return;
    }
    setHub(null);
    const response = await fetch(
      `/api/friday/memory/note?path=${encodeURIComponent(node.path)}`,
      { headers: ownerHeaders() },
    ).catch(() => null);
    if (response?.ok) setNote((await response.json()) as Note);
  }, []);

  const counts = useMemo(() => {
    const out: Partial<Record<Kind, number>> = {};
    for (const node of graph?.nodes ?? [])
      out[node.kind] = (out[node.kind] ?? 0) + 1;
    return out;
  }, [graph]);

  return (
    <section className="admin-section memory-graph-section">
      <div className="memory-graph-head">
        <h2>Memory graph</h2>
        <span className="memory-graph-sub">
          {graph
            ? `${graph.nodes.length} dots · ${graph.links.length} links · ${graph.vault}`
            : 'Friday’s Obsidian vault'}
        </span>
        <div className="memory-graph-actions">
          <input
            type="search"
            placeholder="Find a note, person or topic"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="button" onClick={() => void load()} disabled={loading}>
            {loading ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <RefreshCw size={15} />
            )}
            Refresh
          </button>
          {graph && (
            <a className="memory-graph-obsidian" href={graph.obsidianUrl}>
              <ExternalLink size={15} />
              Open vault in Obsidian
            </a>
          )}
        </div>
      </div>
      <div className="memory-graph-legend">
        {KINDS.map(({ kind, label, color }) => (
          <label key={kind} className={hidden.has(kind) ? 'off' : ''}>
            <input
              type="checkbox"
              checked={!hidden.has(kind)}
              onChange={() =>
                setHidden((set) => {
                  const next = new Set(set);
                  if (next.has(kind)) next.delete(kind);
                  else next.add(kind);
                  return next;
                })
              }
            />
            <span
              className="memory-graph-swatch"
              style={{ background: color }}
            />
            {label} <small>{counts[kind] ?? 0}</small>
          </label>
        ))}
      </div>
      {error && <p className="admin-banner tone-down">{error}</p>}
      <div className="memory-graph-body">
        {graph && graph.nodes.length > 0 ? (
          <GraphCanvas
            graph={graph}
            hidden={hidden}
            query={query}
            onPick={(node) => void open(node)}
          />
        ) : (
          <p className="memory-graph-empty">
            {graph
              ? 'No notes yet. Talk to Friday, or say “remember that…”.'
              : 'Loading…'}
          </p>
        )}
        {(note || hub) && (
          <aside className="memory-graph-note">
            <button
              type="button"
              className="memory-graph-close"
              aria-label="Close"
              onClick={() => {
                setNote(null);
                setHub(null);
              }}
            >
              <X size={15} />
            </button>
            {note && (
              <>
                {/* The note's own heading, not its file name (an id). */}
                <h3>{/^#\s+(.+)/u.exec(note.content)?.[1] ?? note.title}</h3>
                <p className="memory-graph-path">{note.path}</p>
                <pre>{note.content.replace(/^#\s+.+\n*/u, '')}</pre>
                <a href={note.obsidianUrl}>
                  <ExternalLink size={14} /> Open in Obsidian
                </a>
              </>
            )}
            {hub && graph && (
              <>
                <h3>{hub.label}</h3>
                <p className="memory-graph-path">
                  {KINDS.find((k) => k.kind === hub.kind)?.label} · linked to{' '}
                  {hub.degree} notes
                </p>
                <ul>
                  {graph.links
                    .filter((l) => l.source === hub.id || l.target === hub.id)
                    .map((l) => (l.source === hub.id ? l.target : l.source))
                    .map((id) => graph.nodes.find((n) => n.id === id))
                    .filter((n): n is GraphNode => !!n?.path)
                    .slice(0, 40)
                    .map((n) => (
                      <li key={n.id}>
                        <button type="button" onClick={() => void open(n)}>
                          {n.label}
                        </button>
                      </li>
                    ))}
                </ul>
              </>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}

// The graph in 3D: a force layout in space, seen through a perspective
// camera. It turns slowly by itself; drag to turn it, right-drag or
// Shift-drag to move it, scroll to zoom. Near dots are drawn bigger and on top.
function GraphCanvas({
  graph,
  hidden,
  query,
  onPick,
}: {
  graph: Graph;
  hidden: Set<Kind>;
  query: string;
  onPick: (node: GraphNode) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({
    dots: new Map<string, Dot>(),
    // yaw/pitch: the camera's turn; x/y: screen offset; k: zoom.
    view: { yaw: 0.6, pitch: -0.35, x: 0, y: 0, k: 1 },
    hover: null as Dot | null,
    drag: null as null | {
      x: number;
      y: number;
      moved: boolean;
      pan: boolean;
      dot?: Dot;
    },
    lastTouched: 0,
    heat: 1,
    // Where each dot was last drawn, for hovering and clicking.
    drawn: [] as { dot: Dot; sx: number; sy: number; r: number }[],
  });
  const visible = useMemo(() => {
    const nodes = graph.nodes.filter((n) => !hidden.has(n.kind));
    const ids = new Set(nodes.map((n) => n.id));
    const links = graph.links.filter(
      (l) => ids.has(l.source) && ids.has(l.target),
    );
    return { nodes, links };
  }, [graph, hidden]);
  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const { source, target } of visible.links) {
      if (!map.has(source)) map.set(source, new Set());
      if (!map.has(target)) map.set(target, new Set());
      map.get(source)!.add(target);
      map.get(target)!.add(source);
    }
    return map;
  }, [visible]);

  // Keep dots that were already placed; new ones start in a ball.
  useEffect(() => {
    const s = state.current;
    const next = new Map<string, Dot>();
    for (const node of visible.nodes) {
      const old = s.dots.get(node.id);
      const r = node.path
        ? 3.2 + Math.min(4, node.degree * 0.5)
        : 5 + Math.min(14, Math.sqrt(node.degree) * 2.4);
      const spread = () => (Math.random() - 0.5) * 400;
      next.set(
        node.id,
        old
          ? { ...old, ...node, r }
          : {
              ...node,
              r,
              x: spread(),
              y: spread(),
              z: spread(),
              vx: 0,
              vy: 0,
              vz: 0,
            },
      );
    }
    s.dots = next;
    s.heat = 1;
  }, [visible]);

  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext('2d')!;
    const s = state.current;
    let frame = 0;
    const size = () => {
      const { width, height } = el.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      el.width = Math.round(width * dpr);
      el.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { width, height };
    };
    let box = size();
    const resize = new ResizeObserver(() => {
      box = size();
    });
    resize.observe(el);
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      s.view.k = Math.min(
        6,
        Math.max(0.2, s.view.k * Math.exp(-e.deltaY * 0.0015)),
      );
      s.lastTouched = performance.now();
    };
    el.addEventListener('wheel', wheel, { passive: false });
    const needle = query.trim().toLowerCase();
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

    const tick = () => {
      const dots = [...s.dots.values()];
      if (s.heat > 0.02) {
        // Repel every pair, pull along links, drift to the middle.
        for (let i = 0; i < dots.length; i++) {
          const a = dots[i];
          for (let j = i + 1; j < dots.length; j++) {
            const b = dots[j];
            let dx = b.x - a.x;
            let dy = b.y - a.y;
            let dz = b.z - a.z;
            let d2 = dx * dx + dy * dy + dz * dz;
            if (d2 < 0.01) {
              dx = Math.random() - 0.5;
              dy = Math.random() - 0.5;
              dz = Math.random() - 0.5;
              d2 = 0.25;
            }
            if (d2 > 250_000) continue;
            const f = (1400 / d2) * s.heat * 0.05;
            a.vx -= dx * f;
            a.vy -= dy * f;
            a.vz -= dz * f;
            b.vx += dx * f;
            b.vy += dy * f;
            b.vz += dz * f;
          }
        }
        for (const { source, target } of visible.links) {
          const a = s.dots.get(source);
          const b = s.dots.get(target);
          if (!a || !b) continue;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dz = b.z - a.z;
          const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
          const f = ((d - 70) / d) * 0.04 * s.heat;
          a.vx += dx * f;
          a.vy += dy * f;
          a.vz += dz * f;
          b.vx -= dx * f;
          b.vy -= dy * f;
          b.vz -= dz * f;
        }
        for (const dot of dots) {
          dot.vx -= dot.x * 0.002 * s.heat;
          dot.vy -= dot.y * 0.002 * s.heat;
          dot.vz -= dot.z * 0.002 * s.heat;
          dot.vx *= 0.82;
          dot.vy *= 0.82;
          dot.vz *= 0.82;
          dot.x += dot.vx;
          dot.y += dot.vy;
          dot.z += dot.vz;
        }
        s.heat *= 0.994;
      }
      // A slow turn while nobody is touching it.
      if (!still && !s.drag && performance.now() - s.lastTouched > 2500)
        s.view.yaw += 0.0025;

      // Camera: turn by yaw (around up) then pitch (around across), then
      // perspective from a fixed distance.
      const { yaw, pitch, k } = s.view;
      const cy = Math.cos(yaw);
      const sy = Math.sin(yaw);
      const cp = Math.cos(pitch);
      const sp = Math.sin(pitch);
      const distance = 900;
      const cx0 = box.width / 2 + s.view.x;
      const cy0 = box.height / 2 + s.view.y;
      const project = (dot: Dot) => {
        const x1 = dot.x * cy - dot.z * sy;
        const z1 = dot.x * sy + dot.z * cy;
        const y2 = dot.y * cp - z1 * sp;
        const z2 = dot.y * sp + z1 * cp;
        const scale = (distance / (distance + z2)) * k;
        return { sx: cx0 + x1 * scale, sy: cy0 + y2 * scale, scale, depth: z2 };
      };
      const placed = new Map<string, ReturnType<typeof project>>();
      for (const dot of dots) placed.set(dot.id, project(dot));

      ctx.clearRect(0, 0, box.width, box.height);
      const focus = s.hover;
      const near = focus ? neighbours.get(focus.id) : undefined;
      const lit = (id: string) => !focus || id === focus.id || !!near?.has(id);
      const found = (dot: Dot) =>
        !!needle && dot.label.toLowerCase().includes(needle);
      // Far things are dimmer, like fog.
      const fog = (depth: number) =>
        Math.max(0.25, Math.min(1, 1 - depth / (distance * 1.4)));

      ctx.lineWidth = 1;
      for (const { source, target } of visible.links) {
        const a = placed.get(source);
        const b = placed.get(target);
        if (!a || !b) continue;
        const on = focus && (source === focus.id || target === focus.id);
        const alpha = on ? 0.85 : focus ? 0.04 : 0.14;
        ctx.strokeStyle = on
          ? `rgba(88, 201, 243, ${alpha})`
          : `rgba(255, 255, 255, ${alpha * fog((a.depth + b.depth) / 2)})`;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }
      // Far to near, so near dots cover far ones.
      const order = dots
        .map((dot) => ({ dot, p: placed.get(dot.id)! }))
        .sort((a, b) => b.p.depth - a.p.depth);
      s.drawn = [];
      for (const { dot, p } of order) {
        const r = Math.max(1.5, dot.r * p.scale);
        const shown = lit(dot.id) || found(dot);
        ctx.globalAlpha = (shown ? 1 : 0.18) * fog(p.depth);
        const color = dot === focus || found(dot) ? ACCENT : COLOR[dot.kind];
        // A soft sphere: lighter towards the top left.
        const shade = ctx.createRadialGradient(
          p.sx - r * 0.35,
          p.sy - r * 0.35,
          r * 0.1,
          p.sx,
          p.sy,
          r,
        );
        shade.addColorStop(0, '#ffffff');
        shade.addColorStop(0.25, color);
        shade.addColorStop(1, color);
        ctx.fillStyle = shade;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
        ctx.fill();
        if (found(dot)) {
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.lineWidth = 1;
        }
        s.drawn.push({ dot, sx: p.sx, sy: p.sy, r });
      }
      // Labels: hubs always, notes when zoomed in, everything near the hover.
      ctx.textAlign = 'center';
      for (const { dot, p } of order) {
        const show =
          dot === focus ||
          !!near?.has(dot.id) ||
          found(dot) ||
          (!dot.path && dot.degree > 1) ||
          k > 1.8;
        if (!show) continue;
        ctx.globalAlpha =
          (lit(dot.id) || found(dot) ? 0.95 : 0.15) * fog(p.depth);
        ctx.font = `${Math.max(9, Math.min(14, 11 * p.scale))}px 'Segoe UI', system-ui, sans-serif`;
        ctx.fillStyle = '#e6e8f5';
        ctx.fillText(dot.label, p.sx, p.sy + dot.r * p.scale + 12);
      }
      ctx.globalAlpha = 1;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      el.removeEventListener('wheel', wheel);
    };
  }, [visible, neighbours, query]);

  const dotAt = (event: { clientX: number; clientY: number }) => {
    const rect = canvas.current!.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    // The nearest dot on top: drawn last means nearest the camera.
    for (let i = state.current.drawn.length - 1; i >= 0; i--) {
      const hit = state.current.drawn[i];
      if (Math.hypot(hit.sx - x, hit.sy - y) < Math.max(hit.r + 3, 7))
        return hit.dot;
    }
    return null;
  };

  return (
    <canvas
      ref={canvas}
      className="memory-graph-canvas"
      onContextMenu={(e) => e.preventDefault()}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        state.current.drag = {
          x: e.clientX,
          y: e.clientY,
          moved: false,
          pan: e.button === 2 || e.shiftKey,
          dot: dotAt(e) ?? undefined,
        };
        state.current.lastTouched = performance.now();
      }}
      onPointerMove={(e) => {
        const s = state.current;
        const drag = s.drag;
        if (!drag) {
          s.hover = dotAt(e);
          e.currentTarget.style.cursor = s.hover ? 'pointer' : 'grab';
          return;
        }
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        drag.x = e.clientX;
        drag.y = e.clientY;
        if (drag.pan) {
          s.view.x += dx;
          s.view.y += dy;
        } else {
          s.view.yaw += dx * 0.008;
          s.view.pitch = Math.max(
            -1.45,
            Math.min(1.45, s.view.pitch + dy * 0.008),
          );
        }
        s.lastTouched = performance.now();
      }}
      onPointerUp={() => {
        const drag = state.current.drag;
        state.current.drag = null;
        state.current.lastTouched = performance.now();
        if (drag?.dot && !drag.moved) onPick(drag.dot);
      }}
      onPointerLeave={() => {
        state.current.hover = null;
      }}
    />
  );
}
