// Friday: the live task toast (#/toast), a small see-through card the desktop
// shell shows at the bottom left, above the taskbar. While Friday or a helper
// works it shows only the current step ("Mail: Reading the email."), for a few
// seconds, then fades; there is no history. It never takes the mouse.
import { useEffect, useRef, useState } from 'react';
import { followActivity } from './activity';
import { ACTOR_NAMES, type ActivityEvent } from './narration';
import './toast.css';

export const TOAST_HASH = '#/toast';
const SHOW_MS = 4000;
// Events older than this when they arrive are history (a reconnect replays
// recent ones): not shown.
const FRESH_MS = 8000;

const at = (event: ActivityEvent) =>
  event.at > 1e12 ? event.at : event.at * 1000; // seconds or milliseconds

export function ActivityToast() {
  const [step, setStep] = useState<{
    who: string;
    what: string;
    failed: boolean;
  } | null>(null);
  const hide = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    document.documentElement.classList.add('friday-toast-page');
    const stop = followActivity(
      (event) => {
        if (event.actor === 'control' || Date.now() - at(event) > FRESH_MS)
          return;
        const what = event.detail.replace(/\s+/gu, ' ').trim();
        if (!what) return;
        setStep({
          who: ACTOR_NAMES[event.actor] ?? event.actor,
          what: what.length > 90 ? `${what.slice(0, 89)}…` : what,
          failed: event.state === 'failed',
        });
        console.info(`[toast] showing: ${what.slice(0, 60)}`);
        clearTimeout(hide.current);
        hide.current = setTimeout(() => setStep(null), SHOW_MS);
      },
      // The desktop shell logs these lines (friday/desktop/main.cjs).
      (connected) =>
        console.info(`[toast] feed ${connected ? 'connected' : 'lost'}`),
      false,
    );
    return () => {
      stop();
      clearTimeout(hide.current);
      document.documentElement.classList.remove('friday-toast-page');
    };
  }, []);
  return (
    <div className={`friday-toast ${step ? 'on' : ''}`} aria-live="polite">
      {step && (
        <div className={`friday-toast-card ${step.failed ? 'failed' : ''}`}>
          <span className="friday-toast-dot" aria-hidden />
          <span className="friday-toast-who">{step.who}</span>
          <span className="friday-toast-what">{step.what}</span>
        </div>
      )}
    </div>
  );
}
