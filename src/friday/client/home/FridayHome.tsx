// Friday: the main page. The owner's contacts, Friday's Google account and
// shortcuts into it, and what Friday is allowed to do. Talking happens on the
// voice screen or the desktop notch, by holding Caps Lock.
import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  CalendarDays,
  ChevronDown,
  HardDrive,
  LayoutGrid,
  Loader2,
  Mail,
  Mic,
  Phone,
  Play,
  ScanFace,
  ShieldCheck,
  X,
} from 'lucide-react';
import type { Connection } from '../../../shared/connection-types';
import type { WorkspaceState } from '../../../shared/types';
import { ownerHeaders } from '../stage/activity';
import {
  ABILITIES,
  setAbility,
  stateOf,
  toolsOf,
  type AbilityState,
} from './permissions';
import { FridayConsole } from './Console';
import './home.css';

export const HOME_HASH = '#/home';

async function api<T>(path: string, init: RequestInit = {}) {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...ownerHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  const data = (await response.json().catch(() => ({}))) as T & {
    error?: string;
    detail?: string;
  };
  if (!response.ok)
    throw new Error(
      data.detail ?? data.error ?? `Request failed (${response.status}).`,
    );
  return data as T;
}

type Profile = Partial<
  Record<
    'name' | 'email' | 'phone' | 'city' | 'address' | 'notes' | 'friday_google',
    string
  >
>;
interface Contact {
  name: string;
  phone: string;
  note?: string;
}

// How many contacts show before "Show more".
const CONTACTS_SHOWN = 2;

// Who "who is this" knows, and deleting them. Adding is by voice, with the
// person in front of the camera.
function PeopleCard() {
  const [people, setPeople] = useState<{ name: string }[]>([]);
  const [status, setStatus] = useState('');
  useEffect(() => {
    void api<{ name: string }[]>('/friday/people')
      .then(setPeople)
      .catch(() => setStatus('Friday’s services are not running.'));
  }, []);
  const forget = async (name: string) => {
    setStatus('');
    try {
      setPeople(
        await api<{ name: string }[]>(
          `/friday/people/${encodeURIComponent(name)}`,
          { method: 'DELETE' },
        ),
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not delete.');
    }
  };
  return (
    <section className="home-card">
      <header>
        <ScanFace size={16} />
        <h2>People Friday knows</h2>
      </header>
      <p className="home-muted">
        With a friend in front of the camera, and only if they agree, say “isko
        Rahul ke naam se save karo”. Then ask “ye kaun hai?”. Face data stays on
        this PC, and no photo is kept.
      </p>
      {people.length > 0 && (
        <ul className="home-contacts">
          {people.map(({ name }) => (
            <li key={name}>
              <strong>{name}</strong>
              <span />
              <button
                type="button"
                aria-label={`Delete ${name}`}
                onClick={() => void forget(name)}
              >
                <X size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {status && <p className="home-note">{status}</p>}
    </section>
  );
}

// The people Friday may phone for the owner: "call Rahul" uses Rahul's number.
function ContactsCard() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [draft, setDraft] = useState({ name: '', phone: '' });
  const [status, setStatus] = useState('');
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    void api<Contact[]>('/friday/contacts')
      .then(setContacts)
      .catch(() => setStatus('Could not load your contacts.'));
  }, []);
  const save = async (next: Contact[]) => {
    setStatus('');
    try {
      setContacts(
        await api<Contact[]>('/friday/contacts', {
          method: 'PUT',
          body: JSON.stringify(next),
        }),
      );
      return true;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not save.');
      return false;
    }
  };
  const add = async () => {
    const name = draft.name.trim();
    if (!name || !draft.phone.trim()) return;
    const others = contacts.filter(
      (contact) => contact.name.toLowerCase() !== name.toLowerCase(),
    );
    if (await save([...others, { name, phone: draft.phone.trim() }]))
      setDraft({ name: '', phone: '' });
  };
  const shown = showAll ? contacts : contacts.slice(0, CONTACTS_SHOWN);
  const hidden = Math.max(0, contacts.length - CONTACTS_SHOWN);
  return (
    <section className="home-card">
      <header>
        <Phone size={18} />
        <h2>Contacts</h2>
      </header>
      <p className="home-muted">
        People Friday can phone for you: say “call{' '}
        {contacts[0]?.name ?? 'Rahul'}
        ”. Kept on this PC.
      </p>
      {contacts.length > 0 && (
        <ul className="home-contacts">
          {shown.map((contact) => (
            <li key={contact.name}>
              <span>{contact.name}</span>
              <span className="home-muted">{contact.phone}</span>
              <button
                type="button"
                aria-label={`Remove ${contact.name}`}
                onClick={() =>
                  void save(contacts.filter((item) => item !== contact))
                }
              >
                <X size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {hidden > 0 && (
        <button
          type="button"
          className={`home-contacts-more ${showAll ? 'open' : ''}`}
          aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? 'Show less' : `Show ${hidden} more`}
          <ChevronDown size={16} />
        </button>
      )}
      <form
        className="home-form"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <label>
          <span>Name</span>
          <input
            value={draft.name}
            maxLength={60}
            onChange={(event) =>
              setDraft((current) => ({ ...current, name: event.target.value }))
            }
          />
        </label>
        <label>
          <span>Phone</span>
          <input
            type="tel"
            placeholder="98765 43210"
            value={draft.phone}
            maxLength={20}
            onChange={(event) =>
              setDraft((current) => ({ ...current, phone: event.target.value }))
            }
          />
        </label>
        <div className="home-actions wide">
          <span className="home-muted">{status}</span>
          <button
            type="submit"
            className="primary"
            disabled={!draft.name.trim() || !draft.phone.trim()}
          >
            Add
          </button>
        </div>
      </form>
    </section>
  );
}

interface ConnectedApp {
  id: string;
  name: string;
  does: string;
  toolkit: string;
  connected: boolean;
  active: boolean;
  added?: boolean;
}
interface AppsState {
  composio: boolean;
  error?: string;
  apps: ConnectedApp[];
  more: { name: string; toolkit: string; does: string }[];
  builtIn: { id: string; name: string; does: string }[];
}

// Google apps the Mail and Navigator Dots can use in Friday's Chrome.
const BROWSER_APPS = new Set(['gmail', 'googlecalendar', 'googledrive']);

/** Gmail, Calendar, Drive and any app the owner adds (signed in through
 * Composio), and what is built into Friday. */
function ConnectionsCard() {
  const [state, setState] = useState<AppsState>();
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [key, setKey] = useState('');
  const [other, setOther] = useState('');
  // Friday's own Chrome: where the Mail Dot works without Composio. Checked
  // only when asked, since checking starts that Chrome.
  const [browserGoogle, setBrowserGoogle] = useState('');
  const checkBrowserGoogle = () =>
    run('browser-check', async () => {
      try {
        const found = await api<{ signed_in: boolean; email?: string | null }>(
          '/friday/account/google',
        );
        setBrowserGoogle(
          found.signed_in
            ? `Signed in${found.email ? ` as ${found.email}` : ''}`
            : 'Not signed in yet',
        );
      } catch (error) {
        setBrowserGoogle(offlineHint(error));
      }
    });
  // Said next to the buttons, where the owner is looking.
  const offlineHint = (error: unknown) =>
    error instanceof Error && /not reachable/i.test(error.message)
      ? 'Friday’s services are not running. Double-click Start Friday, then try again.'
      : error instanceof Error
        ? error.message
        : 'That did not work.';
  const signInBrowserGoogle = () =>
    run('browser-sign-in', async () => {
      try {
        await api('/friday/account/google/connect', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        setBrowserGoogle(
          'Friday’s Chrome is open on Google’s sign-in page: type your password there yourself, then press Check.',
        );
      } catch (error) {
        setBrowserGoogle(offlineHint(error));
      }
    });
  const load = useCallback(async () => {
    try {
      setState(await api<AppsState>('/friday/apps'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not load.');
    }
  }, []);
  useEffect(() => {
    void load();
    // A sign-in finished in another tab shows up here by itself.
    const refresh = setInterval(() => void load(), 15_000);
    return () => clearInterval(refresh);
  }, [load]);
  const run = async (id: string, work: () => Promise<string | void>) => {
    setBusy(id);
    setStatus('');
    try {
      const said = await work();
      if (said) setStatus(said);
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'That did not work.');
    } finally {
      setBusy('');
    }
  };
  const connect = (app: ConnectedApp) =>
    run(app.id, async () => {
      const { url } = await api<{ url: string }>(
        `/friday/apps/${app.id}/connect`,
        { method: 'POST' },
      );
      // In the owner's own Chrome, where that Google account is signed in
      // already: just Allow. Another browser tab only if that fails.
      if (!(await openInMyChrome(url))) window.open(url, '_blank', 'noopener');
      return `Allow ${app.name} in the tab that opened in your Chrome; it shows Connected here after.`;
    });
  const saveKey = () =>
    run('key', async () => {
      await api('/friday/apps/key', {
        method: 'POST',
        body: JSON.stringify({ key }),
      });
      setKey('');
      return 'Key saved. Now click Connect next to an app.';
    });
  const add = (toolkit: string, name?: string) =>
    run(`add-${toolkit}`, async () => {
      await api('/friday/apps/add', {
        method: 'POST',
        body: JSON.stringify({ toolkit, name }),
      });
      setOther('');
    });
  const remove = (app: ConnectedApp) =>
    run(app.id, async () => {
      await api(`/friday/apps/${app.id}`, { method: 'DELETE' });
    });
  return (
    <section className="home-card">
      <header>
        <LayoutGrid size={18} />
        <h2>Connections</h2>
        <button type="button" className="home-link" onClick={() => void load()}>
          Refresh
        </button>
      </header>
      <p className="home-muted">
        Connect an app once and Friday works in it for you: “summarise my unread
        emails”, “reply to Ravi that I agree”, “what’s on my calendar tomorrow”.
        Anything that sends or changes something asks you first.
      </p>
      {state && !state.composio && (
        <div className="home-apps-browser">
          <p className="home-note">
            <strong>Gmail, Calendar and Drive work in Friday’s Chrome</strong>{' '}
            (the Mail helper reads, summarises and drafts there, in the
            background). Sign in to Gmail in that window once and it stays
            signed in.
          </p>
          <div className="home-row">
            <button
              type="button"
              className="primary"
              disabled={busy === 'browser-sign-in'}
              onClick={() => void signInBrowserGoogle()}
            >
              Sign in once
            </button>
            <button
              type="button"
              disabled={busy === 'browser-check'}
              onClick={() => void checkBrowserGoogle()}
            >
              {busy === 'browser-check' ? (
                <Loader2 size={14} className="spin" />
              ) : (
                'Check'
              )}
            </button>
            {browserGoogle && (
              <span className="home-muted">{browserGoogle}</span>
            )}
          </div>
        </div>
      )}
      {state && !state.composio && (
        <details className="home-apps-key">
          <summary>
            Optional: faster and fully in the background, through Composio
          </summary>
          <p className="home-note">
            <strong>One-time setup:</strong> apps sign in through Composio
            (free). Sign up at{' '}
            <a
              href="https://platform.composio.dev"
              target="_blank"
              rel="noreferrer"
            >
              platform.composio.dev
            </a>
            , copy your API key from Settings, and paste it here.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (key.trim()) void saveKey();
            }}
          >
            <input
              type="password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Composio API key"
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" disabled={!key.trim() || busy === 'key'}>
              {busy === 'key' ? (
                <Loader2 size={14} className="spin" />
              ) : (
                'Save key'
              )}
            </button>
          </form>
        </details>
      )}
      {state?.error && <p className="home-note error">{state.error}</p>}
      <ul className="home-apps">
        {(state?.apps ?? []).map((app) => (
          <li key={app.id}>
            <div>
              <strong>{app.name}</strong>
              <span className="home-muted">{app.does}</span>
            </div>
            {app.active ? (
              <span className="home-pill signed-in">Connected</span>
            ) : !state?.composio && BROWSER_APPS.has(app.toolkit) ? (
              <span className="home-pill">In Friday’s Chrome</span>
            ) : (
              <button
                type="button"
                disabled={!state?.composio || busy === app.id}
                title={
                  state?.composio ? undefined : 'Save your Composio key first'
                }
                onClick={() => void connect(app)}
              >
                {busy === app.id ? (
                  <Loader2 size={14} className="spin" />
                ) : app.connected ? (
                  'Turn on'
                ) : (
                  'Connect'
                )}
              </button>
            )}
            {app.added && !app.active && (
              <button
                type="button"
                className="home-link"
                aria-label={`Remove ${app.name}`}
                onClick={() => void remove(app)}
              >
                <X size={14} />
              </button>
            )}
          </li>
        ))}
        {(state?.builtIn ?? []).map((app) => (
          <li key={app.id}>
            <div>
              <strong>{app.name}</strong>
              <span className="home-muted">{app.does}</span>
            </div>
            <span className="home-pill">Built in</span>
          </li>
        ))}
      </ul>
      <div className="home-apps-more">
        <span className="home-muted">Add an app:</span>
        {(state?.more ?? []).map((item) => (
          <button
            key={item.toolkit}
            type="button"
            title={item.does}
            disabled={busy === `add-${item.toolkit}`}
            onClick={() => void add(item.toolkit, item.name)}
          >
            + {item.name}
          </button>
        ))}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (other.trim()) void add(other.trim());
          }}
        >
          <input
            value={other}
            onChange={(event) => setOther(event.target.value)}
            placeholder="Any other app (e.g. hubspot)"
            spellCheck={false}
          />
          <button type="submit" disabled={!other.trim()}>
            Add
          </button>
        </form>
      </div>
      {status && <p className="home-note">{status}</p>}
    </section>
  );
}

// Gmail, Calendar and Drive in the account Friday is signed in to, even if
// more Google accounts get signed in to Friday's Chrome later.
export function googleShortcuts(email?: string | null) {
  const as = email ? `authuser=${encodeURIComponent(email)}` : '';
  return [
    {
      label: 'Gmail',
      url: as
        ? `https://mail.google.com/mail/u/?${as}`
        : 'https://mail.google.com/',
    },
    {
      label: 'Calendar',
      url: as
        ? `https://calendar.google.com/calendar/r?${as}`
        : 'https://calendar.google.com/',
    },
    {
      label: 'Drive',
      url: as
        ? `https://drive.google.com/drive/my-drive?${as}`
        : 'https://drive.google.com/',
    },
    { label: 'YouTube', url: 'https://www.youtube.com/' },
  ];
}
const SHORTCUT_ICONS = {
  Gmail: Mail,
  Calendar: CalendarDays,
  Drive: HardDrive,
  YouTube: Play,
} as const;

interface ChromeAccounts {
  accounts: { folder: string; email: string; name: string }[];
  using: { folder: string; email: string } | null;
}

/** Opens a page in the owner's own signed-in Chrome; false if that failed. */
export async function openInMyChrome(url: string) {
  try {
    await api('/friday/browser/open-mine', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
    return true;
  } catch {
    return false;
  }
}

/** The Google account linked to Friday: one already signed in to the owner's
 * own Chrome, picked from a list. No second login and no separate browser:
 * Gmail, Calendar and Drive open in that Chrome profile. */
function GoogleCard({
  profile,
  onSaved,
}: {
  profile: Profile;
  onSaved: (profile: Profile) => void;
}) {
  const [chrome, setChrome] = useState<ChromeAccounts | null>(null);
  const [offline, setOffline] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const check = useCallback(async () => {
    try {
      setChrome(await api<ChromeAccounts>('/friday/account/chrome'));
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);
  useEffect(() => void check(), [check]);
  const using = chrome?.using?.email ?? null;
  const choose = async (email: string) => {
    setBusy(true);
    setNote('');
    try {
      onSaved(
        await api<Profile>('/friday/profile', {
          method: 'PUT',
          body: JSON.stringify({ ...profile, friday_google: email }),
        }),
      );
      await check();
      setNote(`Friday now uses ${email}, in your Chrome.`);
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not save.');
    } finally {
      setBusy(false);
    }
  };
  const open = async (url: string) => {
    setNote(
      (await openInMyChrome(url)) ? '' : 'Could not open it in your Chrome.',
    );
  };
  const label = offline
    ? 'Friday services are offline'
    : chrome === null
      ? 'Checking…'
      : using
        ? `Linked: ${using}`
        : 'Not linked';
  return (
    <section className="home-card">
      <header>
        <Mail size={18} />
        <h2>Google account</h2>
        <span
          className={`home-pill ${offline ? 'offline' : using ? 'signed-in' : 'signed-out'}`}
        >
          {label}
        </span>
      </header>
      <p className="home-muted">
        Friday uses a Google account that is already signed in to your Chrome,
        so there is nothing to log in to again. Sites, music, Gmail, Calendar
        and Drive open in that Chrome profile. Sending an email always asks you
        first.
      </p>
      {chrome && chrome.accounts.length > 0 && (
        <form
          className="home-account"
          onSubmit={(event) => {
            event.preventDefault();
            const email = new FormData(event.currentTarget).get('account');
            if (typeof email === 'string' && email) void choose(email);
          }}
        >
          <label>
            <span>Google account Friday uses</span>
            <select
              name="account"
              key={using ?? 'none'}
              defaultValue={using ?? chrome.accounts[0].email}
            >
              {chrome.accounts.map((item) => (
                <option key={item.folder} value={item.email}>
                  {item.email}
                  {item.name ? ` (${item.name})` : ''}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy}>
            {busy ? <Loader2 size={14} className="spin" /> : 'Use this account'}
          </button>
        </form>
      )}
      {chrome && chrome.accounts.length === 0 && (
        <p className="home-note">
          No Google account is signed in to Chrome yet. Sign in to your Gmail in
          Chrome once, then press Check again.
        </p>
      )}
      <div className="home-row">
        <button type="button" onClick={() => void check()}>
          Check again
        </button>
      </div>
      {note && <p className="home-note">{note}</p>}
      <div className="home-shortcuts">
        {googleShortcuts(using).map(({ label: name, url }) => {
          const Icon = SHORTCUT_ICONS[name as keyof typeof SHORTCUT_ICONS];
          return (
            <button
              key={name}
              type="button"
              disabled={offline}
              onClick={() => void open(url)}
            >
              <Icon size={16} />
              {name}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PermissionsCard({ dots }: { dots: { id: string; name: string }[] }) {
  // The workspace is re-sent often; reload only when the Dots themselves change.
  const dotKey = dots.map((dot) => `${dot.id}:${dot.name}`).join(',');
  const [byDot, setByDot] = useState<Record<string, Connection[]>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const entries = await Promise.all(
        dots
          .filter((dot) =>
            ['Friday', 'Navigator', 'Operator', 'Coder'].includes(dot.name),
          )
          .map(
            async (dot) =>
              [
                dot.name,
                await api<Connection[]>(`/dots/${dot.id}/connections`),
              ] as const,
          ),
      );
      setByDot(Object.fromEntries(entries));
      setError('');
    } catch {
      setError('Could not read Friday’s permissions.');
    }
  }, [dotKey]);
  useEffect(() => void load(), [load]);
  const toggle = async (id: string, on: boolean) => {
    const ability = ABILITIES.find((item) => item.id === id)!;
    setBusy(id);
    try {
      await setAbility(
        toolsOf(ability, byDot),
        on,
        async (connection, tool) => {
          await api(
            `/connections/${connection}/tools/${encodeURIComponent(tool)}`,
            { method: 'PATCH', body: JSON.stringify({ enabled: on }) },
          );
        },
      );
      await load();
    } catch {
      setError('Could not change that permission.');
    } finally {
      setBusy('');
    }
  };
  return (
    <section className="home-card">
      <header>
        <ShieldCheck size={18} />
        <h2>Permissions</h2>
      </header>
      <p className="home-muted">
        Switching something off stops every helper from doing it.
      </p>
      <ul className="home-permissions">
        {ABILITIES.map((ability) => {
          const state: AbilityState = stateOf(toolsOf(ability, byDot));
          const on = state === 'on' || state === 'mixed';
          return (
            <li key={ability.id}>
              <div>
                <strong>{ability.title}</strong>
                <span className="home-muted">
                  {ability.detail}
                  {ability.alwaysAsks && ' Always asks you first.'}
                  {state === 'unavailable' && ' Not set up yet.'}
                </span>
              </div>
              {busy === ability.id ? (
                <Loader2 size={18} className="home-spin" />
              ) : (
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-label={ability.title}
                  className={`home-switch ${on ? 'on' : ''}`}
                  disabled={state === 'unavailable'}
                  onClick={() => void toggle(ability.id, !on)}
                >
                  <i />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="home-note error">{error}</p>}
    </section>
  );
}

export function FridayHome({
  workspace,
  onVoice,
  onAdvanced,
}: {
  workspace?: WorkspaceState;
  onVoice: () => void;
  onAdvanced: () => void;
}) {
  // Your details, which the Google card reads and updates.
  const [profile, setProfile] = useState<Profile>({});
  useEffect(() => {
    void api<Profile>('/friday/profile')
      .then(setProfile)
      .catch(() => {});
  }, []);
  return (
    <div className="friday-home">
      <header className="home-top">
        <div className="home-brand">
          <span className="home-mark" aria-hidden>
            <i />
            <i />
            <i />
          </span>
          <strong>Friday</strong>
        </div>
        <nav>
          <button type="button" onClick={onVoice}>
            <Mic size={16} />
            Voice screen
          </button>
          <button type="button" onClick={onAdvanced}>
            <LayoutGrid size={16} />
            Dots
          </button>
          <button
            type="button"
            onClick={() => {
              location.hash = '#/admin';
            }}
          >
            <Activity size={16} />
            Admin
          </button>
        </nav>
      </header>
      <main className="home-main">
        <FridayConsole name={profile.name} />
        <section className="home-setup">
          <h2 className="home-section">Setup</h2>
          <div className="home-grid">
            <ContactsCard />
            <PeopleCard />
            <GoogleCard profile={profile} onSaved={setProfile} />
            <ConnectionsCard />
            <PermissionsCard dots={workspace?.dots ?? []} />
          </div>
        </section>
      </main>
    </div>
  );
}
