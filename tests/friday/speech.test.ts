import { expect, it } from 'vitest';
import { speakableText } from '../../src/friday/server/speech-text.js';
import { lastReplyText } from '../../src/friday/client/speech.js';
import { encodeWav, toBase64 } from '../../src/friday/client/wav.js';

it('strips markdown so Timbre reads only the words', () => {
  expect(
    speakableText(
      '## Done\n\n- **Dentist:** Smile Clinic ([map](https://maps.example/x))\n- Time: `6 PM`\n\nSource: https://example.com/a',
    ),
  ).toBe('Done. Dentist: Smile Clinic (map). Time: 6 PM. Source: Example');
});

it('says a site name, never the whole address', () => {
  expect(speakableText('Opened https://mail.google.com.')).toBe(
    'Opened Gmail.',
  );
  expect(speakableText('Opening mail.google.com/mail/u/0 now')).toBe(
    'Opening Gmail now',
  );
  expect(speakableText('Opened https://www.example.co.in/a?b=c, done')).toBe(
    'Opened Example, done',
  );
  expect(speakableText('Mail ravi@example.com the report.pdf')).toBe(
    'Mail ravi@example.com the report.pdf',
  );
});

it('stops long replies at a sentence boundary', () => {
  const text = 'This is one sentence. '.repeat(60);
  const spoken = speakableText(text, 100);
  expect(spoken.length).toBeLessThanOrEqual(100);
  expect(spoken.endsWith('.')).toBe(true);
});

it('reads only the latest turn reply', () => {
  expect(
    lastReplyText([
      { role: 'user', content: 'Book it' },
      { role: 'assistant', content: '' },
      { role: 'tool', content: '{"status":"answered"}' },
      { role: 'assistant', content: 'Booked for 6 PM and Priya knows.' },
    ]),
  ).toBe('Booked for 6 PM and Priya knows.');
  expect(
    lastReplyText([
      { role: 'assistant', content: 'Old reply' },
      { role: 'user', content: 'New question' },
    ]),
  ).toBe('');
});

it('encodes a valid 16 kHz mono 16-bit WAV', () => {
  const wav = encodeWav(new Float32Array([0, 1, -1, 0.5]));
  const view = new DataView(wav.buffer);
  expect(new TextDecoder().decode(wav.subarray(0, 4))).toBe('RIFF');
  expect(new TextDecoder().decode(wav.subarray(8, 12))).toBe('WAVE');
  expect(view.getUint32(24, true)).toBe(16_000);
  expect(view.getUint16(22, true)).toBe(1);
  expect(view.getUint16(34, true)).toBe(16);
  expect(view.getUint32(40, true)).toBe(8);
  expect([1, 2, 3].map((i) => view.getInt16(44 + i * 2, true))).toEqual([
    32767, -32768, 16383,
  ]);
  expect(Buffer.from(toBase64(wav), 'base64')).toEqual(Buffer.from(wav));
});
