import type { Message } from '@ag-ui/core';
import { expect, it, vi } from 'vitest';
import {
  pendingApprovalOf,
  toolAnswer,
  trimHistory,
} from '../../src/friday/client/stage/conversation.js';
import { fridayRunRoutes } from '../../src/friday/server/run-routes.js';

const user = (id: string, content: string) =>
  ({ id, role: 'user', content }) as Message;
const reply = (id: string, content: string) =>
  ({ id, role: 'assistant', content }) as Message;
const askOk = (id: string, approvalId: string, summary: string) =>
  ({
    id,
    role: 'assistant',
    toolCalls: [
      {
        id: `call-${id}`,
        type: 'function',
        function: {
          name: 'request_connection_action',
          arguments: JSON.stringify({ approvalId, summary }),
        },
      },
    ],
  }) as Message;

it('keeps recent turns, always starting at something the owner said', () => {
  const history = [
    user('u1', 'first'),
    reply('a1', 'one'),
    { id: 't1', role: 'tool', toolCallId: 'x', content: 'y'.repeat(9000) },
    user('u2', 'second'),
    reply('a2', 'two'),
  ] as Message[];
  expect(trimHistory(history, 3).map((m) => m.id)).toEqual(['u2', 'a2']);
  const full = trimHistory(history, 10);
  expect(full[0].id).toBe('u1');
  expect((full[2].content as string).length).toBeLessThan(4100);
});

// The connection tool's answer: the server saved this action for the owner's OK.
const saved = (id: string, approvalId: string) =>
  ({
    id,
    role: 'tool',
    toolCallId: `send-${id}`,
    content: JSON.stringify({ status: 'approval_required', approvalId }),
  }) as Message;

it('finds an approval still waiting for the owner', () => {
  const waiting = [
    user('u1', 'WhatsApp Priya I am late'),
    saved('t1', 'ap_1'),
    askOk('a1', 'ap_1', 'Send "I am late" to Priya'),
  ];
  const pending = pendingApprovalOf(waiting);
  expect(pending).toEqual({
    toolCallId: 'call-a1',
    approvalId: 'ap_1',
    summary: 'Send "I am late" to Priya',
  });
  const answered = [...waiting, toolAnswer(pending!, { approved: true })];
  expect(pendingApprovalOf(answered)).toBeUndefined();
  expect(
    pendingApprovalOf([...waiting, user('u2', 'never mind')]),
  ).toBeUndefined();
});

it('uses the approval the server saved, not an id the model made up', () => {
  // Models sometimes ask for the OK in the same step as the action, before
  // its approvalId exists, and invent one; only the saved one can run.
  const madeUp = [
    user('u1', 'WhatsApp Priya I am late'),
    saved('t1', 'ap_real'),
    askOk('a1', 'ap_invented', 'Send "I am late" to Priya'),
  ];
  expect(pendingApprovalOf(madeUp)?.approvalId).toBe('ap_real');
  // Nothing saved: there is nothing the owner could approve.
  expect(
    pendingApprovalOf([
      user('u1', 'WhatsApp Priya I am late'),
      askOk('a1', 'ap_1', 'Send "I am late" to Priya'),
    ]),
  ).toBeUndefined();
});

function fakePlatform(paused = false) {
  const conversations: { id: string; dotId: string; title: string }[] = [];
  return {
    store: { settings: () => ({ paused }) },
    workspace: {
      dots: () => [{ id: 'f', name: 'Friday' }],
      conversations: () => conversations,
      bindThread: (id: string, dotId: string, title: string) => {
        const thread = { id, dotId, title };
        conversations.push(thread);
        return thread;
      },
    },
    config: {},
    setupTelemetry: undefined,
    delegate: async () => '',
  } as never;
}

const post = (app: ReturnType<typeof fridayRunRoutes>, body: unknown) =>
  app.request('/friday/voice/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

it('runs Friday directly with approvals on, in one local voice thread', async () => {
  const run = vi.fn(async () => [reply('a1', 'Yes, I am ready.')]);
  const app = fridayRunRoutes(fakePlatform(), run);
  const first = await (
    await post(app, { messages: [user('u1', 'ready?')] })
  ).json();
  const second = await (
    await post(app, { messages: [user('u2', 'again?')] })
  ).json();
  expect(first.newMessages[0].content).toBe('Yes, I am ready.');
  expect(second.threadId).toBe(first.threadId);
  const [, dotId, threadId, , options] = run.mock.calls[0] as unknown[];
  expect(dotId).toBe('f');
  expect(threadId).toBe(first.threadId);
  expect(options).toMatchObject({ approvals: true });
});

it('refuses bad input, and says so when Friday is paused', async () => {
  const run = vi.fn();
  expect((await post(fridayRunRoutes(fakePlatform(), run), {})).status).toBe(
    400,
  );
  const paused = await post(fridayRunRoutes(fakePlatform(true), run), {
    messages: [user('u1', 'hi')],
  });
  expect(paused.status).toBe(409);
  expect((await paused.json()).error).toContain('paused');
  expect(run).not.toHaveBeenCalled();
});

it('leaves a model’s reasoning notes out of the conversation, both ways', async () => {
  const notes = { id: 'r1', role: 'reasoning', content: 'The user wants…' };
  const run = vi.fn(async () => [
    notes as unknown as Message,
    reply('a1', 'Calculator is open.'),
  ]);
  const app = fridayRunRoutes(fakePlatform(), run);
  const first = await (
    await post(app, { messages: [user('u1', 'open calculator')] })
  ).json();
  expect(first.newMessages.map((m: Message) => m.role)).toEqual(['assistant']);
  // A history saved before this fix, still holding reasoning notes, works.
  const second = await post(app, {
    messages: [
      user('u1', 'open calculator'),
      notes,
      reply('a1', 'Calculator is open.'),
      user('u2', 'thanks'),
    ],
  });
  expect(second.status).toBe(200);
  const sent = run.mock.calls[1] as unknown as [
    unknown,
    unknown,
    unknown,
    Message[],
  ];
  expect(sent[3].map((m) => m.role)).toEqual(['user', 'assistant', 'user']);
});

it('answers an instant command with no model, and teaches the rest', async () => {
  const instant = {
    run: vi.fn(async (text: string) =>
      text === 'open notepad' ? { say: 'Notepad is open.' } : null,
    ),
    learn: vi.fn(async () => {}),
  };
  const run = vi.fn(async () => [
    {
      id: 'a1',
      role: 'assistant',
      content: '',
      toolCalls: [
        {
          id: 't1',
          type: 'function',
          function: {
            name: 'quick__snap_window',
            arguments: '{"window":"chrome","position":"left"}',
          },
        },
      ],
    } as unknown as Message,
    {
      id: 't1r',
      role: 'tool',
      toolCallId: 't1',
      content: JSON.stringify({ friday_say: 'Chrome is on the left.' }),
    } as unknown as Message,
  ]);
  const app = fridayRunRoutes(fakePlatform(), run, instant);
  const quick = await (
    await post(app, { messages: [user('u1', 'open notepad')] })
  ).json();
  expect(quick.newMessages.map((m: Message) => m.content)).toEqual([
    'Notepad is open.',
  ]);
  expect(run).not.toHaveBeenCalled();
  await post(app, { messages: [user('u2', 'chrome to the left please')] });
  expect(run).toHaveBeenCalledOnce();
  expect(instant.learn).toHaveBeenCalledWith(
    'chrome to the left please',
    [{ tool: 'snap_window', args: { window: 'chrome', position: 'left' } }],
    'Chrome is on the left.',
  );
});
