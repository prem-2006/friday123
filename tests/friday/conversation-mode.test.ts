import { expect, it } from 'vitest';
import {
  isCancelCommand,
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

it('stops on the ways the owner really says it, in Hindi script too', () => {
  for (const said of [
    'stopped',
    'Friday stopped.',
    'kuchh nahi stop',
    'nahi nahi kuchh nahi toh stop ho ja',
    'stop stop stop',
    'Friday, stop ho jao',
    'disable Friday',
    'Friday off',
    'स्टॉप',
    'स्टॉप फ्राइडे',
  ])
    expect(isSleepCommand(said), said).toBe(true);
  for (const said of [
    'stop the song and play the next one',
    'turn off the lights',
    'nahi stop mat karo gaana',
  ])
    expect(isSleepCommand(said), said).toBe(false);
});

it('wakes on "Hey Friday" as Gnani mishears it, only at the start', () => {
  for (const said of ['a friday', 'He Friday!', 'हे फ्राइडे', 'फ्राइडे'])
    expect(resumeRequest(said).resume, said).toBe(true);
  expect(resumeRequest('a friday play some music').rest).toBe(
    'play some music',
  );
  expect(resumeRequest('kal hai friday').resume).toBe(false);
});

it('knows when Friday is named in a sentence', () => {
  expect(mentionsFriday('stop Friday')).toBe(true);
  expect(mentionsFriday('stop the fridge')).toBe(false);
});

it('cancels on "cancel all the task", in Hindi too, not on a request', () => {
  for (const said of [
    'cancel all the task',
    'Cancel all the tasks.',
    'Friday, cancel everything',
    'sab cancel karo',
    'saare kaam cancel kar do',
    'chhodo',
    'never mind',
    'stop all the tasks',
  ])
    expect(isCancelCommand(said), said).toBe(true);
  for (const said of [
    'cancel the email to Ravi',
    'cancel my meeting at 5',
    'open chrome',
    '',
  ])
    expect(isCancelCommand(said), said).toBe(false);
});
