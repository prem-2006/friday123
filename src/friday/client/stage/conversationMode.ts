// Friday: always-on conversation on the notch. Friday hears everything and
// answers what the owner says, until told to be quiet ("wait", "kuch mat bolo
// abhi"); then it keeps listening but answers nothing until called back
// ("suno Friday, ek kaam hai", "ab baat karte hain Friday"). Pure text rules,
// so they can be tested.

// How Gnani may write these words in Hindi mode, or mishear "hey Friday".
const SPELLINGS: [RegExp, string][] = [
  [/फ्राइडे|फ्रायडे|फ़्राइडे/gu, 'friday'],
  [/स्टॉप|स्टाप/gu, 'stop'],
  [/(^| )(हे|हाय)(?= friday)/gu, '$1hey'],
  // Only first: "kal hai friday" is not a call.
  [/^(a|ae|he|hai|hay|pay)(?= friday( |$))/u, 'hey'],
];

function normalise(text: string) {
  return SPELLINGS.reduce(
    (said, [pattern, word]) => said.replace(pattern, word),
    text
      .toLowerCase()
      .replace(/[.,!?।"'’]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim(),
  );
}

// "Be quiet for now" (not "cancel the task": that is still "stop").
const QUIET = [
  'wait',
  'wait friday',
  'ruko',
  'ruk jao',
  'ruko friday',
  'kuch mat bolo',
  'kuch mat bolo abhi',
  'abhi kuch mat bolo',
  'mat bolo',
  'chup',
  'chup raho',
  'chup ho jao',
  'chup karo',
  'abhi nahi',
  'abhi mat bolo',
  'baad mein',
  'baad me baat karte hai',
  'be quiet',
  'stay quiet',
  'stop talking',
  'shut up',
  'quiet',
  'silence',
  'shh',
  'not now',
  'later',
  'रुको',
  'चुप',
  'कुछ मत बोलो',
];

// Calling Friday back. Anything said after these is the request itself.
const RESUME = [
  'suno friday',
  'friday suno',
  'sun rahi ho friday',
  'sun rahe ho friday',
  'friday sun rahi ho',
  'friday sun rahe ho',
  'ab baat karte hai friday',
  'ab baat karte hain friday',
  'friday ab baat karte hai',
  'friday ab baat karte hain',
  'ab baat karte hai',
  'ab baat karte hain',
  'baat karte hai friday',
  'baat karte hain friday',
  'hey friday',
  'hi friday',
  'ok friday',
  'okay friday',
  'listen friday',
  'friday listen',
  'are you there friday',
  'friday are you there',
  'friday',
];
// Words that only lead into the request.
const LEAD_IN =
  /^(ek kaam hai|ek kam hai|ek baat hai|mujhe|please|zara|na|ki)\b\s*/u;

/** "Wait", "kuch mat bolo abhi": go quiet. Only short sentences count, so
 * "wait for the download and tell me" stays a request. */
export function isQuietCommand(text: string): boolean {
  const said = normalise(text);
  if (!said || said.split(' ').length > 6) return false;
  return QUIET.some(
    (phrase) =>
      said === phrase ||
      said.startsWith(`${phrase} `) ||
      said.endsWith(` ${phrase}`),
  );
}

// "Stop": cancel everything and stop listening until called back. Whole
// sentences only, so "stop the song" stays a request.
const SLEEP = [
  'stop',
  'stop now',
  'stop it',
  'stop friday',
  'friday stop',
  'friday stop now',
  'stop now friday',
  'stop listening',
  'stop everything',
  'stop stop',
  'please stop',
  'stopped',
  'stop ho ja',
  'stop ho jao',
  'disable',
  'off',
  'turn off',
  'switch off',
  'band ho ja',
  'band ho jao',
  'bas',
  'bas karo',
  'bas friday',
  'बस',
  'बस करो',
];

// Said around a stop without changing it: "ok stop", "stop yaar", "Friday,
// please stop now".
const SLEEP_FILLER = new Set([
  'ok',
  'okay',
  'please',
  'friday',
  'hey',
  'yaar',
  'ji',
  'now',
  'abhi',
  'just',
  'bhai',
  'arey',
  'are',
  'nahi',
  'nahin',
  'kuch',
  'kuchh',
  'toh',
  'tum',
  'you',
  'bas',
]);
const SLEEP_ALSO = ['go to sleep', 'sleep', 'so jao', 'stop it now'];

/** "Stop", "stop now": cancel the task, go silent and hear nothing but
 * "Friday" / "Hey Friday" (or a Caps Lock hold) until called back. */
export function isSleepCommand(text: string): boolean {
  const said = normalise(text);
  if (SLEEP.includes(said)) return true;
  const words = said.split(' ');
  if (words.length > 8) return false;
  const core = words
    .filter((word) => !SLEEP_FILLER.has(word))
    // "stop stop stop" is one stop.
    .filter((word, i, kept) => word !== 'stop' || kept[i - 1] !== 'stop')
    .join(' ');
  return !!core && (SLEEP.includes(core) || SLEEP_ALSO.includes(core));
}

/** After being called, Friday takes follow-ups without "Friday" for this long
 * after the owner last spoke to it, then sleeps again: speech in the room
 * (a video, someone else) is not answered unless it starts with "Friday". */
export const AWAKE_MS = 60_000;

/** Whether "Friday" was said in it at all ("stop Friday"). */
export function mentionsFriday(text: string): boolean {
  return /(^| )friday( |$)/.test(normalise(text));
}

/** While quiet: was Friday called back, and is there a request with it?
 * "suno friday ek kaam hai notepad kholo" -> { resume: true, rest: "notepad kholo" }. */
export function resumeRequest(text: string): { resume: boolean; rest: string } {
  const said = normalise(text);
  // Longest phrases first, so "suno friday" wins over "friday".
  const phrase = [...RESUME]
    .sort((a, b) => b.length - a.length)
    .find((item) =>
      // A bare "Friday" counts only first: "see you on Friday" is not a call.
      item === 'friday'
        ? said === item || said.startsWith(`${item} `)
        : said === item ||
          said.startsWith(`${item} `) ||
          said.includes(` ${item} `) ||
          said.endsWith(` ${item}`),
    );
  if (!phrase) return { resume: false, rest: '' };
  let rest = said.slice(said.indexOf(phrase) + phrase.length).trim();
  for (let i = 0; i < 3; i++) rest = rest.replace(LEAD_IN, '').trim();
  return { resume: true, rest: rest.split(' ').length >= 2 ? rest : '' };
}

// Said around a cancel without changing what is cancelled: "cancel all the
// tasks", "sab kaam cancel karo", "Friday, leave it".
const CANCEL_FILLER = new Set([
  'all',
  'the',
  'a',
  'my',
  'your',
  'this',
  'that',
  'it',
  'task',
  'tasks',
  'job',
  'jobs',
  'everything',
  'current',
  'last',
  'running',
  'please',
  'now',
  'ok',
  'okay',
  'friday',
  'hey',
  'sab',
  'saare',
  'sare',
  'saara',
  'kaam',
  'yeh',
  'woh',
  'wala',
  'abhi',
  'karo',
  'kar',
  'do',
  'de',
  'dijiye',
  'just',
  'bhai',
  'yaar',
]);
const CANCEL = [
  'cancel',
  'abort',
  'stop',
  'chhodo',
  'chodo',
  'chhod',
  'chod',
  'leave',
  'forget',
  'never mind',
  'nevermind',
  'rehne',
  'rehne do',
];

/** "Cancel all the task", "sab cancel karo", "chhodo", "never mind": drop
 * whatever Friday is doing (not "cancel the email to Ravi", which is a request). */
export function isCancelCommand(text: string): boolean {
  const words = normalise(text).split(' ').filter(Boolean);
  if (!words.length || words.length > 8) return false;
  const core = words.filter((word) => !CANCEL_FILLER.has(word)).join(' ');
  return CANCEL.includes(core);
}
