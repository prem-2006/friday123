import { expect, it } from 'vitest';
import {
  doneText,
  dueLabel,
} from '../../src/friday/client/home/Console.js';
import { googleShortcuts } from '../../src/friday/client/home/FridayHome.js';

it('opens Gmail, Calendar and Drive in the account Friday is signed in to', () => {
  const urls = Object.fromEntries(
    googleShortcuts('prem@gmail.com').map(({ label, url }) => [label, url]),
  );
  expect(urls.Gmail).toBe(
    'https://mail.google.com/mail/u/?authuser=prem%40gmail.com',
  );
  expect(urls.Calendar).toBe(
    'https://calendar.google.com/calendar/r?authuser=prem%40gmail.com',
  );
  expect(urls.Drive).toBe(
    'https://drive.google.com/drive/my-drive?authuser=prem%40gmail.com',
  );
  expect(urls.YouTube).toBe('https://www.youtube.com/');
});

it('uses the plain addresses until the account is known', () => {
  expect(googleShortcuts(null).map(({ url }) => url)).toEqual([
    'https://mail.google.com/',
    'https://calendar.google.com/',
    'https://drive.google.com/',
    'https://www.youtube.com/',
  ]);
});

it('says when a reminder is due in plain words', () => {
  const now = new Date(2026, 10, 4, 9, 30);
  const at = (day: number, hour: number) =>
    new Date(2026, 10, day, hour).getTime() / 1000;
  expect(dueLabel(at(4, 17), now)).toMatch(/^5:00\s?pm$/i);
  expect(dueLabel(at(5, 9), now)).toBe('Tomorrow');
  expect(dueLabel(at(12, 9), now)).toMatch(/12/);
});

it('drops the feed\'s Done from what Friday did', () => {
  expect(doneText('Opening Notepad… Done.')).toBe('Opening Notepad…');
  expect(doneText('Reading the page.')).toBe('Reading the page.');
});
