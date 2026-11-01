// Friday: what Friday is allowed to do, as switches on the main page. Each
// ability maps to the Dots' connected tools; switching it off disables those
// tools in OpenDots, so no Dot can use them until it is switched back on.
import type { Connection } from '../../../shared/connection-types';

interface ToolGroup {
  dot: string;
  connection: string;
  /** Only these tools of the connection; all of them when absent. */
  names?: string[];
}

export interface Ability {
  id: string;
  title: string;
  detail: string;
  /** Friday asks the owner before every use, whatever the switch says. */
  alwaysAsks?: boolean;
  tools: ToolGroup[];
}

export const ABILITIES: Ability[] = [
  {
    id: 'web',
    title: 'Use the web browser',
    detail:
      'Search, open sites and work in your Google account in Friday’s Chrome window.',
    tools: [
      { dot: 'Navigator', connection: 'browser' },
      { dot: 'Coder', connection: 'browser' },
      {
        dot: 'Friday',
        connection: 'quick',
        names: [
          'open_website',
          'play_youtube',
          'web_search',
          'play_shown_video',
          'weather',
          'directions',
        ],
      },
    ],
  },
  {
    id: 'apps',
    title: 'Use desktop apps',
    detail: 'Open apps, type into them and arrange windows.',
    tools: [
      { dot: 'Operator', connection: 'desktop' },
      {
        dot: 'Friday',
        connection: 'quick',
        names: [
          'open_app',
          'close_app',
          'write_in_app',
          'clear_in_app',
          'press_keys_in_app',
          'snap_window',
          'media_control',
          'run_mode',
          'focus_session',
          'take_screenshot',
          'lock_screen',
        ],
      },
    ],
  },
  {
    id: 'screen',
    title: 'Look at my screen',
    detail:
      'Describe what is on screen when you ask (the screenshot goes to your AI model), and watch a window to tell you when something is done (read on this PC only).',
    tools: [
      {
        dot: 'Friday',
        connection: 'quick',
        names: ['look_at_screen', 'watch_window', 'stop_watching'],
      },
    ],
  },
  {
    id: 'files',
    title: 'Find and open my files',
    detail:
      'Search your Desktop, Documents, Downloads, Pictures, Music and Videos by name. Programs are never opened.',
    tools: [
      {
        dot: 'Friday',
        connection: 'quick',
        names: ['find_files', 'open_file'],
      },
    ],
  },
  {
    id: 'meetings',
    title: 'Schedule meetings',
    detail: 'Add events to your Google Calendar and invite people.',
    alwaysAsks: true,
    tools: [
      { dot: 'Friday', connection: 'quick', names: ['schedule_meeting'] },
    ],
  },
  {
    id: 'calls',
    title: 'Call my phone',
    detail:
      'Friday rings your mobile for deadlines and when you ask, at most once in 10 minutes.',
    tools: [{ dot: 'Friday', connection: 'quick', names: ['call_me'] }],
  },
  {
    id: 'email',
    title: 'Send email',
    detail: 'Send drafts from your Gmail.',
    alwaysAsks: true,
    tools: [
      {
        dot: 'Friday',
        connection: 'quick',
        names: ['draft_email', 'send_email'],
      },
    ],
  },
  {
    id: 'whatsapp',
    title: 'WhatsApp messages and calls',
    detail: 'Message or call your contacts from WhatsApp Desktop.',
    alwaysAsks: true,
    tools: [{ dot: 'Friday', connection: 'whatsapp' }],
  },
];

export interface ToolRef {
  connectionId: string;
  name: string;
  enabled: boolean;
}

export type AbilityState = 'on' | 'off' | 'mixed' | 'unavailable';

/** The tools behind one ability, from each Dot's connections. */
export function toolsOf(
  ability: Ability,
  connectionsByDot: Record<string, Connection[]>,
): ToolRef[] {
  return ability.tools.flatMap((group) =>
    (connectionsByDot[group.dot] ?? [])
      .filter((connection) => connection.name === group.connection)
      .flatMap((connection) =>
        connection.tools
          .filter((tool) => !group.names || group.names.includes(tool.name))
          .map((tool) => ({
            connectionId: connection.id,
            name: tool.name,
            enabled: tool.enabled,
          })),
      ),
  );
}

export function stateOf(tools: ToolRef[]): AbilityState {
  if (!tools.length) return 'unavailable';
  const on = tools.filter((tool) => tool.enabled).length;
  return on === tools.length ? 'on' : on === 0 ? 'off' : 'mixed';
}

/** Turns every tool of an ability on or off; returns how many changed. */
export async function setAbility(
  tools: ToolRef[],
  on: boolean,
  patch: (
    connectionId: string,
    tool: string,
    enabled: boolean,
  ) => Promise<void>,
) {
  const changing = tools.filter((tool) => tool.enabled !== on);
  for (const tool of changing) await patch(tool.connectionId, tool.name, on);
  return changing.length;
}
