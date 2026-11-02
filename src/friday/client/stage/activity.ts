// Friday: follow the helpers' live activity (Server-Sent Events relayed by
// the app). fetch is used instead of EventSource so the owner token header
// can be sent when OpenDots is protected with OWNER_TOKEN.
import type { ActivityEvent } from './narration';

export function ownerHeaders(): Record<string, string> {
  try {
    const token = sessionStorage.getItem('opendots-token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

const STREAM_SILENCE_MS = 40_000;

export function followActivity(
  onEvent: (event: ActivityEvent) => void,
  onStatus: (connected: boolean) => void,
  /** A voice window (voice screen, notch) also receives Caps Lock signals;
   * the notch gets them first when it is open. */
  voice: boolean | 'notch' = true,
) {
  const controller = new AbortController();
  const run = async () => {
    while (!controller.signal.aborted) {
      // The services send a keep-alive every 15 s. A stream silent for 40 s
      // died without saying so (the services restarted under the app), so
      // drop it and connect again; otherwise Caps Lock would reach no one.
      const attempt = new AbortController();
      const stopAttempt = () => attempt.abort();
      controller.signal.addEventListener('abort', stopAttempt);
      let silence: ReturnType<typeof setTimeout> | undefined;
      const heardFrom = () => {
        clearTimeout(silence);
        silence = setTimeout(stopAttempt, STREAM_SILENCE_MS);
      };
      try {
        heardFrom();
        // ?voice=1: this window handles Caps Lock (the newest notch does, else
        // the newest voice window).
        const response = await fetch(
          `/api/friday/activity/stream${voice ? `?voice=${voice === 'notch' ? 'notch' : '1'}` : ''}`,
          {
            headers: ownerHeaders(),
            signal: attempt.signal,
          },
        );
        if (!response.ok || !response.body) throw new Error('unavailable');
        onStatus(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          heardFrom();
          buffer += decoder.decode(value, { stream: true });
          let split;
          while ((split = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            const data = block
              .split('\n')
              .filter((line) => line.startsWith('data: '))
              .map((line) => line.slice(6))
              .join('');
            if (data) onEvent(JSON.parse(data) as ActivityEvent);
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      } finally {
        clearTimeout(silence);
        controller.signal.removeEventListener('abort', stopAttempt);
      }
      onStatus(false);
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  };
  void run();
  return () => controller.abort();
}

export async function setStopped(stopped: boolean) {
  await fetch(`/api/friday/activity/${stopped ? 'stop' : 'resume'}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...ownerHeaders() },
    body: '{}',
    // Never hold up a stop or a new request: the services lift a stop by
    // themselves after a few seconds anyway.
    signal: AbortSignal.timeout(3000),
  }).catch(() => {});
}
