// Friday: the console at the top of the main page, in the layout of the ARRC
// "J" console: how to talk and system status on the left, Friday's reactor
// and what it is doing in the middle, and the live feed, reminders and
// abilities on the right.
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlarmClock,
  AppWindow,
  CalendarDays,
  CloudSun,
  Code2,
  FolderOpen,
  Globe,
  Mail,
  MessageCircle,
  Monitor,
  Music,
  NotebookPen,
} from 'lucide-react';
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

// Which ability lights up for each action Friday or a helper runs.
export const ABILITY_OF: Record<string, string> = {
  open_website: 'web',
  web_search: 'web',
  browser_search: 'web',
  browser_open: 'web',
  browser_task: 'web',
  browser_click: 'web',
  browser_type: 'web',
  browser_read: 'web',
  play_youtube: 'music',
  play_shown_video: 'music',
  media_control: 'music',
  run_mode: 'apps',
  open_app: 'apps',
  write_in_app: 'apps',
  clear_in_app: 'apps',
  press_keys_in_app: 'apps',
  window_type: 'apps',
  window_click: 'apps',
  snap_window: 'apps',
  find_files: 'files',
  open_file: 'files',
  draft_email: 'email',
  send_email: 'email',
  schedule_meeting: 'calendar',
  whatsapp_send_message: 'whatsapp',
  whatsapp_call: 'whatsapp',
  whatsapp_find_chat: 'whatsapp',
  set_timer: 'reminders',
  add_reminder: 'reminders',
  add_deadline: 'reminders',
  finish_reminder: 'reminders',
  reminder: 'reminders',
  focus_session: 'reminders',
  take_note: 'notes',
  read_notes: 'notes',
  show_notes: 'notes',
  look_at_screen: 'screen',
  take_screenshot: 'screen',
  system_status: 'screen',
  lock_screen: 'screen',
  weather: 'weather',
  directions: 'weather',
  code_editor_read: 'coding',
  code_editor_write: 'coding',
};

const ABILITIES = [
  { id: 'web', label: 'Web & Google', Icon: Globe },
  { id: 'music', label: 'YouTube & music', Icon: Music },
  { id: 'apps', label: 'Apps & modes', Icon: AppWindow },
  { id: 'files', label: 'Files', Icon: FolderOpen },
  { id: 'email', label: 'Gmail', Icon: Mail },
  { id: 'calendar', label: 'Meetings', Icon: CalendarDays },
  { id: 'whatsapp', label: 'WhatsApp', Icon: MessageCircle },
  { id: 'reminders', label: 'Timers & deadlines', Icon: AlarmClock },
  { id: 'notes', label: 'Notes', Icon: NotebookPen },
  { id: 'screen', label: 'Screen & PC', Icon: Monitor },
  { id: 'weather', label: 'Weather & maps', Icon: CloudSun },
  { id: 'coding', label: 'Coding practice', Icon: Code2 },
] as const;

const SAMPLES = [
  'Play Arijit Singh on YouTube',
  'Remind me, DBMS project due tomorrow 5 pm',
  '25 minute focus session for my assignment',
  'Open Notepad and write my shopping list',
  'What’s the weather in Pune?',
  'Schedule a meeting with Ruchita tomorrow at 3',
  'Solve this LeetCode problem',
  'Find my resume',
];

interface Reminder {
  id: string;
  kind: 'timer' | 'reminder' | 'deadline';
  text: string;
  due: number;
}

const ACTORS: Record<string, string> = {
  friday: 'Friday',
  navigator: 'Navigator',
  operator: 'Operator',
};

const clock = (seconds: number) =>
  new Date(seconds * 1000).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });

function dueLabel(due: number) {
  const date = new Date(due * 1000);
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86_400_000);
  const time = date.toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });
  if (date.toDateString() === today.toDateString()) return `Today ${time}`;
  if (date.toDateString() === tomorrow.toDateString())
    return `Tomorrow ${time}`;
  return `${date.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} ${time}`;
}

function Reactor({
  state,
  word,
}: {
  state: 'idle' | 'fire' | 'deny';
  word: string;
}) {
  return (
    <div className={`reactor ${state}`}>
      <div className="reactor-gyro" aria-hidden>
        <i />
        <i />
        <i />
      </div>
      <div className="reactor-sweep" aria-hidden />
      <svg viewBox="0 0 200 200" aria-hidden>
        <g className="spin">
          <circle
            cx="100"
            cy="100"
            r="92"
            className="ring thin"
            strokeDasharray="2 6"
          />
        </g>
        <g className="spin r">
          <circle
            cx="100"
            cy="100"
            r="80"
            className="ring"
            strokeDasharray="60 18 8 18"
          />
        </g>
        <g className="spin f">
          <circle
            cx="100"
            cy="100"
            r="68"
            className="ring bold"
            strokeDasharray="22 140"
          />
        </g>
        <g className="spin r">
          <circle
            cx="100"
            cy="100"
            r="58"
            className="ring thin"
            strokeDasharray="1 4"
          />
        </g>
        {Array.from({ length: 12 }, (_, i) => (
          <line
            key={i}
            x1="100"
            y1="6"
            x2="100"
            y2="14"
            className="tick"
            transform={`rotate(${i * 30} 100 100)`}
          />
        ))}
      </svg>
      <div className="center">
        <div className="state">
          {state === 'fire'
            ? 'WORKING'
            : state === 'deny'
              ? 'PROBLEM'
              : 'READY'}
        </div>
        <div className="big">{word}</div>
      </div>
    </div>
  );
}

export function FridayConsole() {
  const [googleAccount, setGoogleAccount] = useState('');
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [online, setOnline] = useState(false);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [draft, setDraft] = useState({ text: '', when: '', kind: 'deadline' });
  const [note, setNote] = useState('');
  const [now, setNow] = useState(Date.now());

  const loadReminders = useCallback(async () => {
    try {
      setReminders(await api<Reminder[]>('/friday/reminders'));
    } catch {
      // services offline: the System panel says so
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
        if (ABILITY_OF[event.action] === 'reminders') void loadReminders();
      },
      setOnline,
      false,
    );
    const ticker = setInterval(() => setNow(Date.now()), 1000);
    const refresh = setInterval(() => void loadReminders(), 20_000);
    return () => {
      stop();
      clearInterval(ticker);
      clearInterval(refresh);
    };
  }, [loadReminders]);

  const latest = events.at(-1);
  const recentHot = useMemo(
    () =>
      new Set(
        events
          .filter((event) => now - event.at * 1000 < 6000)
          .map((event) => ABILITY_OF[event.action])
          .filter(Boolean),
      ),
    [events, now],
  );
  const reactorState =
    latest && now - latest.at * 1000 < 8000
      ? latest.state === 'failed'
        ? 'deny'
        : latest.state === 'running'
          ? 'fire'
          : 'idle'
      : 'idle';
  const today = new Date().toDateString();
  const actionsToday = events.filter(
    (event) =>
      event.state === 'done' &&
      new Date(event.at * 1000).toDateString() === today,
  ).length;
  const word = latest
    ? latest.action.replace(/_/g, ' ').replace(/^browser /, '') || 'Friday'
    : 'Friday';

  const addReminder = async () => {
    setNote('');
    try {
      await api('/friday/reminders', {
        method: 'POST',
        body: JSON.stringify({
          kind: draft.kind,
          text: draft.text,
          when: draft.when,
        }),
      });
      setDraft({ text: '', when: '', kind: draft.kind });
      await loadReminders();
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not save it.');
    }
  };
  const finish = async (id: string) => {
    await api(`/friday/reminders/${encodeURIComponent(id)}/done`, {
      method: 'POST',
      body: '{}',
    }).catch(() => {});
    await loadReminders();
  };

  // FRIDAY: one clear order: who Friday is and how to talk to it, what it is
  // doing, then everything it can do.
  return (
    <div className="console">
      <section className="console-hero">
        <div className="hero-copy">
          <h1 data-text="FRIDAY">
            F<span>R</span>IDAY
          </h1>
          <div className="tag">Your AI voice assistant, built on Gnani</div>
          <div className="panel talk">
            <div className="label">Talk to Friday</div>
            Hold
            <b>
              <kbd>Caps Lock</kbd>
            </b>
            <div className="hint">
              anywhere and speak; let go when you are done. English, Hindi or
              any mix. Gnani hears you, Friday and its Dots do the work, Gnani
              speaks. Ctrl + Caps Lock hides the notch.
            </div>
          </div>
          <div className="panel system">
            <div className="label">System</div>
            <div className="chips">
              <div className="chip">
                <span>Friday services</span>
                <i className={online ? 'ok' : 'bad'}>
                  <span className="dot" />
                  {online ? 'ONLINE' : 'OFFLINE'}
                </i>
              </div>
              <div className="chip">
                <span>Google account</span>
                <i className={googleAccount ? 'ok' : 'warn'}>
                  {googleAccount ? 'SIGNED IN' : 'NOT YET'}
                </i>
              </div>
              <div className="chip">
                <span>Actions today</span>
                <i>{actionsToday}</i>
              </div>
              <div className="chip">
                <span>Coming up</span>
                <i>{reminders.length}</i>
              </div>
            </div>
          </div>
        </div>
        <div className="console-core">
          <Reactor state={reactorState} word={word} />
          <div className="now">
            <div className="cmd">
              {latest ? (
                <>
                  <b>{ACTORS[latest.actor] ?? latest.actor}</b> {latest.detail}
                </>
              ) : (
                'Waiting for you. Hold Caps Lock to talk.'
              )}
            </div>
            <div className="res">{latest ? clock(latest.at) : ''}</div>
          </div>
        </div>
      </section>

      <h2 className="home-section">Activity</h2>
      <div className="console-row">
        <div className="panel live">
          <div className="label">Live</div>
          <div className="feed">
            {events.length === 0 && (
              <div className="empty">
                Nothing yet. What Friday does shows up here.
              </div>
            )}
            {[...events]
              .reverse()
              .filter((event) => event.state !== 'running')
              .slice(0, 12)
              .map((event) => (
                <div
                  key={event.id}
                  className={`item ${event.state === 'failed' ? 'refused' : ''}`}
                >
                  <div className="top">
                    <span className="act">
                      {event.action.replace(/_/g, ' ')}
                    </span>
                    <span>{clock(event.at)}</span>
                  </div>
                  <div className="in">{event.detail}</div>
                  <div className="out">
                    {ACTORS[event.actor] ?? event.actor}
                  </div>
                </div>
              ))}
          </div>
        </div>
        <div className="panel reminders">
          <div className="label">Reminders & deadlines</div>
          <div className="dls">
            {reminders.length === 0 && (
              <div className="empty">Nothing coming up.</div>
            )}
            {reminders.map((item) => {
              const left = item.due * 1000 - now;
              return (
                <div
                  key={item.id}
                  className={`dl ${left < 0 ? 'late' : left < 3 * 3_600_000 ? 'soon' : ''}`}
                >
                  <div>
                    <b>{item.text}</b>
                    <small>
                      {item.kind} · {dueLabel(item.due)}
                    </small>
                  </div>
                  <button type="button" onClick={() => void finish(item.id)}>
                    Done
                  </button>
                </div>
              );
            })}
          </div>
          <form
            className="add"
            onSubmit={(event) => {
              event.preventDefault();
              void addReminder();
            }}
          >
            <input
              placeholder="What, e.g. DBMS project"
              value={draft.text}
              onChange={(event) =>
                setDraft({ ...draft, text: event.target.value })
              }
            />
            <div className="add-row">
              <input
                type="datetime-local"
                aria-label="When"
                value={draft.when}
                onChange={(event) =>
                  setDraft({ ...draft, when: event.target.value })
                }
              />
              <select
                aria-label="Kind"
                value={draft.kind}
                onChange={(event) =>
                  setDraft({ ...draft, kind: event.target.value })
                }
              >
                <option value="deadline">Deadline</option>
                <option value="reminder">Reminder</option>
              </select>
            </div>
            <button type="submit" disabled={!draft.text.trim() || !draft.when}>
              Add
            </button>
            {note && <div className="watchmsg">{note}</div>}
          </form>
        </div>
        <div className="panel samples">
          <div className="label">Try saying</div>
          {SAMPLES.map((sample) => (
            <div key={sample}>
              <span>“{sample}”</span>
            </div>
          ))}
        </div>
      </div>

      <h2 className="home-section">Abilities</h2>
      <div className="panel abilities">
        <div className="label">Abilities</div>
        <div className="caps">
          {ABILITIES.map(({ id, label, Icon }) => (
            <div key={id} className={`cap ${recentHot.has(id) ? 'hot' : ''}`}>
              <Icon size={15} className="ic" />
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
