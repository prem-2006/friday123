import { describe, expect, it, vi } from 'vitest';
import { instantClient, turnCalls } from '../../src/friday/server/instant.js';

const said = (text: string) => JSON.stringify({ friday_say: text });

describe('turnCalls', () => {
  it('collects the quick tool calls of a turn that worked', () => {
    const calls = turnCalls([
      { role: 'user', content: 'chrome on the left' },
      {
        role: 'assistant',
        toolCalls: [
          {
            id: 'a',
            function: {
              name: 'quick__snap_window',
              arguments: '{"window":"chrome","position":"left"}',
            },
          },
        ],
      },
      { role: 'tool', toolCallId: 'a', content: said('Chrome is on the left.') },
      { role: 'assistant', content: 'Chrome is on the left.' },
    ]);
    expect(calls).toEqual([
      {
        tool: 'snap_window',
        args: { window: 'chrome', position: 'left' },
      },
    ]);
  });

  it('learns nothing when a step failed or had no answer', () => {
    const call = {
      role: 'assistant',
      toolCalls: [
        { id: 'a', function: { name: 'quick__open_app', arguments: '{}' } },
      ],
    };
    expect(turnCalls([call])).toEqual([]);
    expect(
      turnCalls([
        call,
        {
          role: 'tool',
          toolCallId: 'a',
          content: JSON.stringify({ isError: true, text: 'No app' }),
        },
      ]),
    ).toEqual([]);
  });

  it('keeps other tools’ full names so the services refuse them', () => {
    const calls = turnCalls([
      {
        role: 'assistant',
        toolCalls: [
          {
            id: 'w',
            function: { name: 'whatsapp__whatsapp_call', arguments: '{}' },
          },
        ],
      },
      { role: 'tool', toolCallId: 'w', content: said('Calling.') },
    ]);
    expect(calls[0].tool).toBe('whatsapp__whatsapp_call');
  });
});

describe('instantClient', () => {
  it('is off without the services token', () => {
    expect(instantClient({ servicesUrl: 'http://x' })).toBeUndefined();
  });

  it('returns what the services said, or null to let the model answer', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ handled: true, say: 'Notepad is open.' }),
      )
      .mockResolvedValueOnce(Response.json({ handled: false }))
      .mockRejectedValueOnce(new Error('down'));
    const client = instantClient({
      servicesUrl: 'http://services/',
      token: 'secret',
      fetch,
    })!;
    expect(await client.run('open notepad')).toEqual({
      say: 'Notepad is open.',
    });
    expect(await client.run('what should I eat')).toBeNull();
    expect(await client.run('open notepad')).toBeNull();
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('http://services/instant');
    expect(init.headers.Authorization).toBe('Bearer secret');
  });
});
