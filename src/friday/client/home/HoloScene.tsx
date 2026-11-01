// Friday: the main page's sci-fi backdrop. A grid floor runs off to a glowing
// horizon, a plexus of data motes drifts in depth, a scan beam sweeps down,
// and HUD brackets frame the screen; near motes drift a little with the
// pointer. Purely decorative: nothing here is read by assistive tech.
import { useEffect, useRef, useState } from 'react';
import './holo.css';

const MOTES = 72;
// How close two motes must be (in CSS pixels) to be joined by a line.
const LINK = 130;

export function HoloScene() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    const home = element?.closest<HTMLElement>('.friday-home');
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    // The pointer, as -1..1 from the centre, for the motes' parallax.
    const tilt = (event: PointerEvent) => {
      home?.style.setProperty(
        '--mx',
        ((event.clientX / innerWidth) * 2 - 1).toFixed(3),
      );
      home?.style.setProperty(
        '--my',
        ((event.clientY / innerHeight) * 2 - 1).toFixed(3),
      );
    };
    if (!still) addEventListener('pointermove', tilt);

    let w = 0;
    let h = 0;
    const resize = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = innerWidth;
      h = innerHeight;
      element.width = w * dpr;
      element.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    addEventListener('resize', resize);
    // Each mote has a depth z (0 far .. 1 near) that sets size, speed, glow.
    const motes = Array.from({ length: MOTES }, () => ({
      x: Math.random(),
      y: Math.random(),
      z: Math.random(),
      vx: (Math.random() - 0.5) * 0.00012,
      vy: -0.00004 - Math.random() * 0.00012,
    }));
    let frame = 0;
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      const points = motes.map((mote) => {
        if (!still) {
          mote.x += mote.vx * (0.4 + mote.z);
          mote.y += mote.vy * (0.4 + mote.z);
          if (mote.y < -0.05) mote.y = 1.05;
          if (mote.x < -0.05) mote.x = 1.05;
          if (mote.x > 1.05) mote.x = -0.05;
        }
        // A little parallax: near motes follow the pointer further.
        const mx = Number(home?.style.getPropertyValue('--mx') || 0);
        const my = Number(home?.style.getPropertyValue('--my') || 0);
        return {
          x: mote.x * w - mx * 18 * mote.z,
          y: mote.y * h - my * 12 * mote.z,
          z: mote.z,
        };
      });
      ctx.lineWidth = 0.6;
      for (let i = 0; i < points.length; i++)
        for (let j = i + 1; j < points.length; j++) {
          const a = points[i];
          const b = points[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d > LINK) continue;
          const alpha = (1 - d / LINK) * 0.22 * (0.4 + (a.z + b.z) / 2);
          ctx.strokeStyle = `rgba(88,201,243,${alpha.toFixed(3)})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      for (const point of points) {
        const r = 0.6 + point.z * 1.8;
        ctx.fillStyle = `rgba(189,229,255,${(0.25 + point.z * 0.6).toFixed(3)})`;
        ctx.shadowColor = 'rgba(88,201,243,0.9)';
        ctx.shadowBlur = 6 * point.z;
        ctx.beginPath();
        ctx.arc(point.x, point.y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;
      if (!still) frame = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(frame);
      removeEventListener('resize', resize);
      removeEventListener('pointermove', tilt);
    };
  }, []);
  return (
    <div className="home-holo" aria-hidden>
      <div className="holo-sky" />
      <div className="holo-floor" />
      <div className="holo-horizon" />
      <canvas ref={canvas} className="holo-motes" />
      <div className="holo-scan" />
      <div className="holo-frame">
        <i />
        <i />
        <i />
        <i />
      </div>
    </div>
  );
}

const pad = (value: number) => String(value).padStart(2, '0');

// The HUD strip under the header: the time, the date and how long this page
// has been open, in the console's monospace.
export function HoloHud() {
  const [now, setNow] = useState(() => new Date());
  const [opened] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(tick);
  }, []);
  const up = Math.floor((now.getTime() - opened) / 1000);
  return (
    <div className="home-hud" aria-hidden>
      <span className="hud-tag">
        <b /> FRIDAY // CORE INTERFACE
      </span>
      <span className="hud-wave">
        {Array.from({ length: 24 }, (_, i) => (
          <i key={i} style={{ animationDelay: `${(i % 8) * -0.15}s` }} />
        ))}
      </span>
      <span className="hud-readout">
        <span>
          T {pad(now.getHours())}:{pad(now.getMinutes())}:
          {pad(now.getSeconds())}
        </span>
        <span>
          {now
            .toLocaleDateString([], {
              day: '2-digit',
              month: 'short',
              year: 'numeric',
            })
            .toUpperCase()}
        </span>
        <span>
          SESSION {pad(Math.floor(up / 3600))}:{pad(Math.floor(up / 60) % 60)}:
          {pad(up % 60)}
        </span>
      </span>
    </div>
  );
}
