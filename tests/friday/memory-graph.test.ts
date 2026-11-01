import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  buildGraph,
  topicLinks,
  type GraphNote,
} from '../../src/friday/server/memory-graph.js';
import { ObsidianMemory } from '../../src/friday/server/obsidian-memory.js';

const conversation = (day: string, id: string, owner: string): GraphNote => ({
  path: `Friday/Conversations/${day}/${id}.md`,
  title: id,
  content: `# Friday conversation · 08:27 pm\n\n**Owner:** ${owner}\n\n**Friday:** Done.`,
  memory: false,
  modified: 1,
});

describe('topicLinks', () => {
  it('finds people and topics, in Hinglish too, but not inside words', () => {
    expect(topicLinks('Prince ko phone karo', ['Prince', 'Didi'])).toEqual([
      'Prince',
      'Calls',
    ]);
    expect(topicLinks('arijit ka gaana bajao', [])).toEqual([
      'Music & YouTube',
    ]);
    expect(topicLinks('Princess Diaries', ['Prince'])).toEqual([]);
  });
});

describe('buildGraph', () => {
  it('joins notes through people, topics, days and [[links]]', () => {
    const graph = buildGraph(
      [
        conversation('2026-10-27', 'a', 'Prince ko phone karo'),
        conversation('2026-10-27', 'b', 'call Prince again'),
        {
          path: 'Friday/Memories/m1.md',
          title: 'm1',
          content: '# Saved memory\n\nPrince likes cricket. See [[Cricket]].',
          memory: true,
          modified: 1,
        },
      ],
      ['Prince'],
    );
    const ids = graph.nodes.map((n) => n.id).sort();
    expect(ids).toEqual(
      [
        'day:2026-10-27',
        'note:Friday/Conversations/2026-10-27/a.md',
        'note:Friday/Conversations/2026-10-27/b.md',
        'note:Friday/Memories/m1.md',
        'person:prince',
        'topic:calls',
        'topic:cricket',
      ].sort(),
    );
    const prince = graph.nodes.find((n) => n.id === 'person:prince')!;
    expect(prince.degree).toBe(3);
    expect(graph.nodes.find((n) => n.kind === 'memory')!.label).toBe(
      'Prince likes cricket. See [[Cricket]].',
    );
    expect(graph.nodes.find((n) => n.path?.endsWith('/a.md'))!.label).toBe(
      'Prince ko phone karo',
    );
  });
});

describe('ObsidianMemory notes and the graph', () => {
  let folder = '';
  afterEach(async () => {
    if (folder) await rm(folder, { recursive: true, force: true });
  });

  it('writes [[links]] into new notes and never reads outside the vault', async () => {
    folder = await mkdtemp(join(tmpdir(), 'friday-vault-'));
    const vault = new ObsidianMemory(
      () => folder,
      () => ['Priya'],
    );
    await vault.saveConversation({
      id: 't1',
      createdAt: Date.UTC(2026, 9, 27, 10),
      userText: 'WhatsApp Priya that I am late',
      assistantText: 'Sent.',
    });
    const written = await readFile(
      join(folder, 'Friday', 'Conversations', '2026-10-27', 't1.md'),
      'utf8',
    );
    expect(written).toContain(
      'Related: [[2026-10-27]] · [[Priya]] · [[WhatsApp]]',
    );
    const graph = await vault.graph();
    expect(graph.nodes.some((n) => n.id === 'person:priya')).toBe(true);
    const note = await vault.readNote('Friday/Conversations/2026-10-27/t1.md');
    expect(note?.content).toContain('**Owner:** WhatsApp Priya');
    expect(await vault.readNote('../outside.md')).toBeUndefined();
    expect(await vault.readNote('Friday/x.txt')).toBeUndefined();
  });
});

describe('Mail’s conversation map', () => {
  let folder = '';
  afterEach(async () => {
    if (folder) await rm(folder, { recursive: true, force: true });
  });

  it('is kept as one up-to-date note, plus a copy for the day', async () => {
    folder = await mkdtemp(join(tmpdir(), 'friday-mail-'));
    const vault = new ObsidianMemory(
      () => folder,
      () => ['Ravi'],
    );
    await vault.saveMailMap(
      'what is pending in my email',
      'Waiting on you:\n- Ravi - Project plan - wants your yes - since Monday',
    );
    const latest = await readFile(
      join(folder, 'Friday', 'Mail', 'Conversation map.md'),
      'utf8',
    );
    expect(latest).toContain('Asked: what is pending in my email');
    expect(latest).toContain('Waiting on you:');
    expect(latest).toContain('[[Ravi]]');
    const notes = await vault.search('pending Ravi project');
    expect(notes[0].path).toMatch(/^Friday\/Mail\//);
  });
});
