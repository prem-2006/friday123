// Friday: the main page's Connections card. Gmail, Calendar, Drive and any
// other app the owner adds connect through Composio (the owner signs in on the
// app's own page; Friday never sees the password) and then become tools of the
// Friday Dot: read, summarise, reply, send, add events. Anything that sends or
// changes something asks the owner first, as every non-read-only connection
// tool does. WhatsApp, Chrome and phone calls are built into Friday and are
// listed for completeness. The Composio key can be pasted into the card: it is
// saved to .env and used at once.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { Composio } from '@composio/core';
import { Hono } from 'hono';
import { z } from 'zod';
import type { Platform } from '../../server/platform.js';
import { FRIDAY_DOT_NAME } from './delegate.js';

export interface AppEntry {
  id: string;
  name: string;
  toolkit: string;
  does: string;
  /** Added by the owner (it can be removed again). */
  added?: boolean;
}

export const CONNECTED_APPS: AppEntry[] = [
  {
    id: 'gmail',
    name: 'Gmail',
    toolkit: 'gmail',
    does: 'Read, summarise, reply to and send email (sending asks you first)',
  },
  {
    id: 'calendar',
    name: 'Google Calendar',
    toolkit: 'googlecalendar',
    does: 'Your day at a glance; add, move and cancel events',
  },
  {
    id: 'drive',
    name: 'Google Drive',
    toolkit: 'googledrive',
    does: 'Find, read and share your files',
  },
];

/** Apps the owner can add with one click (Composio toolkit slugs). */
export const MORE_APPS: Omit<AppEntry, 'id'>[] = [
  { name: 'Outlook', toolkit: 'outlook', does: 'Outlook mail and calendar' },
  {
    name: 'Google Sheets',
    toolkit: 'googlesheets',
    does: 'Read and update sheets',
  },
  {
    name: 'Google Docs',
    toolkit: 'googledocs',
    does: 'Read and write documents',
  },
  { name: 'Slack', toolkit: 'slack', does: 'Read and send Slack messages' },
  { name: 'Notion', toolkit: 'notion', does: 'Search and write Notion pages' },
  {
    name: 'GitHub',
    toolkit: 'github',
    does: 'Issues, pull requests and repos',
  },
  { name: 'LinkedIn', toolkit: 'linkedin', does: 'Your profile and posts' },
  { name: 'YouTube', toolkit: 'youtube', does: 'Your channel and playlists' },
  { name: 'Discord', toolkit: 'discord', does: 'Your servers and messages' },
  { name: 'Todoist', toolkit: 'todoist', does: 'Tasks and projects' },
  { name: 'Trello', toolkit: 'trello', does: 'Boards and cards' },
  { name: 'Zoom', toolkit: 'zoom', does: 'Meetings and recordings' },
];

export const BUILT_IN = [
  {
    id: 'whatsapp',
    name: 'WhatsApp',
    does: 'Messages and calls through WhatsApp Desktop, after your yes',
  },
  {
    id: 'chrome',
    name: 'Your Chrome',
    does: 'Sites, searches and music open in your signed-in Chrome profile',
  },
  {
    id: 'phone',
    name: 'Phone calls',
    does: 'Friday calls you for reminders, and answers your number',
  },
] as const;

type AppsPlatform = Pick<Platform, 'workspace' | 'store' | 'config'>;

export interface UnreadMail {
  count: number;
  mails: {
    id: string;
    sender: string;
    email: string;
    subject: string;
    preview: string;
    when: string;
    text: string;
  }[];
}

const MAX_TEXT = 6000;

/** "Ravi Kumar <ravi@x.com>" -> ["Ravi Kumar", "ravi@x.com"] */
export function splitSender(from: string): [string, string] {
  const match = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(from);
  if (match) return [match[1]!.trim(), match[2]!.trim()];
  return from.includes('@') ? ['', from.trim()] : [from.trim(), ''];
}

/** Composio's GMAIL_FETCH_EMAILS data -> the mail Friday keeps track of. */
export function unreadFromComposio(data: unknown): UnreadMail {
  const body = (data ?? {}) as {
    messages?: Record<string, unknown>[];
    resultSizeEstimate?: number;
  };
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  const mails = (body.messages ?? []).map((message) => {
    const [sender, email] = splitSender(text(message.sender));
    const preview = (message.preview ?? {}) as Record<string, unknown>;
    return {
      id: text(message.messageId),
      sender,
      email,
      subject: text(message.subject).trim(),
      preview: text(preview.body).trim(),
      when: text(message.messageTimestamp),
      text: text(message.messageText).trim().slice(0, MAX_TEXT),
    };
  });
  mails.sort((a, b) => (a.when < b.when ? 1 : a.when > b.when ? -1 : 0));
  return {
    count: Math.max(body.resultSizeEstimate ?? 0, mails.length),
    mails,
  };
}

export interface AppsDeps {
  /** Composio's connected accounts and sign-in links (replaced in tests). */
  connected: (apiKey: string, userId: string) => Promise<string[]>;
  signIn: (apiKey: string, userId: string, toolkit: string) => Promise<string>;
  /** The owner's unread inbox mail, newest first, read through Composio. */
  unreadMail: (apiKey: string, userId: string) => Promise<UnreadMail>;
  /** The top of the owner's inbox as Gmail shows it (Primary, read or unread). */
  latestMail: (apiKey: string, userId: string) => Promise<UnreadMail>;
  /** Where the key is kept (.env) and the owner's added apps. */
  envPath?: string;
  addedPath?: string;
}

const composio = (apiKey: string) => new Composio({ apiKey });
async function fetchMail(apiKey: string, userId: string, query: string) {
  const result = await composio(apiKey).tools.execute('GMAIL_FETCH_EMAILS', {
    userId,
    arguments: { query, max_results: 10, verbose: true },
    dangerouslySkipVersionCheck: true,
  });
  if (!result.successful)
    throw new Error(result.error ?? 'Gmail did not answer through Composio.');
  return unreadFromComposio(result.data);
}
export const composioApps: AppsDeps = {
  async connected(apiKey, userId) {
    const accounts = await composio(apiKey).connectedAccounts.list({
      userIds: [userId],
    });
    return accounts.items
      .filter((account) => account.status === 'ACTIVE')
      .map((account) => account.toolkit.slug.toLowerCase());
  },
  async signIn(apiKey, userId, toolkit) {
    // Composio's own Google sign-in for the app (made once), then a Connect
    // Link: toolkits.authorize uses an endpoint Composio has switched off.
    const client = composio(apiKey);
    let config = (await client.authConfigs.list({ toolkit })).items[0]?.id;
    config ??= (
      await client.authConfigs.create(toolkit, {
        type: 'use_composio_managed_auth',
        name: `${toolkit} for Friday`,
      })
    ).id;
    const request = await client.connectedAccounts.link(userId, config, {
      allowMultiple: true,
    });
    if (!request.redirectUrl) throw new Error('Composio gave no sign-in link.');
    return request.redirectUrl;
  },
  unreadMail: (apiKey, userId) =>
    fetchMail(apiKey, userId, 'is:unread in:inbox'),
  latestMail: (apiKey, userId) =>
    fetchMail(apiKey, userId, 'in:inbox category:primary'),
};

/** Sets one KEY=value line in a .env file, keeping everything else. */
export function setEnvLine(path: string, key: string, value: string) {
  const text = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const line = `${key}=${value}`;
  const pattern = new RegExp(`^${key}=.*$`, 'm');
  const next = pattern.test(text)
    ? text.replace(pattern, line)
    : `${text}${text && !text.endsWith('\n') ? '\n' : ''}${line}\n`;
  writeFileSync(path, next, 'utf8');
}

const slug = z
  .string()
  .trim()
  .toLowerCase()
  .regex(/^[a-z0-9_]{2,40}$/u, 'Use the app’s Composio name, e.g. slack.');

export function fridayAppsRoutes(
  platform: AppsPlatform,
  deps: AppsDeps = composioApps,
) {
  const app = new Hono();
  const envPath = deps.envPath ?? '.env';
  const addedPath = deps.addedPath ?? 'data/friday-apps.json';
  const friday = () =>
    platform.workspace.dots().find((dot) => dot.name === FRIDAY_DOT_NAME);
  const added = (): AppEntry[] => {
    try {
      const list = JSON.parse(readFileSync(addedPath, 'utf8')) as AppEntry[];
      return Array.isArray(list)
        ? list.map((a) => ({ ...a, added: true }))
        : [];
    } catch {
      return [];
    }
  };
  const saveAdded = (list: AppEntry[]) => {
    mkdirSync(dirname(addedPath), { recursive: true });
    writeFileSync(
      addedPath,
      JSON.stringify(
        list.map(({ added: _added, ...entry }) => entry),
        null,
        1,
      ),
      'utf8',
    );
  };
  const allApps = () => {
    const list = [...CONNECTED_APPS];
    for (const extra of added())
      if (!list.some((item) => item.toolkit === extra.toolkit))
        list.push(extra);
    return list;
  };

  app.get('/friday/apps', async (c) => {
    const apiKey = platform.config.composioApiKey;
    const dot = friday();
    const allowed =
      !!platform.store.settings().appsAllowed && !!dot?.appsAllowed;
    let connected: string[] = [];
    let error: string | undefined;
    if (apiKey) {
      try {
        connected = await deps.connected(apiKey, platform.workspace.ownerId);
      } catch (e) {
        error = e instanceof Error ? e.message : String(e);
      }
    }
    const apps = allApps();
    return c.json({
      composio: !!apiKey,
      error,
      apps: apps.map((item) => ({
        ...item,
        connected: connected.includes(item.toolkit),
        // Friday can use it: signed in, and switched on for the Friday Dot.
        active:
          connected.includes(item.toolkit) &&
          allowed &&
          !!dot?.composioToolkits.includes(item.toolkit),
      })),
      more: MORE_APPS.filter(
        (item) => !apps.some((entry) => entry.toolkit === item.toolkit),
      ),
      builtIn: BUILT_IN,
    });
  });

  // The owner pastes their Composio key: checked, saved to .env, used at once.
  app.post('/friday/apps/key', async (c) => {
    const input = z
      .object({ key: z.string().trim().min(8).max(200) })
      .safeParse(await c.req.json().catch(() => null));
    if (!input.success)
      return c.json({ error: 'Paste your Composio API key.' }, 400);
    const key = input.data.key;
    try {
      await deps.connected(key, platform.workspace.ownerId);
    } catch {
      return c.json(
        {
          error:
            'Composio did not accept that key. Copy it again from platform.composio.dev.',
        },
        400,
      );
    }
    setEnvLine(envPath, 'COMPOSIO_API_KEY', key);
    process.env.COMPOSIO_API_KEY = key;
    platform.config.composioApiKey = key;
    return c.json({ ok: true });
  });

  // Friday's mail helper reads the inbox here when Gmail is connected through
  // Composio (read only; nothing opens on screen). connected: false sends it
  // back to Friday's Chrome.
  for (const [which, read] of [
    ['unread', deps.unreadMail],
    ['latest', deps.latestMail],
  ] as const) {
    app.get(`/friday/apps/gmail/${which}`, async (c) => {
      const apiKey = platform.config.composioApiKey;
      if (!apiKey) return c.json({ connected: false });
      try {
        const connected = await deps.connected(
          apiKey,
          platform.workspace.ownerId,
        );
        if (!connected.includes('gmail')) return c.json({ connected: false });
        return c.json({
          connected: true,
          ...(await read(apiKey, platform.workspace.ownerId)),
        });
      } catch (e) {
        return c.json(
          { error: e instanceof Error ? e.message : String(e) },
          502,
        );
      }
    });
  }

  // Another app, from the suggestions or by its Composio name.
  app.post('/friday/apps/add', async (c) => {
    const input = z
      .object({ toolkit: slug, name: z.string().trim().max(40).optional() })
      .safeParse(await c.req.json().catch(() => null));
    if (!input.success)
      return c.json(
        { error: input.error.issues[0]?.message ?? 'Name the app.' },
        400,
      );
    const { toolkit } = input.data;
    if (allApps().some((item) => item.toolkit === toolkit))
      return c.json({ ok: true });
    const known = MORE_APPS.find((item) => item.toolkit === toolkit);
    const name =
      input.data.name ||
      known?.name ||
      toolkit.charAt(0).toUpperCase() + toolkit.slice(1);
    saveAdded([
      ...added(),
      {
        id: toolkit,
        name,
        toolkit,
        does: known?.does ?? `Your ${name} account`,
      },
    ]);
    return c.json({ ok: true });
  });

  app.delete('/friday/apps/:id', (c) => {
    const id = c.req.param('id');
    const list = added();
    if (!list.some((item) => item.id === id))
      return c.json({ error: 'Only apps you added can be removed.' }, 400);
    saveAdded(list.filter((item) => item.id !== id));
    return c.json({ ok: true });
  });

  app.post('/friday/apps/:id/connect', async (c) => {
    const item = allApps().find((entry) => entry.id === c.req.param('id'));
    if (!item) return c.json({ error: 'No such app.' }, 404);
    const apiKey = platform.config.composioApiKey;
    if (!apiKey)
      return c.json({ error: 'Add your Composio API key first.' }, 409);
    const dot = friday();
    if (!dot)
      return c.json({ error: 'Friday is not set up yet. Run the setup.' }, 404);
    // Switch the app on for Friday: the owner's choice, made by connecting it.
    platform.store.updateSettings({ appsAllowed: true });
    platform.workspace.updateDot(dot.id, {
      name: dot.name,
      instructions: dot.instructions,
      researchAllowed: dot.researchAllowed,
      memoryAllowed: dot.memoryAllowed,
      appsAllowed: true,
      composioToolkits: [...new Set([...dot.composioToolkits, item.toolkit])],
    });
    try {
      const url = await deps.signIn(
        apiKey,
        platform.workspace.ownerId,
        item.toolkit,
      );
      return c.json({ url });
    } catch (e) {
      return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
    }
  });

  return app;
}
