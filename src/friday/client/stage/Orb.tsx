// Friday: the living orb. A 3D sphere of particles turns inside glowing
// rings that ripple with the voice level, HUD rings spin around it, waves flow
// out to both sides, and busy helper Dots orbit around it. Its colour follows
// what Friday is doing, in the Aqua Nebula palette: glacier listening,
// orchid thinking, lagoon speaking.
import { useEffect, useRef } from 'react';

export type OrbMode =
  'idle' | 'listening' | 'thinking' | 'speaking' | 'working';

const HELPER_COLORS: Record<string, string> = {
  Navigator: '#38e1ff',
  Operator: '#ff5ad1',
  Friday: '#8b7bff',
};
const MODE_ENERGY: Record<OrbMode, number> = {
  idle: 0.12,
  listening: 0.28,
  thinking: 0.42,
  speaking: 0.3,
  working: 0.5,
};
const MODE_SPIN: Record<OrbMode, number> = {
  idle: 0.12,
  listening: 0.22,
  thinking: 0.9,
  speaking: 0.35,
  working: 0.6,
};

// The particle sphere's colour per mode, as "r,g,b".
const MODE_TINT: Record<OrbMode, [string, string]> = {
  idle: ['88,201,243', '47,160,198'],
  listening: ['189,229,255', '88,201,243'],
  thinking: ['230,214,234', '47,160,198'],
  speaking: ['88,201,243', '76,167,161'],
  working: ['76,167,161', '189,229,255'],
};

// Evenly spread points on a unit sphere (a Fibonacci lattice).
const POINTS = 460;
const SPHERE = Array.from({ length: POINTS }, (_, i) => {
  const y = 1 - (2 * (i + 0.5)) / POINTS;
  const r = Math.sqrt(1 - y * y);
  const a = i * Math.PI * (3 - Math.sqrt(5));
  return { x: Math.cos(a) * r, y, z: Math.sin(a) * r, seed: (i * 7919) % 97 };
});

function paletteGradient(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  angle: number,
  alpha: number,
) {
  const stops: [number, string][] = [
    [0, `rgba(88,201,243,${alpha})`],
    [0.3, `rgba(47,160,198,${alpha})`],
    [0.55, `rgba(76,167,161,${alpha})`],
    [0.75, `rgba(230,214,234,${alpha})`],
    [1, `rgba(88,201,243,${alpha})`],
  ];
  if ('createConicGradient' in ctx) {
    const gradient = ctx.createConicGradient(angle, cx, cy);
    for (const [at, color] of stops) gradient.addColorStop(at, color);
    return gradient;
  }
  const gradient = (ctx as CanvasRenderingContext2D).createLinearGradient(
    cx - 200,
    cy,
    cx + 200,
    cy,
  );
  for (const [at, color] of stops) gradient.addColorStop(at, color);
  return gradient;
}

const BARS = 64;

export function Orb({
  mode,
  level,
  spectrum,
  helpers,
}: {
  mode: OrbMode;
  /** 0..1 voice level, read every frame. */
  level: () => number;
  /** Frequency bars (0..255) of the voice being heard or spoken, read every frame. */
  spectrum?: () => Uint8Array | null;
  helpers: string[];
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({
    mode,
    helpers,
    energy: 0.12,
    spin: 0,
    voice: 0,
    bass: 0,
    bars: new Float32Array(BARS),
  });
  state.current.mode = mode;
  state.current.helpers = helpers;

  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    const reduced = window.matchMedia?.(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    let frame = 0;
    let last = performance.now();
    let t = 0;
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      element.width = element.clientWidth * ratio;
      element.height = element.clientHeight * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize);

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      t += reduced ? dt * 0.25 : dt;
      const s = state.current;
      const voice = level();
      s.voice += (voice - s.voice) * 0.25;
      // Music-style bars: mirror the lower half of the spectrum (where the
      // voice is) around the orb, rising fast and falling slowly.
      const bins = spectrum?.() ?? null;
      let bass = 0;
      for (let i = 0; i < BARS; i++) {
        const half = BARS / 2;
        const step = (i < half ? i : BARS - 1 - i) / half; // 0 top .. 1 bottom
        // Higher bars are boosted: a voice is quieter there than in the lows.
        const bin = bins
          ? Math.min(
              1,
              ((bins[1 + Math.floor(step * bins.length * 0.5)] ?? 0) / 255) *
                (1 + step * 2.2),
            )
          : 0;
        s.bars[i] = bin > s.bars[i] ? bin : s.bars[i] * 0.86;
        if (i < 4) bass += bin / 4;
      }
      s.bass += (bass - s.bass) * 0.3;
      const target =
        MODE_ENERGY[s.mode] +
        (s.mode === 'listening' || s.mode === 'speaking' ? s.voice * 1.25 : 0);
      s.energy += (target - s.energy) * 0.08;
      s.spin += dt * MODE_SPIN[s.mode];
      const w = element.clientWidth;
      const h = element.clientHeight;
      const cx = w / 2;
      const cy = h * 0.52;
      const R = Math.max(64, Math.min(170, Math.min(w, h) * 0.15));
      const e = s.energy;
      ctx.clearRect(0, 0, w, h);

      // Halo behind everything.
      ctx.globalCompositeOperation = 'source-over';
      const halo = ctx.createRadialGradient(cx, cy, R * 0.3, cx, cy, R * 3.4);
      halo.addColorStop(0, `rgba(47,160,198,${0.28 + e * 0.35})`);
      halo.addColorStop(0.35, `rgba(28,78,117,${0.1 + e * 0.12})`);
      halo.addColorStop(1, 'rgba(6,24,38,0)');
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, w, h);

      ctx.globalCompositeOperation = 'lighter';
      // Waves flowing out of the orb to both edges.
      for (const side of [-1, 1]) {
        for (let i = 0; i < 4; i++) {
          const startX = cx + side * R * 0.85;
          const endX = side < 0 ? 0 : w;
          const span = Math.abs(endX - startX);
          const gradient = ctx.createLinearGradient(startX, 0, endX, 0);
          gradient.addColorStop(0, `rgba(189,229,255,${0.7 - i * 0.12})`);
          gradient.addColorStop(0.35, `rgba(88,201,243,${0.65 - i * 0.12})`);
          gradient.addColorStop(0.7, `rgba(47,160,198,${0.4 - i * 0.08})`);
          gradient.addColorStop(1, 'rgba(47,160,198,0)');
          ctx.beginPath();
          for (let step = 0; step <= span; step += 4) {
            const u = step / span;
            const envelope =
              Math.pow(Math.sin(Math.PI * Math.min(1, u * 1.1)), 1.3) *
              (1 - u * 0.55);
            const amp = R * 0.5 * (0.3 + e * 1.1) * envelope * (1 - i * 0.17);
            const y =
              cy +
              amp *
                Math.sin(
                  step * 0.016 * (1 + i * 0.22) -
                    t * (1.5 + i * 0.35) +
                    i * 1.3,
                ) +
              amp * 0.35 * Math.sin(step * 0.043 - t * 2.4 + i);
            const x = startX + side * step;
            if (step === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.strokeStyle = gradient;
          ctx.lineWidth = 2.6 - i * 0.45;
          ctx.shadowBlur = 16;
          ctx.shadowColor = 'rgba(47,160,198,0.9)';
          ctx.stroke();
        }
      }

      // Soft bloom: translucent filled lobes that breathe inside the rings.
      for (let k = 0; k < 3; k++) {
        ctx.beginPath();
        for (let a = 0; a <= Math.PI * 2 + 0.05; a += 0.05) {
          const wobble =
            Math.sin(2 * a + t * (0.8 + k * 0.3) + k * 2.1) * 0.6 +
            Math.sin(4 * a - t * 1.1 + k) * 0.4;
          const r = R * (1.02 + k * 0.05) * (1 + wobble * (0.05 + e * 0.12));
          const angle = a - s.spin * (0.4 + k * 0.15);
          const x = cx + Math.cos(angle) * r;
          const y = cy + Math.sin(angle) * r;
          if (a === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        const bloom = ctx.createRadialGradient(
          cx,
          cy,
          R * 0.55,
          cx,
          cy,
          R * 1.25,
        );
        const hue = ['47,160,198', '76,167,161', '230,214,234'][k];
        bloom.addColorStop(0, `rgba(${hue},0)`);
        bloom.addColorStop(0.8, `rgba(${hue},${0.1 + e * 0.12})`);
        bloom.addColorStop(1, `rgba(${hue},0)`);
        ctx.fillStyle = bloom;
        ctx.shadowBlur = 0;
        ctx.fill();
      }

      // Rippling rings.
      for (let k = 0; k < 6; k++) {
        ctx.beginPath();
        const direction = k % 2 ? -1 : 1;
        for (let a = 0; a <= Math.PI * 2 + 0.05; a += 0.035) {
          const wobble =
            Math.sin(3 * a + t * 1.3 + k * 1.7) * 0.5 +
            Math.sin(5 * a - t * 1.9 + k) * 0.3 +
            Math.sin(7 * a + t * 2.6 + k * 0.6) * 0.2;
          const r = R * (0.9 + k * 0.035) * (1 + wobble * (0.045 + e * 0.13));
          const angle = a + s.spin * direction * (0.6 + k * 0.1);
          const x = cx + Math.cos(angle) * r;
          const y = cy + Math.sin(angle) * r;
          if (a === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = paletteGradient(
          ctx,
          cx,
          cy,
          s.spin * 1.4 + k,
          0.5 + e * 0.35,
        );
        ctx.lineWidth = 2.4 - k * 0.2;
        ctx.shadowBlur = 28;
        ctx.shadowColor = 'rgba(88,201,243,0.95)';
        ctx.stroke();
      }

      // Dark glassy core, so the particles read against it.
      ctx.globalCompositeOperation = 'source-over';
      ctx.shadowBlur = 0;
      const core = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 0.92);
      core.addColorStop(0, 'rgba(6,24,38,0.92)');
      core.addColorStop(0.8, 'rgba(6,24,38,0.75)');
      core.addColorStop(1, 'rgba(6,24,38,0)');
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 0.92, 0, Math.PI * 2);
      ctx.fill();

      // The particle sphere: turns, swirls while thinking, swells with the voice.
      ctx.globalCompositeOperation = 'lighter';
      const [near, far] = MODE_TINT[s.mode];
      const turn = t * (0.25 + MODE_SPIN[s.mode] * 0.5);
      const tilt = 0.38 + Math.sin(t * 0.3) * 0.08;
      const swirl = s.mode === 'thinking' || s.mode === 'working' ? 1.4 : 0.25;
      const radius = R * (0.7 + e * 0.12 + s.voice * 0.18 + s.bass * 0.12);
      for (const point of SPHERE) {
        const twist = turn + point.y * swirl * Math.sin(t * 0.7);
        const x1 = point.x * Math.cos(twist) - point.z * Math.sin(twist);
        const z1 = point.x * Math.sin(twist) + point.z * Math.cos(twist);
        const y2 = point.y * Math.cos(tilt) - z1 * Math.sin(tilt);
        const z2 = point.y * Math.sin(tilt) + z1 * Math.cos(tilt);
        const pulse =
          1 + Math.sin(t * 3 + point.seed) * (0.015 + s.voice * 0.08);
        const depth = (z2 + 1) / 2; // 0 far .. 1 near
        const perspective = 1 / (1.25 - z2 * 0.25);
        const px = cx + x1 * radius * pulse * perspective;
        const py = cy + y2 * radius * pulse * perspective;
        const size = 0.6 + depth * 1.7 + s.voice * 1.2;
        ctx.fillStyle = `rgba(${depth > 0.5 ? near : far},${0.18 + depth * 0.72})`;
        ctx.beginPath();
        ctx.arc(px, py, size, 0, Math.PI * 2);
        ctx.fill();
      }
      const sheen = ctx.createRadialGradient(
        cx - R * 0.35,
        cy - R * 0.4,
        2,
        cx,
        cy,
        R,
      );
      sheen.addColorStop(0, `rgba(${near},${0.1 + e * 0.12})`);
      sheen.addColorStop(1, `rgba(${near},0)`);
      ctx.fillStyle = sheen;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 0.9, 0, Math.PI * 2);
      ctx.fill();

      // The visualiser bars between the rings and the HUD.
      ctx.lineCap = 'round';
      for (let i = 0; i < BARS; i++) {
        const value = s.bars[i];
        if (value < 0.04) continue;
        const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
        const inner = R * 1.1;
        const outer = inner + R * 0.34 * value;
        ctx.strokeStyle = `rgba(${i % 2 ? far : near},${0.35 + value * 0.6})`;
        ctx.lineWidth = Math.max(1.5, R * 0.035);
        ctx.shadowBlur = 10;
        ctx.shadowColor = `rgba(${near},0.8)`;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
        ctx.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
        ctx.stroke();
      }
      ctx.shadowBlur = 0;

      // HUD rings: ticks, dashes and arc segments turning at their own speeds.
      ctx.lineCap = 'round';
      const ticks = R * 1.5;
      for (let i = 0; i < 90; i++) {
        const a = (i / 90) * Math.PI * 2 - s.spin * 0.25;
        const long = i % 6 === 0;
        const inner = ticks - (long ? 9 : 4);
        ctx.strokeStyle = `rgba(${near},${long ? 0.5 : 0.22})`;
        ctx.lineWidth = long ? 1.6 : 1;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
        ctx.lineTo(cx + Math.cos(a) * ticks, cy + Math.sin(a) * ticks);
        ctx.stroke();
      }
      ctx.setLineDash([3, 9]);
      ctx.strokeStyle = `rgba(${far},0.4)`;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.36, s.spin * 0.6, s.spin * 0.6 + Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      for (let k = 0; k < 3; k++) {
        const start = s.spin * (1.2 + k * 0.3) * (k % 2 ? -1 : 1) + k * 2.1;
        ctx.strokeStyle = `rgba(${k === 1 ? far : near},${0.55 + e * 0.3})`;
        ctx.lineWidth = 2.6;
        ctx.shadowBlur = 14;
        ctx.shadowColor = `rgba(${near},0.9)`;
        ctx.beginPath();
        ctx.arc(cx, cy, R * (1.22 + k * 0.035), start, start + 0.5 + e * 0.6);
        ctx.stroke();
      }
      ctx.shadowBlur = 0;

      // Busy helper Dots orbit the orb.
      s.helpers.forEach((name, index) => {
        const color = HELPER_COLORS[name] ?? '#8b7bff';
        const angle = t * 0.9 + (index * Math.PI * 2) / s.helpers.length;
        const ox = cx + Math.cos(angle) * R * 1.85;
        const oy = cy + Math.sin(angle) * R * 0.62;
        for (let trail = 1; trail <= 14; trail++) {
          const back = angle - trail * 0.05;
          ctx.fillStyle = `${color}${Math.round(40 - trail * 2.5)
            .toString(16)
            .padStart(2, '0')}`;
          ctx.beginPath();
          ctx.arc(
            cx + Math.cos(back) * R * 1.85,
            cy + Math.sin(back) * R * 0.62,
            6 - trail * 0.3,
            0,
            Math.PI * 2,
          );
          ctx.fill();
        }
        const glow = ctx.createRadialGradient(ox, oy, 0, ox, oy, 22);
        glow.addColorStop(0, color);
        glow.addColorStop(0.35, `${color}99`);
        glow.addColorStop(1, `${color}00`);
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(ox, oy, 22, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
        ctx.fillStyle = 'rgba(220,225,255,0.85)';
        ctx.font = '600 12px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(name, ox, oy + 36);
        ctx.globalCompositeOperation = 'lighter';
      });

      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
    };
  }, [level, spectrum]);

  return <canvas ref={canvas} className="friday-orb-canvas" aria-hidden />;
}
