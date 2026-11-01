#!/usr/bin/env node
// Friday: sets up the three Dots and their tools from friday/services.
//
//   Friday     the one you talk to; quick one-step actions (open an app or site,
//              play on YouTube, search, look at the screen, arrange windows),
//              WhatsApp tools (each send or call asks you first) and
//              delegate_to_dot to hand bigger work to the helpers
//   Navigator  drives a Chrome window on your screen (browser tools)
//   Operator   opens and works desktop apps (desktop tools)
//   Coder      solves practice problems on LeetCode-style sites (browser tools)
//   Mail       works in Gmail: summaries, what is pending, drafts (browser tools)
//   Reception  answers phone calls from anyone but the owner: no tools, no
//              memory, and only its own empty Space, so it can share nothing
//              private (the owner's own calls go to Friday itself)
//
// Helpers work in their own conversations without approval cards, so their
// tools run without "Ask first"; WhatsApp stays with Friday, behind your OK.
// Existing Dots are updated and connections refreshed, keeping your choices;
// pass --reconnect after changing FRIDAY_SERVICES_TOKEN or the port.
//
// Start the app (npm run dev) and friday/services first, then from the repo root:
//   node --env-file-if-exists=.env friday/scripts/setup-friday-dot.mjs

import { readFile } from 'node:fs/promises';

const APP_URL = (process.env.FRIDAY_APP_URL ?? 'http://127.0.0.1:4310').replace(
  /\/+$/,
  '',
);
const SERVICES_PORT = process.env.FRIDAY_SERVICES_PORT || 8765;
const SERVICES_URL = (
  process.env.FRIDAY_SERVICES_URL ?? `http://127.0.0.1:${SERVICES_PORT}`
).replace(/\/+$/, '');
const CALENDAR_URL =
  process.env.FRIDAY_CALENDAR_URL || `${SERVICES_URL}/calendar`;
const reconnect = process.argv.includes('--reconnect');
// Tool connections that could not be made (reported at the end).
const failed = [];

const DOTS = [
  {
    name: 'Friday',
    persona: '../persona.md',
    connections: ['whatsapp', 'quick', 'phone'],
  },
  {
    name: 'Navigator',
    persona: '../dots/navigator.md',
    connections: ['browser'],
  },
  {
    name: 'Operator',
    persona: '../dots/operator.md',
    connections: ['desktop'],
  },
  // Coding practice (LeetCode and similar) in Friday's Chrome window.
  { name: 'Coder', persona: '../dots/coder.md', connections: ['browser'] },
  // Gmail in Friday's Chrome window: summaries, what is pending, drafts.
  { name: 'Mail', persona: '../dots/mail.md', connections: ['browser'] },
  {
    name: 'Reception',
    persona: '../dots/reception.md',
    connections: [],
    space: 'Phone callers',
  },
];
// Parked skills that older setups connected to Friday.
const RETIRED = ['calendar', 'telegram'];
// Run without an approval card: one-step actions that send nothing and buy
// nothing; except these, which always ask the owner first.
const NO_APPROVAL = ['quick', 'phone'];
const ALWAYS_ASK = [
  'send_email',
  'schedule_meeting',
  'lock_screen',
  // start_call (calling someone for the owner) runs without an OK: the owner
  // asked for that on 2026-10-23.
];

async function api(path, method = 'GET', body) {
  const response = await fetch(`${APP_URL}/api${path}`, {
    method,
    headers: {
      // OpenDots only accepts JSON for anything but GET, DELETE included.
      ...(method === 'GET' ? {} : { 'Content-Type': 'application/json' }),
      ...(process.env.OWNER_TOKEN
        ? { Authorization: `Bearer ${process.env.OWNER_TOKEN}` }
        : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30_000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      `${method} ${path} returned HTTP ${response.status}: ${data.error ?? 'no details'}`,
    );
  return data;
}

async function persona(file) {
  const text = (await readFile(new URL(file, import.meta.url), 'utf8'))
    .trim()
    .replaceAll('{{CALENDAR_URL}}', CALENDAR_URL)
    .replaceAll('{{OWNER_NAME}}', process.env.FRIDAY_OWNER_NAME || 'the owner');
  if (text.length > 2000)
    throw new Error(
      `${file} is ${text.length} characters; OpenDots allows 2000.`,
    );
  return text;
}

async function connect(dot, name, helper) {
  const url = `${SERVICES_URL}/${name}/mcp`;
  const current = await api(`/dots/${dot.id}/connections`);
  const found = current.find((connection) => connection.name === name);
  if (found && (reconnect || found.url !== url))
    await api(`/connections/${found.id}`, 'DELETE');
  let connection =
    found && !reconnect && found.url === url
      ? await api(`/connections/${found.id}/refresh`, 'POST', {})
      : await api(`/dots/${dot.id}/connections`, 'POST', {
          name,
          url,
          token: process.env.FRIDAY_SERVICES_TOKEN,
        });
  if (connection.error) throw new Error(connection.error);
  if (helper)
    for (const tool of connection.tools) {
      const ask = ALWAYS_ASK.includes(tool.name);
      if (tool.requiresApproval !== ask)
        connection = await api(
          `/connections/${connection.id}/tools/${encodeURIComponent(tool.name)}`,
          'PATCH',
          { requiresApproval: ask },
        );
    }
  return connection;
}

let workspace;
try {
  workspace = await api('/workspace');
} catch (error) {
  console.error(
    `Could not reach OpenDots at ${APP_URL}. Start it with "npm run dev" first.\n${error.message}`,
  );
  process.exit(1);
}

for (const spec of DOTS) {
  const settings = {
    name: spec.name,
    instructions: await persona(spec.persona),
    // No background web search: every search happens in the on-screen
    // browser, through Navigator.
    researchAllowed: false,
    memoryAllowed: spec.name === 'Friday',
  };
  const existing = workspace.dots.find((dot) => dot.name === spec.name);
  // A Dot with its own Space sees only that Space's pages, never the owner's.
  let own;
  if (spec.space) {
    own =
      workspace.spaces.find((space) => space.name === spec.space) ??
      (await api('/spaces', 'POST', {
        name: spec.space,
        description: `Only for ${spec.name}; keeps the owner's pages out of its reach.`,
      }));
    workspace.spaces.push(own);
  }
  const dot = existing
    ? await api(`/dots/${existing.id}`, 'PUT', {
        ...settings,
        appsAllowed: own ? false : existing.appsAllowed,
        composioToolkits: own ? [] : existing.composioToolkits,
        spaceIds: own ? [own.id] : existing.spaceIds,
        spaceId: own ? own.id : existing.spaceId,
      })
    : await api('/dots', 'POST', {
        ...settings,
        spaceId: own ? own.id : workspace.spaces[0].id,
      });
  console.log(`${existing ? 'Updated' : 'Created'} ${dot.name} (${dot.id}).`);

  if (!process.env.FRIDAY_SERVICES_TOKEN) continue;
  if (spec.name === 'Friday')
    for (const old of (await api(`/dots/${dot.id}/connections`)).filter(
      (connection) => RETIRED.includes(connection.name),
    )) {
      await api(`/connections/${old.id}`, 'DELETE');
      console.log(`  Removed the parked ${old.name} connection.`);
    }
  for (const name of spec.connections)
    try {
      const connection = await connect(
        dot,
        name,
        spec.name !== 'Friday' || NO_APPROVAL.includes(name),
      );
      console.log(
        `  Connected ${name}: ${connection.tools.map((tool) => `${tool.name}${tool.requiresApproval ? ' (asks first)' : ''}`).join(', ')}`,
      );
    } catch (error) {
      console.log(
        `  Could not connect ${name} at ${SERVICES_URL}/${name}/mcp: ${error.message}`,
      );
      failed.push(`${spec.name}: ${name}`);
    }
}

// A helper without its tools can only say it has none, so make this loud.
if (failed.length) {
  console.log(
    `\nWARNING: these tools are NOT connected: ${failed.join(', ')}.\n` +
      'Start Friday (the services must be running), then run this setup again.',
  );
  process.exitCode = 1;
}

if (!process.env.FRIDAY_SERVICES_TOKEN)
  console.log(
    'Skipped tool connections: set FRIDAY_SERVICES_TOKEN in .env and start friday/services.',
  );
if (workspace.setup.missing.length)
  console.log(
    `Before chatting, OpenDots still needs: ${workspace.setup.missing.join(', ')}.`,
  );
