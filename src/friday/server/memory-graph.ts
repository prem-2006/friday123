// Friday: the memory graph, like Obsidian's graph view. Every note in the
// vault is a dot; lines join it to the people it mentions (from Friday's
// contacts), the topics it is about, its day, and anything it [[links]] to.
// The same topics are written into new notes as [[links]] (topicLinks), so
// Obsidian's own graph shows the same connections.

export interface GraphNote {
  path: string;
  title: string;
  content: string;
  memory: boolean;
  modified: number;
}

export type NodeKind =
  'memory' | 'conversation' | 'note' | 'person' | 'topic' | 'day';

export interface GraphNode {
  id: string;
  label: string;
  kind: NodeKind;
  /** The note's path in the vault (notes only). */
  path?: string;
  /** How many lines meet here. */
  degree: number;
}

export interface MemoryGraph {
  nodes: GraphNode[];
  links: { source: string; target: string }[];
}

// Topic -> what says a note is about it, in English, Hindi or Hinglish.
const TOPICS: [string, RegExp][] = [
  ['Calls', /\b(call|calls|calling|phone|ring|dial|baat karo)\w*/iu],
  ['WhatsApp', /\b(whatsapp|message|msg|text him|text her)\b/iu],
  [
    'Music & YouTube',
    /\b(youtube|song|songs|gaana|gaane|music|bajao|playlist)\b/iu,
  ],
  ['Reminders', /\b(remind|reminder|timer|alarm|deadline|yaad)\w*/iu],
  ['Email', /\b(e-?mail|gmail|inbox)\b/iu],
  ['Meetings & calendar', /\b(meeting|calendar|schedule|appointment)\w*/iu],
  ['Coding', /\b(leetcode|hackerrank|coding|code|python|javascript|bug)\b/iu],
  ['Web search', /\b(search|google|website|browser)\b/iu],
  [
    'Apps & windows',
    /\b(notepad|calculator|chrome|vs code|window|kholo|open)\b/iu,
  ],
  ['Weather', /\b(weather|mausam|rain|baarish)\b/iu],
  ['Volume', /\b(volume|awaaz|mute|louder)\b/iu],
  ['Files', /\b(file|files|folder|pdf|document)\b/iu],
  ['Notes', /\b(note|notes|likh|write down)\b/iu],
  ['Screen', /\b(screen|screenshot)\b/iu],
];
// Friday's own labels in conversation notes are not what they are about.
const LABELS = /^# Friday conversation · .*$|\*\*(?:Owner|Friday):\*\*/gmu;
const WIKILINK = /\[\[([^\]|#\n]{1,80})(?:[#|][^\]]*)?\]\]/gu;
const DAY = /^\d{4}-\d{2}-\d{2}$/u;
const MAX_LABEL = 48;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** The people and topics a text is about, as link names. */
export function topicLinks(text: string, people: string[]): string[] {
  const plain = text.replace(LABELS, ' ');
  const found = people.filter(
    (name) =>
      name.trim().length > 1 &&
      new RegExp(
        `(?<![\\p{L}\\p{N}])${escape(name.trim())}(?![\\p{L}\\p{N}])`,
        'iu',
      ).test(plain),
  );
  for (const [topic, pattern] of TOPICS)
    if (pattern.test(plain)) found.push(topic);
  return [...new Set(found)];
}

function short(text: string) {
  const line = text.replace(/\s+/gu, ' ').trim();
  return line.length > MAX_LABEL ? `${line.slice(0, MAX_LABEL - 1)}…` : line;
}

function labelOf(note: GraphNote) {
  const owner = /\*\*Owner:\*\*\s*(.+)/u.exec(note.content)?.[1];
  if (owner) return short(owner);
  const body = note.content
    .split('\n')
    .map((line) => line.trim())
    .find(
      (line) => line && !line.startsWith('#') && !line.startsWith('Related:'),
    );
  return short(note.memory ? (body ?? note.title) : note.title);
}

export function buildGraph(notes: GraphNote[], people: string[]): MemoryGraph {
  const nodes = new Map<string, GraphNode>();
  const links = new Map<string, { source: string; target: string }>();
  const personNames = new Map(people.map((name) => [name.toLowerCase(), name]));
  const byTitle = new Map(
    notes.map((note) => [note.title.toLowerCase(), note]),
  );
  const hub = (name: string): string => {
    const note = byTitle.get(name.toLowerCase());
    if (note) return `note:${note.path}`;
    const person = personNames.get(name.toLowerCase());
    const kind: NodeKind = person ? 'person' : DAY.test(name) ? 'day' : 'topic';
    const id = `${kind}:${(person ?? name).toLowerCase()}`;
    if (!nodes.has(id))
      nodes.set(id, { id, label: person ?? name, kind, degree: 0 });
    return id;
  };
  const link = (source: string, target: string) => {
    if (source === target) return;
    const key = [source, target].sort().join('\n');
    if (!links.has(key)) links.set(key, { source, target });
  };
  for (const note of notes) {
    const conversation = note.path.startsWith('Friday/Conversations/');
    nodes.set(`note:${note.path}`, {
      id: `note:${note.path}`,
      label: labelOf(note),
      kind: note.memory ? 'memory' : conversation ? 'conversation' : 'note',
      path: note.path,
      degree: 0,
    });
  }
  for (const note of notes) {
    const id = `note:${note.path}`;
    const day = /^Friday\/Conversations\/(\d{4}-\d{2}-\d{2})\//u.exec(
      note.path,
    )?.[1];
    if (day) link(id, hub(day));
    for (const name of topicLinks(note.content, people)) link(id, hub(name));
    for (const match of note.content.matchAll(WIKILINK))
      link(id, hub(match[1].trim()));
  }
  for (const { source, target } of links.values()) {
    nodes.get(source)!.degree++;
    nodes.get(target)!.degree++;
  }
  return { nodes: [...nodes.values()], links: [...links.values()] };
}
