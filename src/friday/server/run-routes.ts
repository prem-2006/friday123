// Friday: the voice stage's own turn endpoint, and the one for phone calls.
// Each runs a Dot directly (see direct-run.ts) with the history it is sent.
// On a call the owner gets the Friday Dot itself, so a call can do whatever
// the desktop can; anyone else gets the Reception Dot, which has no tools and
// knows nothing private about the owner.
import { Hono, type Context } from 'hono';
import { z } from 'zod';
import type { Message } from '@ag-ui/core';
import { FRIDAY_DOT_NAME } from './delegate.js';
import { voiceLog } from './voice-log.js';
import { fallbackReply } from './quick-finish.js';
import {
  localThread,
  runDotDirectly,
  type DirectPlatform,
} from './direct-run.js';

export const VOICE_THREAD_TITLE = 'Friday voice';
export const PHONE_THREAD_TITLE = 'Friday phone';
export const RECEPTION_DOT_NAME = 'Reception';
export const RECEPTION_THREAD_TITLE = 'Phone callers';
const MAX_MESSAGES = 40;

const message = z
  .object({
    id: z.string().min(1).max(200),
    // Other roles (e.g. "reasoning" from models that think out loud) are
    // accepted here and left out below.
    role: z.string().min(1).max(40),
  })
  .passthrough();
const CONVERSATION_ROLES = new Set(['user', 'assistant', 'tool']);
const conversational = <T extends { role: string }>(items: T[]) =>
  items.filter((item) => CONVERSATION_ROLES.has(item.role));

type HistoryItem = {
  role: string;
  content?: unknown;
  toolCallId?: string;
  toolCalls?: { id: string }[];
};
// An action that never got an answer (an approval that expired, a run cut
// short) leaves a tool call with no result in the screen's history, and the
// model then ends every later turn at once with nothing to say. Drop those.
const answeredOnly = <T extends HistoryItem>(items: T[]) => {
  const answered = new Set(
    items.flatMap((item) => (item.role === 'tool' ? [item.toolCallId] : [])),
  );
  return items.flatMap((item) => {
    if (item.role !== 'assistant' || !item.toolCalls?.length) return [item];
    const toolCalls = item.toolCalls.filter((call) => answered.has(call.id));
    if (toolCalls.length === item.toolCalls.length) return [item];
    if (!toolCalls.length && !item.content) return [];
    return [{ ...item, toolCalls: toolCalls.length ? toolCalls : undefined }];
  });
};

type Run = typeof runDotDirectly;
type Turn = { dotName: string; threadTitle: string; owner: boolean };

export function fridayRunRoutes(
  platform: DirectPlatform,
  run: Run = runDotDirectly,
) {
  const app = new Hono();
  const turn = async (
    c: Context,
    messages: z.infer<typeof message>[],
    { dotName, threadTitle, owner }: Turn,
  ) => {
    const friday = platform.workspace
      .dots()
      .find((dot) => dot.name === dotName);
    if (!friday)
      return c.json(
        { error: `${dotName} is not set up yet. Run the setup script.` },
        404,
      );
    if (platform.store.settings().paused)
      return c.json(
        { error: 'Friday is paused. Tap Resume to continue.' },
        409,
      );
    const thread = localThread(platform, friday.id, threadTitle);
    const started = performance.now();
    try {
      const ran = await run(
        platform,
        friday.id,
        thread.id,
        answeredOnly(
          conversational(messages) as unknown as HistoryItem[],
        ) as unknown as Message[],
        // Only the owner can approve an action; Reception has no tools.
        { approvals: owner, signal: c.req.raw.signal },
      );
      // The screen keeps only the conversation itself, not a model's notes.
      const newMessages = conversational(ran);
      let said = newMessages
        .filter((item) => item.role === 'assistant')
        .map((item) => (typeof item.content === 'string' ? item.content : ''))
        .filter(Boolean)
        .at(-1);
      // A quick action ended the turn, or the model went quiet after a
      // helper's report: say what the tool reported.
      const reported = fallbackReply(newMessages);
      if (reported && newMessages.at(-1)?.role === 'tool') {
        newMessages.push({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: reported,
        } as Message);
        said = reported;
      }
      const userTurn = messages.findLast((item) => item.role === 'user');
      const userText =
        typeof userTurn?.content === 'string' ? userTurn.content.trim() : '';
      if (
        owner &&
        userTurn &&
        userText &&
        said &&
        platform.obsidianMemory &&
        platform.store.settings().memoryAllowed &&
        friday.memoryAllowed
      ) {
        try {
          await platform.obsidianMemory.saveConversation({
            id: userTurn.id,
            createdAt: Date.now(),
            userText,
            assistantText: said,
          });
        } catch {
          voiceLog('obsidian_save_failed', { kind: 'conversation' });
        }
      }
      voiceLog('answered', {
        dot: dotName,
        ms: Math.round(performance.now() - started),
        tools: newMessages.flatMap((item) =>
          item.role === 'assistant'
            ? (item.toolCalls ?? []).map((call) => call.function.name)
            : [],
        ),
        text: said ?? '',
      });
      return c.json({ threadId: thread.id, newMessages });
    } catch (error) {
      voiceLog('run_failed', {
        ms: Math.round(performance.now() - started),
        error: error instanceof Error ? error.message : String(error),
      });
      return c.json(
        {
          error:
            error instanceof Error && error.message
              ? error.message
              : 'Friday could not finish that.',
        },
        502,
      );
    }
  };
  const history = z.array(message).min(1).max(MAX_MESSAGES);
  app.post('/friday/voice/run', async (c) => {
    const input = z
      .object({ messages: history })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!input.success)
      return c.json({ error: 'Send the conversation messages.' }, 400);
    return turn(c, input.data.messages, {
      dotName: FRIDAY_DOT_NAME,
      threadTitle: VOICE_THREAD_TITLE,
      owner: true,
    });
  });
  // friday/services decides who is calling (the owner's number, or anyone).
  app.post('/friday/phone/run', async (c) => {
    const input = z
      .object({ messages: history, caller: z.enum(['owner', 'client']) })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!input.success)
      return c.json(
        { error: 'Send the call messages and who is calling.' },
        400,
      );
    const owner = input.data.caller === 'owner';
    return turn(
      c,
      input.data.messages,
      owner
        ? { dotName: FRIDAY_DOT_NAME, threadTitle: PHONE_THREAD_TITLE, owner }
        : {
            dotName: RECEPTION_DOT_NAME,
            threadTitle: RECEPTION_THREAD_TITLE,
            owner,
          },
    );
  });
  return app;
}
