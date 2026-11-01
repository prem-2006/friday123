// Friday: run a Dot's turn right here on the server instead of through the
// hosted conversation service. That service adds several seconds per turn
// from India (about 4-6 s versus about 1.3 s direct), which is too slow for
// a voice assistant. The voice stage keeps its own short history and sends
// it with each turn; helper Dots get one task at a time and need none.
import { randomUUID } from 'node:crypto';
import type { Message } from '@ag-ui/core';
import { DotAgent } from '../../server/dot-agent.js';
import { currentTurnText } from '../../server/headless.js';
import type { Platform } from '../../server/platform.js';
import { connectionActionTool } from '../../shared/connection-types.js';

export type DirectPlatform = Pick<
  Platform,
  | 'store'
  | 'workspace'
  | 'config'
  | 'setupTelemetry'
  | 'delegate'
  | 'obsidianMemory'
>;

export async function runDotDirectly(
  platform: DirectPlatform,
  dotId: string,
  threadId: string,
  messages: Message[],
  {
    approvals = false,
    signal,
    onText,
  }: {
    approvals?: boolean;
    signal?: AbortSignal;
    /** Each piece of reply text as the model writes it (to speak it live). */
    onText?: (delta: string) => void;
  } = {},
): Promise<Message[]> {
  const agent = new DotAgent(
    platform.store,
    platform.workspace,
    platform.config,
    dotId,
    false,
    platform.setupTelemetry,
    platform.delegate,
    platform.obsidianMemory,
  );
  agent.threadId = threadId;
  agent.setMessages(messages);
  let runError: Error | undefined;
  const subscription = agent.subscribe({
    onRunErrorEvent: ({ event }) => {
      runError = new Error(event.message);
    },
    onTextMessageContentEvent: ({ event }) => {
      if (event.delta) onText?.(event.delta);
    },
  });
  const stop = () => agent.abortRun();
  signal?.addEventListener('abort', stop, { once: true });
  try {
    const result = await agent.runAgent({
      runId: randomUUID(),
      // The approval tool lets a WhatsApp send or call wait for the owner.
      tools: approvals ? [connectionActionTool] : [],
    });
    if (runError) throw runError;
    signal?.throwIfAborted();
    return result.newMessages;
  } finally {
    signal?.removeEventListener('abort', stop);
    subscription.unsubscribe();
  }
}

/** A conversation the app knows locally, created without any remote call. */
export function localThread(
  platform: Pick<DirectPlatform, 'workspace'>,
  dotId: string,
  title: string,
) {
  return (
    platform.workspace
      .conversations()
      .find((item) => item.dotId === dotId && item.title === title) ??
    platform.workspace.bindThread(randomUUID(), dotId, title)
  );
}

/** One task for a helper Dot; returns its final report. */
export async function runHelperTask(
  platform: DirectPlatform,
  helperId: string,
  threadTitle: string,
  task: string,
  signal: AbortSignal,
) {
  const thread = localThread(platform, helperId, threadTitle);
  const messages = await runDotDirectly(
    platform,
    helperId,
    thread.id,
    [{ id: randomUUID(), role: 'user', content: task }],
    { signal },
  );
  return currentTurnText(messages);
}
