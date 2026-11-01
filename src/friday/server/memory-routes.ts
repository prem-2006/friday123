// Friday: the Admin page's memory graph (memory-graph.ts) and one note's text,
// read from the Obsidian vault Friday writes. Each note also gets an
// obsidian:// link, Obsidian's own way to open a note in the app.
import { Hono } from 'hono';
import type { ObsidianMemory } from './obsidian-memory.js';

export function fridayMemoryRoutes(memory: ObsidianMemory) {
  const app = new Hono();
  app.get('/friday/memory/graph', async (c) => {
    const graph = await memory.graph();
    return c.json({
      ...graph,
      obsidianUrl: `obsidian://open?path=${encodeURIComponent(graph.vault)}`,
    });
  });
  app.get('/friday/memory/note', async (c) => {
    const note = await memory.readNote(c.req.query('path') ?? '');
    if (!note) return c.json({ error: 'No such note in the vault.' }, 404);
    const { file, ...rest } = note;
    return c.json({
      ...rest,
      obsidianUrl: `obsidian://open?path=${encodeURIComponent(file)}`,
    });
  });
  return app;
}
