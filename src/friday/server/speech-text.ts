// Friday: turn a chat reply into text that sounds right when Timbre reads it.
export const MAX_SPOKEN_CHARS = 700;

const SITE_NAMES: Record<string, string> = {
  'mail.google.com': 'Gmail',
  'gmail.com': 'Gmail',
  'calendar.google.com': 'Google Calendar',
  'drive.google.com': 'Google Drive',
  'docs.google.com': 'Google Docs',
  'maps.google.com': 'Google Maps',
  'youtube.com': 'YouTube',
  'music.youtube.com': 'YouTube Music',
  'web.whatsapp.com': 'WhatsApp',
  'linkedin.com': 'LinkedIn',
  'github.com': 'GitHub',
  'chatgpt.com': 'ChatGPT',
  'irctc.co.in': 'IRCTC',
};

/** "https://mail.google.com/mail/u/0" -> "Gmail"; "example.co.in" -> "Example". */
export function siteName(address: string) {
  const host = address
    .replace(/^https?:\/\//i, '')
    .replace(/[/?#:].*$/, '')
    .toLowerCase()
    .replace(/^(www|m)\./, '');
  const known = SITE_NAMES[host];
  if (known) return known;
  const parts = host.split('.');
  const main =
    parts.length >= 3 &&
    ['co', 'com', 'org', 'gov', 'ac'].includes(parts.at(-2)!)
      ? parts.at(-3)!
      : (parts.at(-2) ?? parts[0]!);
  return main.length <= 4
    ? main.toUpperCase()
    : main.charAt(0).toUpperCase() + main.slice(1);
}

// A full address, or a bare one like mail.google.com (not an email address or a file).
const ADDRESS =
  /https?:\/\/\S+|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|in|org|net|io|ai|app|dev|co)(?:\.[a-z]{2})?(?:\/\S*)?(?![\w@-])/gi;

export function speakableText(markdown: string, limit = MAX_SPOKEN_CHARS) {
  const text = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(ADDRESS, (address) => {
      const url = address.replace(/[.,;:!?)…]+$/, '');
      return siteName(url) + address.slice(url.length);
    })
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\|/g, ' ')
    .replace(/(\*\*|__|\*|_|~~)(\S(?:.*?\S)?)\1/g, '$2')
    .replace(/[ \t]+/g, ' ')
    .replace(/\s*\n+\s*/g, '. ')
    .replace(/([.!?])\.\s/g, '$1 ')
    .replace(/\s+([.,!?])/g, '$1')
    .trim();
  if (text.length <= limit) return text;
  // Stop at the last full sentence that fits.
  const cut = text.slice(0, limit);
  const end = Math.max(
    cut.lastIndexOf('. '),
    cut.lastIndexOf('? '),
    cut.lastIndexOf('! '),
  );
  return end > limit / 3 ? cut.slice(0, end + 1) : `${cut.trimEnd()}…`;
}
