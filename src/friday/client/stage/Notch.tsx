// Friday: the desktop notch (friday/desktop shows this page in a small
// see-through window above the taskbar). A thin black pill that widens while
// you hold Caps Lock, with bars that follow your voice, and again while
// Friday speaks. Clicking it opens Friday's main page.
import { useCallback, useEffect, useRef, useState } from 'react';
import './notch.css';

export type NotchState =
  'idle' | 'quiet' | 'hearing' | 'thinking' | 'speaking' | 'asking' | 'offline';

interface Desktop {
  setInteractive: (on: boolean) => void;
  open: (hash: string) => void;
  toggle: () => void;
}
const desktop = () => (globalThis as { fridayDesktop?: Desktop }).fridayDesktop;

const BARS = 27;

// The owner talking: slim icy bars, mirrored from the middle, that jump up
// with each sound and settle slowly, with a faint ripple running through them
// in pauses so the notch never looks frozen while it listens. The pill's edge
// glows with the mic (--mic on the pill).
function Bars({
  spectrum,
  level,
}: {
  spectrum: () => Uint8Array | null;
  level: () => number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    element.width = element.clientWidth * ratio;
    element.height = element.clientHeight * ratio;
    ctx.scale(ratio, ratio);
    const pill = element.parentElement;
    const heights = new Float32Array(BARS);
    let mic = 0;
    let frame = 0;
    const draw = (now: number) => {
      const w = element.clientWidth;
      const h = element.clientHeight;
      const t = now / 1000;
      const bins = spectrum();
      const loud = Math.min(1, level() * 1.5);
      mic += (loud - mic) * 0.2;
      pill?.style.setProperty('--mic', mic.toFixed(3));
      ctx.clearRect(0, 0, w, h);
      const fill = ctx.createLinearGradient(0, 0, 0, h);
      fill.addColorStop(0, '#58c9f3');
      fill.addColorStop(0.5, '#bde5ff');
      fill.addColorStop(1, '#58c9f3');
      ctx.fillStyle = fill;
      ctx.shadowColor = '#58c9f3';
      ctx.shadowBlur = 4 + 6 * mic;
      const gap = w / BARS;
      for (let i = 0; i < BARS; i++) {
        // Mirror the voice band around the middle, like a waveform.
        const from = Math.abs(i - (BARS - 1) / 2) / (BARS / 2);
        const value = bins
          ? (bins[1 + Math.floor(from * bins.length * 0.45)] ?? 0) / 255
          : loud * (1 - from * 0.6);
        // A soft ripple from the middle outwards, so pauses still move.
        const ripple = 0.1 + 0.06 * Math.sin(t * 6 - from * 7);
        const target = Math.max(ripple, Math.min(1, Math.sqrt(value) * 1.15));
        // Jump up fast, fall back gently: lively, not twitchy.
        heights[i] +=
          (target - heights[i]) * (target > heights[i] ? 0.55 : 0.12);
        const bar = Math.max(2, heights[i] * (h - 2) * (1 - from * 0.35));
        const x = i * gap + gap * 0.32;
        ctx.globalAlpha = 0.55 + 0.45 * (1 - from);
        ctx.beginPath();
        ctx.roundRect(x, (h - bar) / 2, gap * 0.36, bar, gap * 0.18);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      pill?.style.removeProperty('--mic');
    };
  }, [spectrum, level]);
  return <canvas ref={canvas} className="notch-bars" aria-hidden />;
}

// Friday's own voice: soft glowing waves that swell with what it says and keep
// drifting in its pauses, unlike the owner's bars. The pill's glow follows the
// same loudness (--voice on the pill).
// Each line also vibrates up and down like a plucked string (shake, how
// fast in turns per second), all the time while Friday talks.
const WAVES = [
  { cycles: 1.6, speed: 2.1, height: 1, width: 2, alpha: 0.95, shake: 7 },
  { cycles: 2.3, speed: -2.9, height: 0.7, width: 1.5, alpha: 0.7, shake: 9.5 },
  { cycles: 3.1, speed: 3.7, height: 0.45, width: 1.2, alpha: 0.55, shake: 12 },
];

function Waves({ level }: { level: () => number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    element.width = element.clientWidth * ratio;
    element.height = element.clientHeight * ratio;
    ctx.scale(ratio, ratio);
    const pill = element.parentElement;
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let loud = 0;
    let frame = 0;
    const draw = (now: number) => {
      const w = element.clientWidth;
      const h = element.clientHeight;
      const t = (now / 1000) * (calm ? 0.35 : 1);
      // Smooth the loudness so the waves breathe instead of jitter.
      loud += (Math.min(1, level() * 1.4) - loud) * 0.18;
      pill?.style.setProperty('--voice', loud.toFixed(3));
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = 'lighter';
      const gradient = ctx.createLinearGradient(0, 0, w, 0);
      gradient.addColorStop(0, 'rgba(88, 201, 243, 0)');
      gradient.addColorStop(0.2, '#58c9f3');
      gradient.addColorStop(0.5, '#2fa0c6');
      gradient.addColorStop(0.8, '#e6d6ea');
      gradient.addColorStop(1, 'rgba(230, 214, 234, 0)');
      ctx.strokeStyle = gradient;
      ctx.shadowColor = '#2fa0c6';
      ctx.shadowBlur = 6;
      // Never flat: a little swell even between words.
      const swell = 0.3 + 0.7 * loud;
      for (const [index, wave] of WAVES.entries()) {
        // The string vibration: the whole line swings above and below the
        // middle, never resting, a little harder when the voice is loud.
        const vibrate =
          Math.cos(t * Math.PI * 2 * wave.shake + index * 1.7) *
          (0.55 + 0.25 * loud);
        ctx.globalAlpha = wave.alpha;
        ctx.lineWidth = wave.width;
        ctx.beginPath();
        for (let x = 0; x <= w; x += 1) {
          const along = x / w;
          // Highest in the middle, still at both ends.
          const taper = Math.pow(Math.sin(Math.PI * along), 2);
          // A fine fast ripple on top: the voice's buzz.
          const buzz =
            Math.sin(along * Math.PI * 18 + t * 32) * 0.12 * loud * loud;
          const y =
            h / 2 +
            (Math.sin(along * Math.PI * 2 * wave.cycles + t * wave.speed) *
              (0.45 + vibrate) +
              buzz) *
              taper *
              (h / 2 - 2) *
              wave.height *
              swell;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      pill?.style.removeProperty('--voice');
    };
  }, [level]);
  return <canvas ref={canvas} className="notch-waves" aria-hidden />;
}

export function Notch({
  state,
  level,
  spectrum,
  note,
  onDecide,
  linked = true,
}: {
  state: NotchState;
  /** False while the stream that brings Caps Lock is down; the desktop shell
   * reloads a notch that stays unlinked. */
  linked?: boolean;
  level: () => number;
  spectrum: () => Uint8Array | null;
  /** A short line above the pill: what needs an OK, or what went wrong. */
  note?: string;
  onDecide?: (approve: boolean) => void;
}) {
  useEffect(() => {
    document.documentElement.classList.add('friday-notch-page');
    return () => document.documentElement.classList.remove('friday-notch-page');
  }, []);
  // The notch window ignores the mouse entirely, so it never changes the
  // pointer or catches a click meant for the app under it; only an approval
  // on screen takes clicks, for its two buttons.
  const asking = state === 'asking' && !!onDecide;
  useEffect(() => {
    desktop()?.setInteractive(asking);
    return () => desktop()?.setInteractive(false);
  }, [asking]);
  return (
    <div className={`friday-notch ${state}${linked ? '' : ' unlinked'}`}>
      {note && (
        <div
          className={`notch-note${asking ? ' asking' : ''}`}
          role={state === 'asking' ? 'dialog' : 'status'}
        >
          {asking && <strong>Friday needs your OK</strong>}
          <span>{note}</span>
          {asking && (
            <div className="notch-actions">
              {/* Acts on the press: in this window that never takes focus,
                  a press arrives but a full click may not. */}
              <button
                type="button"
                onPointerDown={(e) => {
                  e.preventDefault();
                  onDecide(false);
                }}
              >
                Decline
              </button>
              <button
                type="button"
                className="approve"
                onPointerDown={(e) => {
                  e.preventDefault();
                  onDecide(true);
                }}
              >
                Approve
              </button>
            </div>
          )}
        </div>
      )}
      <button
        type="button"
        className="notch-pill"
        aria-label="Friday. Hold Caps Lock to talk; click to open Friday."
        tabIndex={-1}
        onClick={() => desktop()?.open('#/home')}
      >
        {state === 'hearing' && <Bars spectrum={spectrum} level={level} />}
        {state === 'speaking' && <Waves level={level} />}
        {(state === 'thinking' || state === 'asking') && (
          <span className="notch-dots" aria-hidden>
            <i />
            <i />
            <i />
          </span>
        )}
        {state === 'offline' && <span className="notch-warn" aria-hidden />}
      </button>
    </div>
  );
}

// Every notch state with a made-up voice, to look at the design without
// talking to Friday: #/notch-demo
export const NOTCH_DEMO_HASH = '#/notch-demo';
const DEMO_STATES: NotchState[] = ['idle', 'hearing', 'thinking', 'speaking'];

export function NotchDemo() {
  // #/notch-demo?speaking holds one state; otherwise every state in turn.
  const held = DEMO_STATES.indexOf(location.hash.split('?')[1] as NotchState);
  const [step, setStep] = useState(
    held >= 0 ? held : DEMO_STATES.indexOf('speaking'),
  );
  useEffect(() => {
    if (held >= 0) return;
    const timer = setInterval(
      () => setStep((n) => (n + 1) % DEMO_STATES.length),
      4000,
    );
    return () => clearInterval(timer);
  }, [held]);
  // Speech-like loudness: syllables inside words inside phrases.
  const level = useCallback(() => {
    const t = performance.now() / 1000;
    const phrase = 0.15 + 0.85 * Math.max(0, Math.sin(t * 0.9));
    return (
      phrase * (0.45 + 0.4 * Math.abs(Math.sin(t * 7.5) * Math.sin(t * 2.1)))
    );
  }, []);
  const spectrum = useCallback(() => {
    const t = performance.now() / 1000;
    return Uint8Array.from({ length: 64 }, (_, i) =>
      Math.max(
        0,
        220 * Math.exp(-i / 14) * (0.55 + 0.45 * Math.sin(t * 9 + i * 0.7)),
      ),
    );
  }, []);
  // #/notch-demo?asking: the approval card for a WhatsApp message.
  if (location.hash.split('?')[1] === 'asking')
    return (
      <div className="notch-demo">
        <Notch
          state="asking"
          level={level}
          spectrum={spectrum}
          note="Send “Running 10 minutes late, see you soon” to Priya on WhatsApp?"
          onDecide={() => {}}
        />
      </div>
    );
  return (
    <div className="notch-demo">
      <Notch
        state={DEMO_STATES[step]}
        level={level}
        spectrum={spectrum}
        note={`Demo: ${DEMO_STATES[step]}`}
      />
    </div>
  );
}
