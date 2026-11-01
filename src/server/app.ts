import { computerRoutes } from './computer-routes.js';
import { connectionRoutes } from './connection-routes.js';
import { Hono } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { Store } from './store.js';
import { Runner } from './runner.js';
import { configured, type Config } from './research.js';
import type { Platform } from './platform.js';
import { VoiceService } from './voice.js';
import { workspaceRoutes } from './workspace-routes.js';
import { fridayVoiceRoutes } from '../friday/server/voice-routes.js';
import { fridayActivityRoutes } from '../friday/server/activity-routes.js';
import { fridayRunRoutes } from '../friday/server/run-routes.js';
import { fridayMemoryRoutes } from '../friday/server/memory-routes.js';
import { fridayAppsRoutes } from '../friday/server/apps.js';
import { fridayAdminRoutes } from '../friday/server/admin-routes.js';
import { fridayProfileRoutes } from '../friday/server/profile.js';
import { fridayContactsRoutes } from '../friday/server/contacts.js';
const interval = z.number().int().min(60).max(31_536_000).nullable();
export interface AppOptions {
  store: Store;
  runner: Runner;
  config: Config;
  ownerToken?: string;
  origin?: string | string[];
  platform?: Platform;
}
export function createApp({
  store,
  runner,
  config,
  ownerToken,
  origin,
  platform,
}: AppOptions) {
  const app = new Hono();
  app.use(
    '/api/*',
    bodyLimit({
      maxSize: 1_000_000,
      onError: (c) => c.json({ error: 'Request is too large.' }, 413),
    }),
  );
  app.use('/api/*', async (c, next) => {
    c.header('Cache-Control', 'no-store');
    c.header('X-Content-Type-Options', 'nosniff');
    const requestUrl = new URL(c.req.url);
    const origins = origin
      ? Array.isArray(origin)
        ? origin
        : [origin]
      : undefined;
    const originHostnames = origins
      ? origins
          .map((o) => {
            try {
              return new URL(o).hostname;
            } catch {
              return '';
            }
          })
          .filter(Boolean)
      : [];
    const allowedHosts = new Set([
      'localhost',
      '127.0.0.1',
      '[::1]',
      ...originHostnames,
    ]);
    if (!ownerToken && !allowedHosts.has(requestUrl.hostname))
      return c.json({ error: 'Unrecognized host.' }, 403);
    const requestOrigin = c.req.header('origin');
    const allowedOrigins = new Set(origins ?? [new URL(c.req.url).origin]);
    if (requestOrigin && !allowedOrigins.has(requestOrigin))
      return c.json({ error: 'Cross-origin requests are not allowed.' }, 403);
    if (c.req.header('sec-fetch-site') === 'cross-site')
      return c.json({ error: 'Cross-site requests are not allowed.' }, 403);
    if (ownerToken) {
      const expected = Buffer.from(ownerToken);
      const supplied = Buffer.from(
        c.req.header('authorization')?.replace(/^Bearer /, '') ?? '',
      );
      if (
        expected.length !== supplied.length ||
        !timingSafeEqual(expected, supplied)
      )
        return c.json(
          { error: 'Enter your owner access token to unlock OpenDots.' },
          401,
        );
    }
    if (
      !['GET', 'HEAD'].includes(c.req.method) &&
      !c.req.header('content-type')?.includes('application/json')
    )
      return c.json({ error: 'Use application/json.' }, 415);
    await next();
  });
  // FRIDAY: Gnani speech for talking to Friday (Prisma in, Timbre out).
  app.route('/api', fridayVoiceRoutes({ apiKey: process.env.GNANI_API_KEY }));
  // FRIDAY: live helper activity and the stop button, from friday/services.
  app.route(
    '/api',
    fridayActivityRoutes({
      servicesUrl:
        process.env.FRIDAY_SERVICES_URL ??
        `http://127.0.0.1:${process.env.FRIDAY_SERVICES_PORT || 8765}`,
      token: process.env.FRIDAY_SERVICES_TOKEN,
    }),
  );
  // FRIDAY: the Admin page: how every outside API is doing, live.
  app.route(
    '/api',
    fridayAdminRoutes({
      servicesUrl:
        process.env.FRIDAY_SERVICES_URL ??
        `http://127.0.0.1:${process.env.FRIDAY_SERVICES_PORT || 8765}`,
      token: process.env.FRIDAY_SERVICES_TOKEN,
    }),
  );
  // FRIDAY: the owner's details, edited on Friday's main page.
  app.route('/api', fridayProfileRoutes());
  app.route('/api', fridayContactsRoutes());
  // FRIDAY: fast voice turns, run directly on this server.
  if (platform) app.route('/api', fridayRunRoutes(platform));
  // FRIDAY: the Admin page's memory graph, from the Obsidian vault.
  if (platform?.obsidianMemory)
    app.route('/api', fridayMemoryRoutes(platform.obsidianMemory));
  // FRIDAY: the main page's Connections card (Gmail, Calendar, Drive).
  if (platform) app.route('/api', fridayAppsRoutes(platform));
  if (platform) app.route('/api', computerRoutes(platform.computers));
  if (platform)
    app.route(
      '/api',
      connectionRoutes(platform.workspace, platform.connections),
    );
  const voice = platform ? new VoiceService(platform) : undefined;
  if (platform && voice) app.route('/api', workspaceRoutes(platform, voice));
  app.get('/api/state', (c) =>
    c.json({
      settings: store.settings(),
      tasks: store.tasks(),
      memories: store.memories(),
      mode: config.mode,
      configured: configured(config),
    }),
  );
  app.post('/api/tasks', async (c) => {
    const parsed = z
      .object({
        prompt: z.string().trim().min(3).max(4000),
        intervalSeconds: interval.optional(),
        threadId: z.string().optional(),
      })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success)
      return c.json(
        {
          error:
            'Enter a request between 3 and 4,000 characters; repeat intervals must be at least 60 seconds.',
        },
        400,
      );
    if (!store.settings().researchAllowed)
      return c.json({ error: 'Research is disabled in Settings.' }, 403);
    if (platform) {
      if (platform.setup().missing.length)
        return c.json(
          { error: `Setup required: ${platform.setup().missing.join(', ')}.` },
          503,
        );
      if (!parsed.data.threadId)
        return c.json(
          { error: 'Select a conversation for this scheduled task.' },
          400,
        );
      try {
        platform.workspace.requireThread(parsed.data.threadId);
      } catch {
        return c.json(
          { error: 'Conversation is not owned by this workspace.' },
          403,
        );
      }
    }
    const task = store.createTask(
      parsed.data.prompt,
      parsed.data.intervalSeconds,
    );
    if (platform && parsed.data.threadId)
      platform.workspace.bindTask(task.id, parsed.data.threadId);
    return c.json(task, 201);
  });
  app.get('/api/tasks/:id', (c) => {
    const detail = store.detail(c.req.param('id'));
    return detail ? c.json(detail) : c.json({ error: 'Task not found.' }, 404);
  });
  app.post('/api/tasks/:id/actions', async (c) => {
    const parsed = z
      .object({ action: z.enum(['run', 'pause', 'cancel']) })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success) return c.json({ error: 'Unknown task action.' }, 400);
    if (parsed.data.action === 'run' && !store.settings().researchAllowed)
      return c.json({ error: 'Research is disabled in Settings.' }, 403);
    const task = store.action(c.req.param('id'), parsed.data.action);
    if (parsed.data.action !== 'run') runner.abort(c.req.param('id'));
    return task ? c.json(task) : c.json({ error: 'Task not found.' }, 404);
  });
  app.put('/api/tasks/:id/schedule', async (c) => {
    const parsed = z
      .object({ intervalSeconds: interval })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success)
      return c.json(
        { error: 'Repeat interval must be 60 seconds to one year, or null.' },
        400,
      );
    const task = store.schedule(c.req.param('id'), parsed.data.intervalSeconds);
    return task ? c.json(task) : c.json({ error: 'Task not found.' }, 404);
  });
  app.patch('/api/settings', async (c) => {
    const parsed = z
      .object({
        name: z.string().trim().min(1).max(40).optional(),
        paused: z.boolean().optional(),
        researchAllowed: z.boolean().optional(),
        memoryAllowed: z.boolean().optional(),
        appsAllowed: z.boolean().optional(),
        obsidianVaultPath: z.string().trim().max(1000).optional(),
      })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success) return c.json({ error: 'Invalid settings.' }, 400);
    const previous = store.settings();
    const vaultPathChanged =
      parsed.data.obsidianVaultPath !== undefined &&
      previous.obsidianVaultPath !== parsed.data.obsidianVaultPath;
    if (vaultPathChanged && platform?.obsidianMemory) {
      try {
        await platform.obsidianMemory.prepareVault(
          parsed.data.obsidianVaultPath,
        );
        for (const memory of store.memories())
          await platform.obsidianMemory.saveMemory(
            memory,
            parsed.data.obsidianVaultPath,
          );
      } catch {
        return c.json(
          {
            error:
              'Could not copy saved memories to that Obsidian vault. Check the folder path and permissions.',
          },
          503,
        );
      }
    }
    const settings = store.updateSettings(parsed.data);
    if (
      settings.paused ||
      !settings.researchAllowed ||
      previous.memoryAllowed !== settings.memoryAllowed ||
      previous.appsAllowed !== settings.appsAllowed ||
      vaultPathChanged
    )
      runner.abortAll();
    if (settings.paused || vaultPathChanged) voice?.abortAll();
    else void voice?.resumePending();
    return c.json(settings);
  });
  app.post('/api/memories', async (c) => {
    const parsed = z
      .object({ text: z.string().trim().min(1).max(2000) })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success)
      return c.json(
        { error: 'Memory must be between 1 and 2,000 characters.' },
        400,
      );
    const id = randomUUID();
    const memory = { id, text: parsed.data.text, createdAt: Date.now() };
    try {
      await platform?.obsidianMemory?.saveMemory(memory);
    } catch {
      return c.json(
        {
          error:
            'Could not save to Obsidian. Check the vault folder path and permissions.',
        },
        503,
      );
    }
    return c.json(store.saveMemory(memory.text, memory.id), 201);
  });
  app.put('/api/memories/:id', async (c) => {
    const parsed = z
      .object({ text: z.string().trim().min(1).max(2000) })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success)
      return c.json(
        { error: 'Memory must be between 1 and 2,000 characters.' },
        400,
      );
    const current = store.memories().find((m) => m.id === c.req.param('id'));
    if (!current) return c.json({ error: 'Memory not found.' }, 404);
    const memory = {
      ...current,
      text: parsed.data.text,
      createdAt: Date.now(),
    };
    try {
      await platform?.obsidianMemory?.saveMemory(memory);
    } catch {
      return c.json(
        {
          error:
            'Could not update the Obsidian note. Check the vault folder path and permissions.',
        },
        503,
      );
    }
    return c.json(store.saveMemory(memory.text, memory.id));
  });
  app.delete('/api/memories/:id', async (c) => {
    const id = c.req.param('id');
    if (!store.memories().some((m) => m.id === id))
      return c.json({ error: 'Memory not found.' }, 404);
    try {
      await platform?.obsidianMemory?.deleteMemory(id);
    } catch {
      return c.json(
        {
          error:
            'Could not remove the Obsidian note. Check the vault folder path and permissions.',
        },
        503,
      );
    }
    return store.deleteMemory(id)
      ? c.json({ ok: true })
      : c.json({ error: 'Memory not found.' }, 404);
  });
  app.onError((error, c) => {
    console.error('API request failed:', error.name);
    return c.json(
      {
        error:
          'The server could not complete this request. Check server logs and database access.',
      },
      500,
    );
  });
  return app;
}
