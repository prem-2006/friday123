import { expect, it } from 'vitest';
import {
  isQuietCommand,
  isSleepCommand,
  mentionsFriday,
  resumeRequest,
} from '../../src/friday/client/stage/conversationMode.js';

it('goes quiet on short "be quiet" sentences, in Hindi too', () => {
  for (const said of [
    'Wait',
    'wait Friday',
    'Kuch mat bolo abhi.',
    'abhi kuch mat bolo',
    'chup raho',
    'ruko',
    'not now',
  ])
    expect(isQuietCommand(said), said).toBe(true);
  for (const said of [
    'wait for the download and then tell me',
    'open notepad',
    'what should I say to Ravi',
    '',
  ])
    expect(isQuietCommand(said), said).toBe(false);
});

it('comes back when called, with the request said along with it', () => {
  expect(resumeRequest('Suno Friday, ek kaam hai')).toEqual({
    resume: true,
    rest: '',
  });
  expect(resumeRequest('suno friday ek kaam hai notepad kholo')).toEqual({
    resume: true,
    rest: 'notepad kholo',
  });
  expect(resumeRequest('Ab baat karte hain Friday')).toEqual({
    resume: true,
    rest: '',
  });
  expect(resumeRequest('Friday, play some music')).toEqual({
    resume: true,
    rest: 'play some music',
  });
  expect(resumeRequest('hey friday what time is it').rest).toBe(
    'what time is it',
  );
});

it('stays quiet for talk that is not meant for Friday', () => {
  for (const said of [
    'see you on Friday',
    'haan main aa raha hoon',
    'the meeting is on friday morning',
  ])
    expect(resumeRequest(said).resume, said).toBe(false);
});

it('stops and sleeps on a bare "stop", not on "stop the song"', () => {
  for (const said of ['Stop', 'stop now!', 'Friday, stop.', 'bas karo'])
    expect(isSleepCommand(said), said).toBe(true);
  for (const said of [
    'stop the song',
    'stop the timer now',
    'bus stop near me',
  ])
    expect(isSleepCommand(said), said).toBe(false);
  expect(resumeRequest('Hey Friday').resume).toBe(true);
});

it('sleeps on a stop said with filler words, never on a longer request', () => {
  for (const said of [
    'ok stop',
    'Okay, stop now.',
    'stop yaar',
    'Friday, please stop',
    'hey friday stop',
    'go to sleep',
    'so jao',
  ])
    expect(isSleepCommand(said), said).toBe(true);
  for (const said of ['stop the song', 'ok stop the music', 'please stop the timer'])
    expect(isSleepCommand(said), said).toBe(false);
});

it('knows when Friday is named in a sentence', () => {
  expect(mentionsFriday('stop Friday')).toBe(true);
  expect(mentionsFriday('stop the fridge')).toBe(false);
});
