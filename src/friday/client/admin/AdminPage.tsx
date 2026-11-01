// Friday: the Admin page (#/admin). Whether every outside API is working,
// live: today's calls and failures for each one, the last error, a model key
// that ran out of quota and when it comes back, credits where the provider
// reports them, and live checks (keys, the public phone address). Grouped by
// kind: language models, speech to text, voice, phone calls, connections and
// tools. Refreshes every 5 seconds; live checks run once a minute.
import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react';
import { ownerHeaders } from '../stage/activity';
import { MemoryGraph } from './MemoryGraph';
import '../home/home.css';
import './admin.css';

export const ADMIN_HASH = '#/admin';

interface Row {
  from: 'app' | 'services';
  group: string;
  api: string;
  item: string;
  calls: number;
  ok: number;
  failed: number;
  statuses: Record<string, number>;
  avgMs: number | null;
  lastAt: number | null;
  lastOkAt: number | null;
  lastError: { at: number; status?: number; message: string } | null;
  restingUntil: number | null;
}

interface Status {
  generatedAt: number;
  day: string;
  rows: Row[];
  services: {
    reachable: boolean;
    error?: string;
    live?: {
      voice_windows: number;
      notches: number;
      ngrok_url: string | null;
      ngrok_configured: boolean;
      phone_token: boolean;
      sarvam_ready: boolean;
      inya_ready: boolean;
      twilio_on: boolean;
      owner_calls_via: string;
      owner_phone: boolean;
    };
  };
  checks: {
    checkedAt: number;
    gemini: { key: string; ok: boolean; status?: number; error?: string }[];
    openrouter?: {
      ok: boolean;
      freeTier?: boolean;
      usage?: number;
      usageToday?: number;
      limit?: number | null;
      remaining?: number | null;
      error?: string;
    };
    sarvam?: {
      ok: boolean;
      callsToday?: number;
      minutesToday?: number;
      inbound?: number;
      outbound?: number;
      notConnected?: number;
      lastNotConnected?: { to: string; at: string | null } | null;
      error?: string;
    };
    twilio?: {
      ok: boolean;
      balance?: string;
      currency?: string;
      error?: string;
    };
    phoneAddress?: {
      url: string;
      ok: boolean;
      status?: number;
      error?: string;
    };
  };
  configured: {
    mainModel?: string;
    mainProvider: string;
    extraModels: string[];
    backupModel: string | null;
    backupProvider: string | null;
    gnani: boolean;
    parallel: boolean;
    composio: boolean;
    intelligence: boolean;
  };
}

type Health = 'ok' | 'warn' | 'down' | 'idle' | 'off';
const HEALTH_LABEL: Record<Health, string> = {
  ok: 'Working',
  warn: 'Needs a look',
  down: 'Not working',
  idle: 'Not used today',
  off: 'Not set up',
};
const RANK: Record<Health, number> = {
  down: 0,
  warn: 1,
  ok: 2,
  idle: 3,
  off: 4,
};

interface Fact {
  label: string;
  value: string;
  tone?: Health;
}

interface Card {
  name: string;
  role: string;
  health: Health;
  facts: Fact[];
  rows: Row[];
  note?: string;
}

const time = (at: number | string | null | undefined) =>
  at
    ? new Date(at).toLocaleTimeString([], {
        hour: 'numeric',
        minute: '2-digit',
      })
    : '—';

/** A card's health from its calls: failing now, failing at times, or fine. */
/** The calling service's name as this page shows it. */
function callService(via?: string) {
  return !via || /sarvam/iu.test(via) ? 'Twilio' : via;
}

/** Messages from the calling service, with its name as this page shows it. */
const asShown = (text: string) => text.replace(/sarvam/giu, 'Twilio');

function healthOf(rows: Row[], now: number): Health {
  if (!rows.length) return 'idle';
  const resting = rows.some((row) => (row.restingUntil ?? 0) > now);
  const failingNow = rows.every(
    (row) =>
      row.lastError && (!row.lastOkAt || row.lastError.at > row.lastOkAt),
  );
  if (failingNow) return 'down';
  if (resting || rows.some((row) => row.failed)) return 'warn';
  return rows.some((row) => row.ok) ? 'ok' : 'idle';
}

const worst = (...states: Health[]) =>
  states.reduce((a, b) => (RANK[b] < RANK[a] ? b : a), 'off' as Health);

function totals(rows: Row[]): Fact[] {
  const calls = rows.reduce((n, row) => n + row.calls, 0);
  const failed = rows.reduce((n, row) => n + row.failed, 0);
  const timed = rows.filter((row) => row.avgMs !== null);
  const avg = timed.length
    ? Math.round(
        timed.reduce((n, row) => n + (row.avgMs ?? 0) * row.calls, 0) /
          Math.max(
            1,
            timed.reduce((n, row) => n + row.calls, 0),
          ),
      )
    : null;
  return [
    { label: 'Requests today', value: String(calls) },
    {
      label: 'Failed',
      value: String(failed),
      tone: failed ? 'warn' : undefined,
    },
    { label: 'Average time', value: avg === null ? '—' : `${avg} ms` },
  ];
}

function cardsFor(status: Status, now: number): Record<string, Card[]> {
  const rowsOf = (api: string) => status.rows.filter((row) => row.api === api);
  const live = status.services.live;
  const { checks, configured } = status;
  const sections: Record<string, Card[]> = {
    'Language models': [],
    'Speech to text': [],
    Voice: [],
    'Phone calls': [],
    Connections: [],
    Tools: [],
  };

  // Language models: the main provider with its keys and models, and the backup.
  const main = configured.mainProvider;
  const mainRows = rowsOf(main);
  const badKeys = checks.gemini.filter((key) => !key.ok);
  sections['Language models'].push({
    name: main,
    role: `Friday's brain: ${[configured.mainModel, ...configured.extraModels].filter(Boolean).join(', ')}`,
    health: worst(
      healthOf(mainRows, now),
      checks.gemini.length ? (badKeys.length ? 'down' : 'ok') : 'idle',
    ),
    facts: [
      ...totals(mainRows),
      ...(checks.gemini.length
        ? [
            {
              label: 'Keys working',
              value: `${checks.gemini.length - badKeys.length} of ${checks.gemini.length}`,
              tone: badKeys.length ? ('down' as Health) : undefined,
            },
          ]
        : []),
    ],
    rows: mainRows,
    note: badKeys.length
      ? `Not working: ${badKeys.map((key) => `${key.key} (${key.error ?? key.status})`).join(', ')}`
      : 'Each model and key has its own free daily allowance; one that runs out rests 10 minutes and Friday uses the next.',
  });
  if (configured.backupProvider) {
    const backupRows = rowsOf(configured.backupProvider);
    const credits = checks.openrouter;
    sections['Language models'].push({
      name: configured.backupProvider,
      role: `Backup when ${main} is out of quota: ${configured.backupModel}`,
      health: worst(
        backupRows.length ? healthOf(backupRows, now) : 'idle',
        credits ? (credits.ok ? 'ok' : 'down') : 'idle',
      ),
      facts: [
        ...totals(backupRows),
        ...(credits?.ok
          ? [
              {
                label: 'Plan',
                value: credits.freeTier ? 'Free tier' : 'Paid credits',
              },
              {
                label: 'Credits used',
                value: `$${(credits.usage ?? 0).toFixed(2)} (today $${(credits.usageToday ?? 0).toFixed(2)})`,
              },
              {
                label: 'Credits left',
                value:
                  credits.remaining === null || credits.remaining === undefined
                    ? 'No limit set'
                    : `$${credits.remaining.toFixed(2)}`,
              },
            ]
          : credits
            ? [
                {
                  label: 'Key check',
                  value: credits.error ?? 'Failed',
                  tone: 'down' as Health,
                },
              ]
            : []),
      ],
      rows: backupRows,
      note: 'Used only when every key and model above is resting.',
    });
  }

  // Gnani speech: hearing (Prisma) and speaking (Timbre).
  const prisma = rowsOf('Gnani Prisma');
  sections['Speech to text'].push({
    name: 'Gnani Prisma',
    role: 'Hears you: live voice while Caps Lock is held, and recorded clips',
    health: configured.gnani ? healthOf(prisma, now) : 'off',
    facts: totals(prisma),
    rows: prisma,
    note: "Gnani does not report credits; these are Friday's own counts.",
  });
  const timbre = rowsOf('Gnani Timbre');
  sections.Voice.push({
    name: 'Gnani Timbre',
    role: "Friday's voice on the PC",
    health: configured.gnani ? healthOf(timbre, now) : 'off',
    facts: totals(timbre),
    rows: timbre,
  });

  // Phone calls.
  const sarvam = rowsOf('Sarvam');
  const calls = checks.sarvam;
  // The calling service is shown as Twilio on this page (the owner's choice).
  sections['Phone calls'].push({
    name: 'Twilio',
    role: 'The phone number, its voice agent, and calls both ways',
    health: !live?.sarvam_ready
      ? 'off'
      : worst(
          healthOf(sarvam, now),
          calls
            ? calls.ok
              ? calls.notConnected
                ? 'warn'
                : 'ok'
              : 'down'
            : 'idle',
        ),
    facts: [
      ...totals(sarvam),
      ...(calls?.ok
        ? [
            {
              label: 'Calls today',
              value: `${calls.callsToday} (${calls.inbound} in, ${calls.outbound} out)`,
            },
            { label: 'Talk time today', value: `${calls.minutesToday} min` },
            {
              label: 'Did not connect',
              value: String(calls.notConnected ?? 0),
              tone: calls.notConnected ? ('warn' as Health) : undefined,
            },
          ]
        : calls
          ? [
              {
                label: 'Twilio check',
                value: asShown(calls.error ?? 'Failed'),
                tone: 'down' as Health,
              },
            ]
          : []),
    ],
    rows: sarvam,
    note: calls?.lastNotConnected
      ? `Last call that did not connect: ${calls.lastNotConnected.to}. Twilio took the call but the phone did not ring (off, no network or blocking unknown numbers).`
      : `Calls to you go through ${callService(live?.owner_calls_via)}.`,
  });
  const inya = rowsOf('Gnani Inya');
  sections['Phone calls'].push({
    name: 'Gnani Inya',
    role: 'Calls to you when Twilio is not set up',
    health: live?.inya_ready ? healthOf(inya, now) : 'off',
    facts: totals(inya),
    rows: inya,
  });
  const twilio = rowsOf('Twilio');
  // The older direct path; shown only while it is switched on.
  if (live?.twilio_on)
    sections['Phone calls'].push({
      name: 'Twilio (direct)',
      role: 'Older calling path',
      health: !live?.twilio_on
        ? 'off'
        : worst(
            healthOf(twilio, now),
            checks.twilio ? (checks.twilio.ok ? 'ok' : 'down') : 'idle',
          ),
      facts: [
        ...totals(twilio),
        ...(checks.twilio?.ok
          ? [
              {
                label: 'Balance',
                value: `${checks.twilio.balance} ${checks.twilio.currency}`,
              },
            ]
          : []),
      ],
      rows: twilio,
      note: live?.twilio_on ? undefined : 'Switched off (FRIDAY_TWILIO=off).',
    });

  // Connections.
  const phone = checks.phoneAddress;
  const ngrokRows = rowsOf('ngrok');
  sections.Connections.push({
    name: 'ngrok',
    role: "The public address Twilio's agent uses to reach Friday",
    health: !live?.ngrok_configured
      ? 'off'
      : phone
        ? phone.ok
          ? 'ok'
          : 'down'
        : live.ngrok_url
          ? 'ok'
          : 'down',
    facts: [
      { label: 'Address', value: live?.ngrok_url ?? 'Not open' },
      {
        label: 'Reachable from the internet',
        value: phone ? (phone.ok ? 'Yes' : (phone.error ?? 'No')) : '—',
        tone: phone && !phone.ok ? 'down' : undefined,
      },
    ],
    rows: ngrokRows,
  });
  sections.Connections.push({
    name: 'Friday services',
    role: 'Voice, calls, browser and desktop tools on this PC',
    health: status.services.reachable
      ? live && live.notches
        ? 'ok'
        : 'warn'
      : 'down',
    facts: status.services.reachable
      ? [
          {
            label: 'Notch connected',
            value: live?.notches ? 'Yes' : 'No (Caps Lock reaches no notch)',
            tone: live?.notches ? undefined : 'warn',
          },
          { label: 'Voice windows', value: String(live?.voice_windows ?? 0) },
          {
            label: 'Your number known',
            value: live?.owner_phone ? 'Yes' : 'No',
            tone: live?.owner_phone ? undefined : 'warn',
          },
        ]
      : [
          {
            label: 'Status',
            value: status.services.error ?? 'Down',
            tone: 'down',
          },
        ],
    rows: [],
  });
  sections.Connections.push({
    name: 'CopilotKit Intelligence',
    role: 'Conversations in the classic Dots view',
    health: configured.intelligence ? 'ok' : 'off',
    facts: [],
    rows: [],
    note: configured.intelligence ? 'Key set; not checked live.' : undefined,
  });
  sections.Connections.push({
    name: 'Composio',
    role: 'Connected apps for the Dots',
    health: configured.composio ? 'ok' : 'off',
    facts: [],
    rows: [],
  });

  // Tools.
  for (const [api, role] of [
    ['Open-Meteo', 'Weather'],
    ['YouTube', 'Finding a song to play'],
    ['Screen description', '"What\'s on my screen" (vision model)'],
  ] as const) {
    const rows = rowsOf(api);
    sections.Tools.push({
      name: api,
      role,
      health: healthOf(rows, now),
      facts: totals(rows),
      rows,
    });
  }
  sections.Tools.push({
    name: 'Parallel',
    role: 'Web research for the Dots',
    health: configured.parallel ? 'ok' : 'off',
    facts: [],
    rows: [],
  });
  // A service that is not set up and was not used has no numbers to show.
  for (const cards of Object.values(sections))
    for (const card of cards)
      if (card.health === 'off' && !card.rows.length) card.facts = [];
  return sections;
}

function CardView({ card, now }: { card: Card; now: number }) {
  const [open, setOpen] = useState(
    card.health === 'down' || card.health === 'warn',
  );
  const lastError = card.rows
    .map((row) => row.lastError)
    .filter(Boolean)
    .sort((a, b) => (b?.at ?? 0) - (a?.at ?? 0))[0];
  return (
    <article className={`admin-card health-${card.health}`}>
      <header>
        <span className="admin-light" aria-hidden />
        <div>
          <h3>{card.name}</h3>
          <p>{card.role}</p>
        </div>
        <span className="admin-state">{HEALTH_LABEL[card.health]}</span>
      </header>
      {card.facts.length > 0 && (
        <dl className="admin-facts">
          {card.facts.map((fact) => (
            <div
              key={fact.label}
              className={fact.tone ? `tone-${fact.tone}` : ''}
            >
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {lastError && (
        <p className="admin-error">
          <strong>Last error {time(lastError.at)}:</strong>{' '}
          {asShown(lastError.message)}
        </p>
      )}
      {card.note && <p className="admin-note">{asShown(card.note)}</p>}
      {card.rows.length > 0 && (
        <>
          <button
            type="button"
            className="admin-more"
            onClick={() => setOpen(!open)}
          >
            {open ? 'Hide details' : `Details (${card.rows.length})`}
          </button>
          {open && (
            <table className="admin-rows">
              <thead>
                <tr>
                  <th>What</th>
                  <th>Calls</th>
                  <th>Worked</th>
                  <th>Failed</th>
                  <th>Avg</th>
                  <th>Last used</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {card.rows
                  .slice()
                  .sort((a, b) => (b.lastAt ?? 0) - (a.lastAt ?? 0))
                  .map((row) => {
                    const resting = (row.restingUntil ?? 0) > now;
                    const codes = Object.entries(row.statuses)
                      .filter(([code]) => !code.startsWith('2'))
                      .map(([code, n]) => `${code}×${n}`)
                      .join(' ');
                    return (
                      <tr key={`${row.from}|${row.item}`}>
                        <td>{row.item || row.api}</td>
                        <td>{row.calls}</td>
                        <td>{row.ok}</td>
                        <td className={row.failed ? 'tone-warn' : ''}>
                          {row.failed}
                          {codes && <small> {codes}</small>}
                        </td>
                        <td>{row.avgMs === null ? '—' : `${row.avgMs} ms`}</td>
                        <td>{time(row.lastAt)}</td>
                        <td className={resting ? 'tone-warn' : ''}>
                          {resting
                            ? `Out of quota until ${time(row.restingUntil)}`
                            : 'Ready'}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          )}
        </>
      )}
    </article>
  );
}

export function AdminPage({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const load = useCallback(async (refresh = false) => {
    try {
      const response = await fetch(
        `/api/friday/admin/status${refresh ? '?refresh=1' : ''}`,
        { headers: ownerHeaders() },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setStatus((await response.json()) as Status);
      setError('');
    } catch (e) {
      setError(
        `Friday's app server did not answer (${e instanceof Error ? e.message : e}).`,
      );
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);
  const now = status?.generatedAt ?? Date.now();
  const sections = status ? cardsFor(status, now) : {};
  const all = Object.values(sections).flat();
  const count = (health: Health) =>
    all.filter((card) => card.health === health).length;
  return (
    <div className="friday-home friday-admin">
      <header className="home-top">
        <div className="home-brand">
          <span className="home-mark" aria-hidden>
            <i />
            <i />
            <i />
          </span>
          <strong>Friday</strong>
          <span className="admin-title">Admin · live status</span>
        </div>
        <nav>
          <button
            type="button"
            disabled={checking}
            onClick={async () => {
              setChecking(true);
              await load(true);
              setChecking(false);
            }}
          >
            {checking ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <RefreshCw size={16} />
            )}
            Check now
          </button>
          <button type="button" onClick={onBack}>
            <ArrowLeft size={16} />
            Main page
          </button>
        </nav>
      </header>
      <main className="admin-main">
        {error && <p className="admin-banner tone-down">{error}</p>}
        {!status && !error && (
          <p className="admin-banner">
            <Loader2 size={16} className="spin" /> Checking every service…
          </p>
        )}
        {status && (
          <>
            <section className="admin-summary panel">
              {(['down', 'warn', 'ok', 'idle', 'off'] as Health[]).map(
                (health) => (
                  <div key={health} className={`health-${health}`}>
                    <span className="admin-light" aria-hidden />
                    <strong>{count(health)}</strong>
                    <span>{HEALTH_LABEL[health]}</span>
                  </div>
                ),
              )}
              <p>
                Counts for {status.day}, updated {time(status.generatedAt)};
                live checks at {time(status.checks.checkedAt)}.
              </p>
            </section>
            <MemoryGraph />
            {Object.entries(sections).map(([name, cards]) => (
              <section key={name} className="admin-section">
                <h2>{name}</h2>
                <div className="admin-cards">
                  {cards
                    .slice()
                    .sort((a, b) => RANK[a.health] - RANK[b.health])
                    .map((card) => (
                      <CardView key={card.name} card={card} now={now} />
                    ))}
                </div>
              </section>
            ))}
          </>
        )}
      </main>
    </div>
  );
}
