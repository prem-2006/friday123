import {
  mkdir,
  readFile,
  readdir,
  stat,
  unlink,
  writeFile,
} from 'node:fs/promises';
import {
  basename,
  extname,
  isAbsolute,
  join,
  relative,
  resolve,
} from 'node:path';
import type { Memory } from '../../shared/types.js';
import { buildGraph, topicLinks, type MemoryGraph } from './memory-graph.js';

export interface ObsidianRecall {
  path: string;
  title: string;
  content: string;
}

interface IndexedNote extends ObsidianRecall {
  absolutePath: string;
  modified: number;
  memory: boolean;
}

// Notes read per search: saved memories first, then the newest notes, so a
// vault that grows by dozens of turns a day never pushes out recent ones.
const MAX_NOTES = 600;
const MAX_FILES_SCANNED = 20_000;
// Friday's own labels in its conversation notes are not what they are about:
// without this, "Friday" or "owner" in a question matches every note.
const NOTE_LABELS = /^# Friday conversation · .*$|\*\*(?:Owner|Friday):\*\*/gmu;
const escapeRegExp = (text: string) =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const MAX_NOTE_BYTES = 64_000;
const NOTE_CACHE_MS = 10_000;
const SKIP_DIRECTORIES = new Set([
  '.obsidian',
  '.trash',
  '.git',
  'node_modules',
  'dist',
  'build',
]);
const STOP_WORDS = new Set([
  'a',
  'about',
  'after',
  'again',
  'all',
  'also',
  'am',
  'an',
  'and',
  'any',
  'are',
  'as',
  'at',
  'be',
  'because',
  'before',
  'been',
  'being',
  'but',
  'by',
  'can',
  'could',
  'did',
  'do',
  'does',
  'for',
  'from',
  'had',
  'has',
  'have',
  'he',
  'her',
  'here',
  'him',
  'his',
  'how',
  'i',
  'if',
  'in',
  'into',
  'is',
  'it',
  'its',
  'just',
  'me',
  'my',
  'no',
  'not',
  'of',
  'on',
  'or',
  'our',
  'please',
  'she',
  'so',
  'tell',
  'that',
  'the',
  'their',
  'them',
  'there',
  'these',
  'they',
  'this',
  'those',
  'to',
  'up',
  'us',
  'was',
  'we',
  'were',
  'what',
  'when',
  'where',
  'which',
  'who',
  'why',
  'will',
  'with',
  'would',
  'you',
  'your',
]);

/**
 * Friday stores conversation notes as ordinary Markdown in an Obsidian vault.
 * The owner may point this at an existing vault; the default folder is itself
 * a valid vault that can be opened directly in Obsidian.
 */
export class ObsidianMemory {
  private cache?: { expiresAt: number; root: string; notes: IndexedNote[] };

  constructor(
    private readonly configuredPath: () => string,
    /** Contact names, so notes link to the people they mention. */
    private readonly people: () => string[] = () => [],
  ) {}

  private peopleOrNone() {
    try {
      return this.people();
    } catch {
      return []; // no contacts file yet: topics alone
    }
  }

  /** "Related: [[Calls]] · [[Prince]]": Obsidian draws these as links. */
  private related(text: string, extra: string[] = []) {
    const names = [
      ...new Set([...extra, ...topicLinks(text, this.peopleOrNone())]),
    ];
    return names.length
      ? ['', `Related: ${names.map((name) => `[[${name}]]`).join(' · ')}`]
      : [];
  }

  /** Every note as Obsidian's graph view would draw it. */
  async graph(): Promise<MemoryGraph & { vault: string }> {
    return {
      vault: this.vaultPath(),
      ...buildGraph(await this.index(), this.peopleOrNone()),
    };
  }

  /** One note's text, by its path inside the vault (never outside it). */
  async readNote(path: string) {
    const root = this.vaultPath();
    const file = resolve(root, path);
    const inside = relative(root, file);
    if (
      !inside ||
      inside.startsWith('..') ||
      isAbsolute(inside) ||
      extname(file).toLocaleLowerCase() !== '.md'
    )
      return undefined;
    try {
      const markdown = await readFile(file, 'utf8');
      return {
        path: inside.replaceAll('\\', '/'),
        title: basename(file).replace(/\.md$/iu, ''),
        content: markdown.replace(/^---\s*[\s\S]*?\n---\s*/u, '').trim(),
        file,
      };
    } catch {
      return undefined;
    }
  }

  vaultPath(pathOverride?: string) {
    const configured = (pathOverride ?? this.configuredPath()).trim();
    return resolve(configured || 'data/friday-obsidian');
  }

  async prepareVault(pathOverride?: string) {
    const root = this.vaultPath(pathOverride);
    await mkdir(join(root, '.obsidian'), { recursive: true });
    return root;
  }

  async saveMemory(memory: Memory, pathOverride?: string) {
    const root = this.vaultPath(pathOverride);
    const id = memory.id.replace(/[^a-zA-Z0-9_-]/g, '');
    if (!id) throw new Error('Invalid memory identifier.');
    const folder = join(root, 'Friday', 'Memories');
    await mkdir(folder, { recursive: true });
    const note = [
      '---',
      `id: ${id}`,
      `created: ${new Date(memory.createdAt).toISOString()}`,
      'tags:',
      '  - friday/memory',
      '---',
      '',
      '# Saved memory',
      '',
      memory.text.trim(),
      ...this.related(memory.text),
      '',
    ].join('\n');
    await writeFile(join(folder, `${id}.md`), note, 'utf8');
    this.cache = undefined;
  }

  /** Mail's latest conversation map (who is waiting on whom), as one note
   * kept up to date, plus a copy for the day. */
  async saveMailMap(task: string, report: string) {
    const root = this.vaultPath();
    const now = Date.now();
    const { date, time } = this.localDateTime(now);
    const folder = join(root, 'Friday', 'Mail');
    await mkdir(folder, { recursive: true });
    const note = [
      '---',
      `updated: ${new Date(now).toISOString()}`,
      'tags:',
      '  - friday/mail',
      '---',
      '',
      `# Gmail conversation map · ${date} ${time}`,
      '',
      `Asked: ${task.trim()}`,
      '',
      report.trim(),
      ...this.related(report, ['Email', date]),
      '',
    ].join('\n');
    await writeFile(join(folder, 'Conversation map.md'), note, 'utf8');
    await writeFile(join(folder, `${date}.md`), note, 'utf8');
    this.cache = undefined;
  }

  async deleteMemory(id: string) {
    const safeId = id.replace(/[^a-zA-Z0-9_-]/g, '');
    if (!safeId) return;
    const file = join(this.vaultPath(), 'Friday', 'Memories', `${safeId}.md`);
    try {
      await unlink(file);
      this.cache = undefined;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
  }

  async saveConversation(input: {
    id: string;
    createdAt: number;
    userText: string;
    assistantText: string;
  }) {
    const root = this.vaultPath();
    const { date, time } = this.localDateTime(input.createdAt);
    const id = input.id.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 100);
    const safeId = id || 'turn';
    const folder = join(root, 'Friday', 'Conversations', date);
    await mkdir(folder, { recursive: true });
    const note = [
      '---',
      `date: ${date}`,
      `created: ${new Date(input.createdAt).toISOString()}`,
      `turn: ${safeId}`,
      'tags:',
      '  - friday/conversation',
      '---',
      '',
      `# Friday conversation · ${time}`,
      '',
      `**Owner:** ${input.userText.trim()}`,
      '',
      `**Friday:** ${input.assistantText.trim()}`,
      ...this.related(`${input.userText}\n${input.assistantText}`, [date]),
      '',
    ].join('\n');
    await writeFile(join(folder, `${safeId}.md`), note, 'utf8');
    this.cache = undefined;
  }

  async search(query: string): Promise<ObsidianRecall[]> {
    const terms = [
      ...new Set(
        (query.toLocaleLowerCase().match(/[\p{L}\p{N}]{2,}/gu) ?? []).filter(
          (term) => !STOP_WORDS.has(term),
        ),
      ),
    ];
    if (!terms.length) return [];
    const notes = await this.index();
    // Whole words or their start ("call" finds "calling"), never the middle
    // of a word ("ai" must not find "said").
    const patterns = terms.map(
      (term) => new RegExp(`(?<![\\p{L}\\p{N}])${escapeRegExp(term)}`, 'u'),
    );
    return (
      notes
        .map((note) => {
          const haystack = `${note.title}\n${note.content}`
            .replace(NOTE_LABELS, ' ')
            .toLocaleLowerCase();
          const matches = terms
            .map((term, index) => ({
              term,
              found: patterns[index].test(haystack),
            }))
            .filter((match) => match.found);
          if (!matches.length) return undefined;
          const contentLower = note.content.toLocaleLowerCase();
          const firstContentMatch = matches
            .map((match) => contentLower.indexOf(match.term))
            .filter((index) => index >= 0)
            .sort((a, b) => a - b)[0];
          const start = Math.max(0, (firstContentMatch ?? 0) - 300);
          const end = Math.min(note.content.length, start + 1400);
          const excerpt = `${start ? '…' : ''}${note.content.slice(start, end).trim()}${end < note.content.length ? '…' : ''}`;
          return {
            path: note.path,
            title: note.title,
            content: excerpt,
            // A saved memory outranks a conversation with the same matches.
            score: matches.length + (note.memory ? 0.5 : 0),
            modified: note.modified,
          };
        })
        .filter((note): note is NonNullable<typeof note> => !!note)
        // Most matching words first; among equals, the newest.
        .sort((a, b) => b.score - a.score || b.modified - a.modified)
        .slice(0, 4)
        .map(({ path, title, content }) => ({ path, title, content }))
    );
  }

  private localDateTime(timestamp: number) {
    const timeZone = process.env.TIMEZONE || 'Asia/Kolkata';
    const date = new Date(timestamp);
    const partsFor = (zone: string) =>
      new Intl.DateTimeFormat('en', {
        timeZone: zone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).formatToParts(date);
    let parts: Intl.DateTimeFormatPart[];
    try {
      parts = partsFor(timeZone);
    } catch {
      parts = partsFor('UTC');
    }
    const value = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((part) => part.type === type)?.value ?? '00';
    let time: string;
    try {
      time = new Intl.DateTimeFormat('en-IN', {
        timeZone,
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
    } catch {
      time = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'UTC',
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
    }
    return {
      date: `${value('year')}-${value('month')}-${value('day')}`,
      time,
    };
  }

  private async index() {
    const root = this.vaultPath();
    if (
      this.cache &&
      this.cache.root === root &&
      this.cache.expiresAt > Date.now()
    )
      return this.cache.notes;
    const files: { absolutePath: string; modified: number; memory: boolean }[] =
      [];
    const memories = join(root, 'Friday', 'Memories');
    const visit = async (directory: string, depth: number): Promise<void> => {
      if (depth > 8 || files.length >= MAX_FILES_SCANNED) return;
      let entries;
      try {
        entries = await readdir(directory, { withFileTypes: true });
      } catch {
        return;
      }
      for (const entry of entries) {
        if (files.length >= MAX_FILES_SCANNED) return;
        if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue;
        const absolutePath = join(directory, entry.name);
        if (entry.isDirectory()) {
          if (!SKIP_DIRECTORIES.has(entry.name.toLocaleLowerCase()))
            await visit(absolutePath, depth + 1);
          continue;
        }
        if (
          !entry.isFile() ||
          extname(entry.name).toLocaleLowerCase() !== '.md'
        )
          continue;
        try {
          const info = await stat(absolutePath);
          if (info.size > MAX_NOTE_BYTES) continue;
          files.push({
            absolutePath,
            modified: info.mtimeMs,
            memory: directory === memories,
          });
        } catch {
          // A locked or changing note should not stop Friday from answering.
        }
      }
    };
    await visit(root, 0);
    files.sort(
      (a, b) => Number(b.memory) - Number(a.memory) || b.modified - a.modified,
    );
    const notes: IndexedNote[] = [];
    for (const file of files.slice(0, MAX_NOTES)) {
      try {
        const markdown = await readFile(file.absolutePath, 'utf8');
        notes.push({
          ...file,
          path: relative(root, file.absolutePath).replaceAll('\\', '/'),
          title: basename(file.absolutePath).replace(/\.md$/iu, ''),
          content: markdown.replace(/^---\s*[\s\S]*?\n---\s*/u, '').trim(),
        });
      } catch {
        // A locked or changing note should not stop Friday from answering.
      }
    }
    this.cache = { expiresAt: Date.now() + NOTE_CACHE_MS, root, notes };
    return notes;
  }
}
