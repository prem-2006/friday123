import { expect, it, vi } from 'vitest';
import {
  fridayAppsRoutes,
  splitSender,
  unreadFromComposio,
} from '../../src/friday/server/apps.js';

function fakePlatform(apiKey?: string) {
  const dot = {
    id: 'f',
    name: 'Friday',
    instructions: 'hi',
    researchAllowed: true,
    memoryAllowed: true,
    appsAllowed: false,
    composioToolkits: [] as string[],
  };
  const settings = { appsAllowed: false };
  return {
    dot,
    settings,
    platform: {
      config: { composioApiKey: apiKey },
      store: {
        settings: () => settings,
        updateSettings: (patch: Partial<typeof settings>) =>
          Object.assign(settings, patch),
      },
      workspace: {
        ownerId: 'owner-1',
        dots: () => [dot],
        updateDot: (_id: string, patch: Partial<typeof dot>) =>
          Object.assign(dot, patch),
      },
    } as never,
  };
}

it('lists the apps, and says a Composio key is needed first', async () => {
  const { platform } = fakePlatform();
  const app = fridayAppsRoutes(platform, {
    connected: vi.fn(),
    signIn: vi.fn(),
    unreadMail: vi.fn(),
    latestMail: vi.fn(),
  });
  const state = await (await app.request('/friday/apps')).json();
  expect(state.composio).toBe(false);
  expect(state.apps.map((a: { id: string }) => a.id)).toEqual([
    'gmail',
    'calendar',
    'drive',
  ]);
  expect(
    (await app.request('/friday/apps/gmail/connect', { method: 'POST' }))
      .status,
  ).toBe(409);
});

it('connecting an app gives Google’s sign-in link and turns it on for Friday', async () => {
  const { platform, dot, settings } = fakePlatform('key');
  const signIn = vi.fn(async () => 'https://connect.composio.dev/link/abc');
  const connected = vi.fn(async () => ['gmail']);
  const app = fridayAppsRoutes(platform, {
    connected,
    signIn,
    unreadMail: vi.fn(),
    latestMail: vi.fn(),
  });
  const done = await (
    await app.request('/friday/apps/gmail/connect', { method: 'POST' })
  ).json();
  expect(done.url).toBe('https://connect.composio.dev/link/abc');
  expect(signIn).toHaveBeenCalledWith('key', 'owner-1', 'gmail');
  expect(settings.appsAllowed).toBe(true);
  expect(dot.appsAllowed).toBe(true);
  expect(dot.composioToolkits).toEqual(['gmail']);
  const state = await (await app.request('/friday/apps')).json();
  const gmail = state.apps.find((a: { id: string }) => a.id === 'gmail');
  expect(gmail).toMatchObject({ connected: true, active: true });
  expect(
    state.apps.find((a: { id: string }) => a.id === 'calendar').active,
  ).toBe(false);
});

it('saves a Composio key that works, and adds and removes more apps', async () => {
  const { mkdtempSync, readFileSync, writeFileSync } = await import('node:fs');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const folder = mkdtempSync(join(tmpdir(), 'friday-apps-'));
  const envPath = join(folder, '.env');
  writeFileSync(envPath, 'GNANI_API_KEY=g\nCOMPOSIO_API_KEY=\n');
  const { platform } = fakePlatform();
  const connected = vi.fn(async (key: string) => {
    if (key !== 'good-key-123') throw new Error('401');
    return [];
  });
  const app = fridayAppsRoutes(platform, {
    connected,
    signIn: vi.fn(),
    unreadMail: vi.fn(),
    latestMail: vi.fn(),
    envPath,
    addedPath: join(folder, 'apps.json'),
  });
  const post = (path: string, body: unknown) =>
    app.request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  expect((await post('/friday/apps/key', { key: 'wrong-key-1' })).status).toBe(
    400,
  );
  expect((await post('/friday/apps/key', { key: 'good-key-123' })).status).toBe(
    200,
  );
  expect(readFileSync(envPath, 'utf8')).toBe(
    'GNANI_API_KEY=g\nCOMPOSIO_API_KEY=good-key-123\n',
  );
  expect(
    (platform as { config: { composioApiKey?: string } }).config.composioApiKey,
  ).toBe('good-key-123');
  await post('/friday/apps/add', { toolkit: 'slack' });
  await post('/friday/apps/add', { toolkit: 'HubSpot' });
  expect(
    (await post('/friday/apps/add', { toolkit: 'no spaces!' })).status,
  ).toBe(400);
  let state = await (await app.request('/friday/apps')).json();
  const ids = state.apps.map((a: { id: string }) => a.id);
  expect(ids).toEqual(['gmail', 'calendar', 'drive', 'slack', 'hubspot']);
  expect(state.composio).toBe(true);
  expect(
    state.more.some((m: { toolkit: string }) => m.toolkit === 'slack'),
  ).toBe(false);
  await app.request('/friday/apps/slack', { method: 'DELETE' });
  expect(
    (await app.request('/friday/apps/gmail', { method: 'DELETE' })).status,
  ).toBe(400);
  state = await (await app.request('/friday/apps')).json();
  expect(state.apps.map((a: { id: string }) => a.id)).toEqual([
    'gmail',
    'calendar',
    'drive',
    'hubspot',
  ]);
});

it('reads unread mail through Composio only when Gmail is connected there', async () => {
  const { platform } = fakePlatform('key');
  let linked: string[] = [];
  const unreadMail = vi.fn(async () => ({ count: 1, mails: [] }));
  const app = fridayAppsRoutes(platform, {
    connected: vi.fn(async () => linked),
    signIn: vi.fn(),
    unreadMail,
    latestMail: vi.fn(),
  });
  const unread = async () =>
    (await app.request('/friday/apps/gmail/unread')).json();
  expect(await unread()).toEqual({ connected: false });
  expect(unreadMail).not.toHaveBeenCalled();
  linked = ['gmail'];
  expect(await unread()).toEqual({ connected: true, count: 1, mails: [] });
  expect(unreadMail).toHaveBeenCalledWith('key', 'owner-1');
});

it("turns Composio's Gmail answer into Friday's mail, newest first", () => {
  expect(splitSender('"Ravi Kumar" <ravi@x.com>')).toEqual([
    'Ravi Kumar',
    'ravi@x.com',
  ]);
  expect(splitSender('ravi@x.com')).toEqual(['', 'ravi@x.com']);
  const mail = unreadFromComposio({
    resultSizeEstimate: 7,
    messages: [
      {
        messageId: 'a1',
        sender: 'Google <no-reply@accounts.google.com>',
        subject: ' Security alert ',
        messageTimestamp: '2026-10-30T10:00:00Z',
        messageText: 'A new sign-in on Windows',
        preview: { body: 'A new sign-in' },
      },
      {
        messageId: 'b2',
        sender: 'Ravi <ravi@x.com>',
        subject: 'Plan',
        messageTimestamp: '2026-10-31T09:00:00Z',
      },
    ],
  });
  expect(mail.count).toBe(7);
  expect(mail.mails.map((m) => m.id)).toEqual(['b2', 'a1']);
  expect(mail.mails[1]).toEqual({
    id: 'a1',
    sender: 'Google',
    email: 'no-reply@accounts.google.com',
    subject: 'Security alert',
    preview: 'A new sign-in',
    when: '2026-10-30T10:00:00Z',
    text: 'A new sign-in on Windows',
  });
});
