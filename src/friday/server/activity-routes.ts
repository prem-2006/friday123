// Friday: brings friday-services' live activity (what Navigator and Operator
// are doing, with screenshots) to the voice screen, and relays the owner's
// stop button. It also passes the voice screen's WebRTC offer to the live
// voice pipeline. The services token never reaches the browser.
import { Hono } from 'hono';

export function fridayActivityRoutes({
  servicesUrl,
  token,
  fetch = globalThis.fetch,
}: {
  servicesUrl: string;
  token?: string;
  fetch?: typeof globalThis.fetch;
}) {
  const app = new Hono();
  const base = servicesUrl.replace(/\/+$/, '');
  const headers = { Authorization: `Bearer ${token ?? ''}` };
  const unavailable = () =>
    Response.json(
      {
        error:
          'Friday services are not reachable. Start friday/services and set FRIDAY_SERVICES_TOKEN.',
      },
      { status: 503 },
    );
  const relay = async (
    path: string,
    method = 'GET',
    signal?: AbortSignal,
    body?: string,
  ) => {
    if (!token) return unavailable();
    try {
      const response = await fetch(`${base}${path}`, {
        method,
        headers: body
          ? { ...headers, 'Content-Type': 'application/json' }
          : headers,
        signal,
        body,
      });
      return new Response(response.body, {
        status: response.status,
        headers: {
          'Content-Type':
            response.headers.get('content-type') ?? 'application/json',
          'Cache-Control': 'no-store',
        },
      });
    } catch {
      return unavailable();
    }
  };

  app.get('/friday/activity', () => relay('/activity'));
  app.get('/friday/activity/stream', (c) =>
    relay(
      // A voice window (voice screen or notch) also gets Caps Lock signals.
      ['1', 'notch'].includes(c.req.query('voice') ?? '')
        ? `/activity/stream?voice=${c.req.query('voice')}`
        : '/activity/stream',
      'GET',
      c.req.raw.signal,
    ),
  );
  app.post('/friday/activity/stop', () => relay('/activity/stop', 'POST'));
  app.post('/friday/activity/resume', () => relay('/activity/resume', 'POST'));
  // "Hey Friday" or a double clap: the wake greeting is spoken, listen now.
  app.post('/friday/wake/ready', () => relay('/wake/ready', 'POST'));
  // "Stop": a double clap no longer wakes Friday, until "Hey Friday".
  app.post('/friday/wake/stopped', async (c) =>
    relay('/wake/stopped', 'POST', undefined, await c.req.text()),
  );
  // The people saved for "who is this".
  app.get('/friday/people', () => relay('/people'));
  app.delete('/friday/people/:name', (c) =>
    relay(`/people/${encodeURIComponent(c.req.param('name'))}`, 'DELETE'),
  );
  // Friday's main page: its Google account and one-click shortcuts.
  app.get('/friday/account/google', () => relay('/account/google'));
  app.post('/friday/account/google/connect', async (c) =>
    relay('/account/google/connect', 'POST', undefined, await c.req.text()),
  );
  // The owner's own Chrome: its signed-in Google accounts, and opening a
  // page there (Gmail, an app's sign-in) instead of in Friday's own Chrome.
  app.get('/friday/account/chrome', () => relay('/account/chrome'));
  app.post('/friday/browser/open-mine', async (c) =>
    relay('/browser/open-mine', 'POST', undefined, await c.req.text()),
  );
  app.post('/friday/browser/open', async (c) =>
    relay('/browser/open', 'POST', undefined, await c.req.text()),
  );
  // The main page's Calls card: Friday's phone calls and what was said.
  app.get('/friday/calls', () => relay('/calls/recent'));
  // The main page's reminders panel (timers, reminders, deadlines).
  app.get('/friday/reminders', () => relay('/reminders'));
  app.post('/friday/reminders', async (c) =>
    relay('/reminders', 'POST', undefined, await c.req.text()),
  );
  app.post('/friday/reminders/:id/done', (c) =>
    relay(`/reminders/${encodeURIComponent(c.req.param('id'))}/done`, 'POST'),
  );
  app.post('/friday/voice/offer', async (c) =>
    relay('/voice/offer', 'POST', undefined, await c.req.text()),
  );
  return app;
}
