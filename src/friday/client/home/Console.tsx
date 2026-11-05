// Friday: the top of the main page. A hello and how to talk, then what is
// coming up and what Friday has done, from the real activity feed.
import { useCallback, useEffect, useState } from 'react';
import { followActivity, ownerHeaders } from '../stage/activity';
import type { ActivityEvent } from '../stage/narration';

async function api<T>(path: string, init: RequestInit = {}) {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...ownerHeaders() },
  });
  const data = (await response.json().catch(() => ({}))) as T & {
    detail?: string;
    error?: string;
  };
  if (!response.ok)
    throw new Error(
      data.detail ?? data.error ?? `Request failed (${response.status}).`,
    );
  return data as T;
}

// Actions that change what is coming up.
const REMINDER_ACTIONS = new Set([
  'set_timer',
  'add_reminder',
  'add_deadline',
  'finish_reminder',
  'reminder',
  'focus_session',
]);

const SAMPLES = [
  'Arijit Singh ka gaana chalao',
  'Aaj ka briefing do',
  'Ye kaun hai?',
  'Kal 5 baje DBMS project ka reminder laga do',
  'Research electric scooters in India and give me a report',
  'Open Notepad and write my shopping list',
  'Priya ko WhatsApp karo ki 10 minute late hoon',
  'Find my resume',
];

interface Reminder {
  id: string;
  kind: 'timer' | 'reminder' | 'deadline';
  text: string;
  due: number;
}

const ACTORS: Record<string, string> = {
  navigator: 'Navigator',
  operator: 'Operator',
};

const clock = (seconds: number) =>
  new Date(seconds * 1000).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });

/** "5:00 pm", "Tomorrow", or "12 Nov" for a reminder's list. */
export function dueLabel(seconds: number, now = new Date()) {
  const due = new Date(seconds * 1000);
  const days = Math.round(
    (new Date(due).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) /
      86_400_000,
  );
  if (days <= 0) return clock(seconds);
  if (days === 1) return 'Tomorrow';
  return due.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

/** What Friday did, in plain words: the feed ends finished steps in "Done." */
export const doneText = (detail: string) => detail.replace(/\s*Done\.$/, '');

export function FridayConsole({ name }: { name?: string }) {
  const [googleAccount, setGoogleAccount] = useState('');
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [online, setOnline] = useState(false);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [now, setNow] = useState(Date.now());

  const loadReminders = useCallback(async () => {
    try {
      setReminders(await api<Reminder[]>('/friday/reminders'));
    } catch {
      // services offline: the status line says so
    }
  }, []);

  useEffect(() => {
    void api<{ events: ActivityEvent[] }>('/friday/activity')
      .then((data) =>
        setEvents(data.events.filter((e) => e.actor !== 'control')),
      )
      .catch(() => {});
    void loadReminders();
    void api<{ signed_in: boolean; email?: string | null }>(
      '/friday/account/google',
    )
      .then((account) =>
        setGoogleAccount(account.signed_in ? account.email || 'signed in' : ''),
      )
      .catch(() => {});
    // Not a voice window: the console never takes Caps Lock from the notch.
    const stop = followActivity(
      (event) => {
        if (event.actor === 'control') return;
        setEvents((list) => [...list.slice(-80), event]);
        if (REMINDER_ACTIONS.has(event.action)) void loadReminders();
      },
      setOnline,
      false,
    );
    const ticker = setInterval(() => setNow(Date.now()), 5000);
    const refresh = setInterval(() => void loadReminders(), 20_000);
    return () => {
      stop();
      clearInterval(ticker);
      clearInterval(refresh);
    };
  }, [loadReminders]);

  const latest = events.at(-1);
  const working =
    latest?.state === 'running' && now - latest.at * 1000 < 60_000;
  const first = name?.trim().split(/\s+/)[0];
  const coming = [...reminders].sort((a, b) => a.due - b.due).slice(0, 5);
  const done = [...events]
    .reverse()
    .filter((event) => event.state !== 'running')
    .slice(0, 8);

  return (
    <div className="console">
      <section className="hello">
        <h1>{first ? `Namaste, ${first}.` : 'Namaste.'}</h1>
        <p className="hello-talk">
          Hold <kbd>Caps Lock</kbd> anywhere and talk. Hindi, English or both.
        </p>
        <p className="hello-status">
          <span className={working ? 'ok' : online ? 'ok' : 'bad'}>
            <i className={`dot ${working ? 'working' : ''}`} />
            {working
              ? doneText(latest.detail)
              : online
                ? 'Friday is ready'
                : 'Friday’s services are not running'}
          </span>
          <span>
            Google: <b>{googleAccount || 'not signed in'}</b>
          </span>
        </p>
      </section>

      <div className="today">
        <section>
          <h2 className="home-section">What Friday did</h2>
          {done.length === 0 ? (
            <p className="lines-empty">Nothing yet today.</p>
          ) : (
            <ul className="lines">
              {done.map((event) => (
                <li
                  key={event.id}
                  className={event.state === 'failed' ? 'failed' : ''}
                >
                  <time>{clock(event.at)}</time>
                  <span>
                    {doneText(event.detail)}
                    {ACTORS[event.actor] && (
                      <small>{ACTORS[event.actor]}</small>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <div className="today-side">
          <section>
            <h2 className="home-section">Coming up</h2>
            {coming.length === 0 ? (
              <p className="lines-empty">No reminders.</p>
            ) : (
              <ul className="lines">
                {coming.map((reminder) => (
                  <li key={reminder.id}>
                    <time>{dueLabel(reminder.due)}</time>
                    <span>{reminder.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section>
            <h2 className="home-section">Try saying</h2>
            <ul className="sayings">
              {SAMPLES.map((sample) => (
                <li key={sample}>“{sample}”</li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}
