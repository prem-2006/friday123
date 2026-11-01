// Friday: instant commands and learned shortcuts (friday_services/instant.py,
// after Atticus by SaarthurR, MIT). A voice turn asks friday-services first;
// a simple ask ("open Notepad", "Chrome left, Notepad right", a voice shortcut)
// is done there with no model call. After a model turn that used only Friday's
// quick tools, the calls are sent back so the same ask is instant next time.
import { spokenResult } from './quick-finish.js';

export interface InstantCall {
  tool: string;
  args: Record<string, unknown>;
}

export interface InstantClient {
  run(text: string, signal?: AbortSignal): Promise<{ say: string } | null>;
  learn(text: string, calls: InstantCall[], reply: string): Promise<void>;
}

// Instant answers must be instant: past this the model takes the turn.
const RUN_TIMEOUT_MS = 8_000;

export function instantClient({
  servicesUrl,
  token,
  fetch = globalThis.fetch,
}: {
  servicesUrl: string;
  token?: string;
  fetch?: typeof globalThis.fetch;
}): InstantClient | undefined {
  if (!token) return undefined;
  const base = servicesUrl.replace(/\/+$/, '');
  const post = (path: string, body: unknown, signal?: AbortSignal) =>
    fetch(`${base}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal,
    });
  return {
    async run(text, signal) {
      try {
        const timeout = AbortSignal.timeout(RUN_TIMEOUT_MS);
        const response = await post(
          '/instant',
          { text },
          signal ? AbortSignal.any([signal, timeout]) : timeout,
        );
        if (!response.ok) return null;
        const data = (await response.json()) as {
          handled?: boolean;
          say?: unknown;
        };
        return data.handled && typeof data.say === 'string' && data.say.trim()
          ? { say: data.say.trim() }
          : null;
      } catch {
        return null; // services down or slow: the model answers as before
      }
    },
    async learn(text, calls, reply) {
      try {
        await post('/instant/learn', { text, calls, reply });
      } catch {
        // Learning is a bonus; a missed one costs nothing.
      }
    },
  };
}

interface TurnMessage {
  role: string;
  content?: unknown;
  toolCallId?: string;
  toolCalls?: { id: string; function: { name: string; arguments: string } }[];
}

const QUICK_PREFIX = 'quick__';

/** The tool calls a finished turn made, if every one worked. Calls to other
 * servers keep their full names, so friday-services refuses to learn them. */
export function turnCalls(messages: readonly TurnMessage[]): InstantCall[] {
  const results = new Map(
    messages
      .filter((item) => item.role === 'tool' && item.toolCallId)
      .map((item) => [item.toolCallId!, item.content]),
  );
  const calls: InstantCall[] = [];
  for (const item of messages) {
    if (item.role !== 'assistant') continue;
    for (const call of item.toolCalls ?? []) {
      if (!spokenResult(results.get(call.id))) return [];
      let args: unknown;
      try {
        args = JSON.parse(call.function.arguments || '{}');
      } catch {
        return [];
      }
      if (!args || typeof args !== 'object' || Array.isArray(args)) return [];
      const name = call.function.name;
      calls.push({
        tool: name.startsWith(QUICK_PREFIX)
          ? name.slice(QUICK_PREFIX.length)
          : name,
        args: args as Record<string, unknown>,
      });
    }
  }
  return calls;
}
