// Friday: the main voice screen. Tap the orb and talk; Friday answers out
// loud, hands browser and desktop work to its helper Dots (Navigator,
// Operator), narrates what they do, and asks for a spoken yes before sending
// or calling on WhatsApp.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Message } from '@ag-ui/core';
import {
  CircleCheck,
  CircleX,
  Loader2,
  LayoutDashboard,
  OctagonX,
  RotateCcw,
} from 'lucide-react';
import type { Dot, WorkspaceState } from '../../../shared/types';
import { FRIDAY_DEFAULT_LANGUAGE, FRIDAY_LANGUAGES } from '../../shared/voices';
import { lastReplyText, speechParts } from '../speech';
import { toBase64 } from '../wav';
import { speechAudio, transcribeClip, voiceConfig } from '../voice-api';
import { followActivity, ownerHeaders, setStopped } from './activity';
import {
  loadHistory,
  pendingApprovalOf,
  readySentences,
  runApprovedAction,
  runFriday,
  saveHistory,
  toolAnswer,
} from './conversation';
import { HandsFree } from './handsFree';
import {
  isQuietCommand,
  isSleepCommand,
  mentionsFriday,
  resumeRequest,
  AWAKE_MS,
} from './conversationMode';
import { friendlyError } from './errors';
import { intentOf, isEcho } from './intent';
import { fixMishearings } from './corrections';
import { LiveVoice, type LiveEvent } from './liveVoice';
import { Notch, type NotchState } from './Notch';
import { ACTOR_NAMES, narrate, type ActivityEvent } from './narration';
import { Orb, type OrbMode } from './Orb';
import './stage.css';

export const STAGE_HASH = '#/friday';
// Shows every animation without keys: #/friday-demo
export const STAGE_DEMO_HASH = '#/friday-demo';
const DEMO: {
  mode: OrbMode;
  title: string;
  line: string;
  helpers: string[];
}[] = [
  {
    mode: 'listening',
    title: 'I’m listening…',
    line: 'Go ahead, I’m here.',
    helpers: [],
  },
  {
    mode: 'thinking',
    title: 'Thinking…',
    line: 'Find a dentist near Indiranagar open this evening',
    helpers: [],
  },
  {
    mode: 'working',
    title: 'Working with Navigator…',
    line: 'Navigator is opening google.com.',
    helpers: ['Navigator'],
  },
  {
    mode: 'working',
    title: 'Working with Navigator & Operator…',
    line: 'Operator is opening Notepad.',
    helpers: ['Navigator', 'Operator'],
  },
  {
    mode: 'speaking',
    title: 'Speaking…',
    line: 'Apollo Dental is open till 9 PM. Shall I message Priya?',
    helpers: [],
  },
];

function StageDemo({ onExit }: { onExit: () => void }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const timer = setInterval(
      () => setStep((n) => (n + 1) % DEMO.length),
      4500,
    );
    return () => clearInterval(timer);
  }, []);
  const scene = DEMO[step];
  const level = useCallback(
    () =>
      0.35 +
      0.35 *
        Math.abs(
          Math.sin(performance.now() / 180) * Math.sin(performance.now() / 530),
        ),
    [],
  );
  // A made-up voice, so the visualiser moves in the demo too.
  const talking = scene.mode === 'listening' || scene.mode === 'speaking';
  const spectrum = useCallback(() => {
    if (!talking) return null;
    const now = performance.now() / 1000;
    return Uint8Array.from({ length: 64 }, (_, i) =>
      Math.max(
        0,
        220 *
          Math.exp(-i / 14) *
          (0.55 + 0.45 * Math.sin(now * 9 + i * 0.7)) *
          (0.6 + 0.4 * Math.sin(now * 2.3)),
      ),
    );
  }, [talking]);
  return (
    <div className={`friday-stage mode-${scene.mode}`}>
      <Orb
        mode={scene.mode}
        level={level}
        spectrum={spectrum}
        helpers={scene.helpers}
      />
      <Header
        status={{ tone: 'ok', label: 'Demo' }}
        clock={`00:${String(step * 4 + 2).padStart(2, '0')}`}
        language={FRIDAY_DEFAULT_LANGUAGE}
        onLanguage={() => {}}
        onExit={onExit}
      />
      <section className="stage-title" key={step}>
        <h1>{scene.title}</h1>
        <p>{scene.line}</p>
      </section>
      {scene.helpers.length > 0 && (
        <aside
          className="stage-live"
          aria-label="What the helper Dots are doing"
        >
          <header>
            <span className="stage-live-dot" />
            Live · {scene.helpers.join(', ')}
          </header>
          <ol>
            <li className="done">
              <CircleCheck size={13} />
              <b>Navigator</b>
              <span>Opening google.com. Now on: Google</span>
            </li>
            <li className="done">
              <CircleCheck size={13} />
              <b>Navigator</b>
              <span>Typing "dentist Indiranagar open now".</span>
            </li>
            <li className={scene.helpers.length > 1 ? 'done' : 'running'}>
              {scene.helpers.length > 1 ? (
                <CircleCheck size={13} />
              ) : (
                <Loader2 size={13} className="spin" />
              )}
              <b>Navigator</b>
              <span>Reading the page.</span>
            </li>
            {scene.helpers.length > 1 && (
              <li className="running">
                <Loader2 size={13} className="spin" />
                <b>Operator</b>
                <span>Opening Notepad…</span>
              </li>
            )}
          </ol>
        </aside>
      )}
    </div>
  );
}
const LANGUAGE_KEY = 'friday-voice-language';

type Phase =
  'off' | 'listening' | 'hearing' | 'transcribing' | 'thinking' | 'speaking';
// Waiting for more of a sentence from Gnani's stream: after a pause while
// hands-free, and after Caps Lock is let go (its 1 s silence tail + network).
const HEARD_PAUSE_MS = 700;

/** Always-on conversation on the notch: on unless turned off
 * (localStorage friday-always-on = "off"). */
function alwaysListening() {
  try {
    return localStorage.getItem('friday-always-on') !== 'off';
  } catch {
    return true;
  }
}
const RELEASE_WAIT_MS = 1800;

interface Caption {
  who: 'you' | 'friday';
  text: string;
}

function savedLanguage() {
  try {
    return localStorage.getItem(LANGUAGE_KEY) || FRIDAY_DEFAULT_LANGUAGE;
  } catch {
    return FRIDAY_DEFAULT_LANGUAGE;
  }
}

function useClock(running: boolean) {
  const [started, setStarted] = useState<number>();
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return setStarted(undefined);
    setStarted(Date.now());
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [running]);
  if (!started) return '00:00';
  const seconds = Math.floor((Date.now() - started) / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function Header({
  status,
  clock,
  language,
  onLanguage,
  onExit,
  onStop,
  onNewChat,
}: {
  status: { tone: 'ok' | 'warn'; label: string };
  clock: string;
  language: string;
  onLanguage: (code: string) => void;
  onExit: () => void;
  onStop?: () => void;
  onNewChat?: () => void;
}) {
  return (
    <header className="stage-header">
      <div className="stage-brand">
        <span className="stage-bars" aria-hidden>
          {[0, 1, 2, 3, 4].map((i) => (
            <i key={i} style={{ animationDelay: `${i * 0.12}s` }} />
          ))}
        </span>
        <div>
          <strong>Friday</strong>
          <span>Your AI Voice Assistant</span>
        </div>
      </div>
      <div className="stage-controls">
        <select
          aria-label="Language"
          value={language}
          onChange={(e) => onLanguage(e.target.value)}
        >
          {FRIDAY_LANGUAGES.map((option) => (
            <option key={option.code} value={option.code}>
              {option.name}
            </option>
          ))}
        </select>
        <span className={`stage-pill ${status.tone}`}>
          <i />
          {status.label}
        </span>
        <span className="stage-clock">{clock}</span>
        {onStop && (
          <button
            type="button"
            className="stage-icon stop"
            onClick={onStop}
            aria-label="Stop everything"
          >
            <OctagonX size={18} />
          </button>
        )}
        {onNewChat && (
          <button
            type="button"
            className="stage-icon"
            onClick={onNewChat}
            aria-label="Start a new conversation"
            title="New conversation"
          >
            <RotateCcw size={17} />
          </button>
        )}
        <button
          type="button"
          className="stage-icon"
          onClick={onExit}
          aria-label="Open Friday's main page"
          title="Main page"
        >
          <LayoutDashboard size={18} />
        </button>
      </div>
    </header>
  );
}

// The notch while Friday cannot be reached (the app restarting, say). It has
// no voice connection, so Caps Lock would do nothing: it reloads until Friday
// is back.
function OfflineNotch({ setUp }: { setUp: boolean }) {
  useEffect(() => {
    const retry = setTimeout(() => location.reload(), 8000);
    return () => clearTimeout(retry);
  }, []);
  return (
    <Notch
      state="offline"
      level={() => 0}
      spectrum={() => null}
      note={setUp ? 'Friday is not set up yet.' : undefined}
    />
  );
}

export function FridayStage({
  workspace,
  configured,
  paused = false,
  onResume = () => {},
  onExit,
  variant = 'stage',
}: {
  workspace?: WorkspaceState;
  configured: boolean;
  /** Everything is paused from the dashboard; Friday cannot act until resumed. */
  paused?: boolean;
  onResume?: () => void;
  onExit: () => void;
  /** 'notch': the desktop pill (friday/desktop) instead of the full screen. */
  variant?: 'stage' | 'notch';
}) {
  const friday = workspace?.dots.find((dot) => dot.name === 'Friday');
  const [language, setLanguage] = useState(savedLanguage);
  const chooseLanguage = (code: string) => {
    setLanguage(code);
    try {
      localStorage.setItem(LANGUAGE_KEY, code);
    } catch {
      // Applies for this visit.
    }
  };
  if (typeof location !== 'undefined' && location.hash === STAGE_DEMO_HASH)
    return <StageDemo onExit={onExit} />;
  if (configured && friday)
    return (
      <StageSession
        key={friday.id}
        friday={friday}
        language={language}
        onLanguage={chooseLanguage}
        onExit={onExit}
        paused={paused}
        onResume={onResume}
        variant={variant}
      />
    );
  if (variant === 'notch') return <OfflineNotch setUp={!!workspace} />;

  const missing = !workspace
    ? 'Connecting…'
    : !friday
      ? 'Run friday/scripts/setup-friday-dot.mjs to create Friday and its helper Dots.'
      : `Add to .env, then restart: ${workspace.setup.missing.join(', ')}.`;
  return (
    <div className="friday-stage">
      <Orb mode="idle" level={() => 0} helpers={[]} />
      <Header
        status={{ tone: 'warn', label: 'Setup needed' }}
        clock="00:00"
        language={language}
        onLanguage={chooseLanguage}
        onExit={onExit}
      />
      <section className="stage-title">
        <h1>Almost ready</h1>
        <p>{missing}</p>
      </section>
    </div>
  );
}

function StageSession({
  friday,
  language,
  onLanguage,
  onExit,
  paused,
  onResume,
  variant,
}: {
  friday: Dot;
  language: string;
  onLanguage: (code: string) => void;
  onExit: () => void;
  paused: boolean;
  onResume: () => void;
  variant: 'stage' | 'notch';
}) {
  // Friday's voice conversation lives here and runs directly on the server.
  const [messages, setMessages] = useState<Message[]>(
    () => loadHistory().messages,
  );
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const threadId = useRef<string | undefined>(loadHistory().threadId);
  const turn = useRef<AbortController | null>(null);
  const loaded = !!friday;
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<Phase>('off');
  const [voiceReady, setVoiceReady] = useState<boolean>();
  const [helpersOnline, setHelpersOnline] = useState(false);
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [subtitle, setSubtitle] = useState('');
  const [error, setError] = useState('');
  // A problem is shown briefly, then the screen (or notch) is clean again.
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(''), 6000);
    return () => clearTimeout(timer);
  }, [error]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const controlRef = useRef<(action: string, detail: string) => void>(() => {});

  const handsFree = useRef<HandsFree | null>(null);
  // Always-on conversation, on the notch only (each voice window has its own
  // mic, and two always-on windows would both answer). Quiet: still hearing,
  // answering nothing until called back (conversationMode.ts).
  const alwaysOn = variant === 'notch' && alwaysListening();
  // Always-on starts asleep: "Friday", "Hey Friday", a double clap or Caps
  // Lock wakes it.
  const [quiet, setQuiet] = useState(alwaysOn);
  const quietRef = useRef(alwaysOn);
  // When the owner last spoke to Friday (follow-ups need no "Friday" until
  // AWAKE_MS after it).
  const awakeAt = useRef(0);
  // The notch shows Friday asleep again once that time is up.
  useEffect(() => {
    if (!alwaysOn) return;
    const timer = setInterval(() => {
      if (
        !quietRef.current &&
        Date.now() - awakeAt.current > AWAKE_MS &&
        !runningRef.current &&
        !replying.current &&
        !audio.current &&
        !live.current?.speaking
      ) {
        quietRef.current = true;
        setQuiet(true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [alwaysOn]);
  // Live copies of state that async voice handlers read after a re-render.
  const runningRef = useRef(false);
  const replying = useRef(false); // speaking the answer to the last request
  const turnDone = useRef<Promise<void>>(Promise.resolve());
  // Whether the turn being heard began while Friday was talking.
  const overFriday = useRef(false);
  // The live voice pipeline (friday/services over WebRTC). When it is not
  // available, Friday falls back to sending recorded clips to Gnani.
  const live = useRef<LiveVoice | null>(null);
  const liveConnecting = useRef<Promise<LiveVoice | null> | null>(null);
  // The screen is closing: stop reconnecting the live voice.
  const leaving = useRef(false);
  const liveEventRef = useRef<(event: LiveEvent) => void>(() => {});
  const spokenWaiter = useRef<(() => void) | null>(null);
  // What Friday said lately, to tell its echo from the owner talking over it.
  const spokenLately = useRef<{ text: string; at: number }[]>([]);
  // Hands-free: the mic is open and turns start by voice. For push-to-talk
  // alone the mic may stay open, but nothing is heard until Caps Lock is held.
  // Whether Friday's voice is playing, by either path.
  const fridayTalking = () => !!audio.current || !!live.current?.speaking;
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const ttsAnalyser = useRef<AnalyserNode | null>(null);
  const speakingMain = useRef(false);
  // Counts the owner talking over Friday, so a reply's queued sentences stop.
  const talkedOver = useRef(0);
  const stopping = useRef(false);
  const spokenApproval = useRef('');
  const languageRef = useRef(language);
  languageRef.current = language;
  // How long each step took for the last thing you said, in ms.
  const timing = useRef<{
    endedAt?: number;
    heard?: number;
    reply?: number;
    firstAudio?: number;
  }>({});
  const logTiming = () => {
    const t = timing.current;
    const ms = (value?: number) =>
      value === undefined ? '–' : `${(value / 1000).toFixed(1)} s`;
    const total =
      t.endedAt !== undefined && t.firstAudio !== undefined
        ? (t.heard ?? 0) + (t.reply ?? 0) + t.firstAudio
        : undefined;
    console.info(
      `[Friday] heard ${ms(t.heard)} · answered ${ms(t.reply)} · voice ${ms(t.firstAudio)} · you stopped → Friday spoke ${ms(total)}`,
    );
    void fetch('/api/friday/voice/timing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...ownerHeaders() },
      body: JSON.stringify({ ...t, total }),
    }).catch(() => {});
  };

  useEffect(() => {
    void voiceConfig()
      .then((config) => setVoiceReady(config.ready))
      .catch(() => setVoiceReady(false));
    const unfollow = followActivity(
      (event) => {
        // Caps Lock and clap signals from friday/services, not helper activity.
        if (event.actor === 'control')
          return controlRef.current(event.action, event.detail);
        setEvents((list) => [...list.slice(-40), event]);
      },
      setHelpersOnline,
      variant === 'notch' ? 'notch' : true,
    );
    return () => {
      leaving.current = true;
      unfollow();
      live.current?.stop();
      handsFree.current?.stop();
      audio.current?.pause();
      void audioContext.current?.close();
    };
  }, []);

  const caption = (who: Caption['who'], text: string) =>
    setCaptions((list) => [...list.slice(-3), { who, text }]);

  const level = useCallback(() => {
    if (live.current) return live.current.level();
    if (speakingMain.current || audio.current) {
      const analyser = ttsAnalyser.current;
      if (!analyser) return 0.3;
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(data);
      let sum = 0;
      for (const value of data) sum += value;
      return Math.min(1, (sum / data.length / 255) * 3);
    }
    return handsFree.current?.level ?? 0;
  }, []);

  // Frequency bars for the orb: Friday's voice while it speaks, else the mic.
  const spectrum = useCallback(() => {
    if (live.current) return live.current.spectrum();
    const analyser = ttsAnalyser.current;
    if ((speakingMain.current || audio.current) && analyser) {
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(data);
      return data;
    }
    return handsFree.current?.spectrum() ?? null;
  }, []);

  // Gnani allows one transcription at a time, so clips wait their turn.
  const transcribing = useRef<Promise<unknown>>(Promise.resolve());
  const starting = useRef(false);
  const speechToken = useRef(0);
  const stopSpeaking = () => {
    speechToken.current += 1; // cancels any parts still queued
    audio.current?.pause();
    audio.current = null;
    if (live.current?.speaking || spokenWaiter.current)
      live.current?.interrupt();
    spokenWaiter.current?.();
    spokenWaiter.current = null;
    speakingMain.current = false;
  };

  const playPart = async (blob: Blob, token: number) => {
    if (token !== speechToken.current) return;
    const url = URL.createObjectURL(blob);
    const player = new Audio(url);
    audio.current = player;
    try {
      audioContext.current ??= new AudioContext();
      const analyser = audioContext.current.createAnalyser();
      analyser.fftSize = 256;
      audioContext.current.createMediaElementSource(player).connect(analyser);
      analyser.connect(audioContext.current.destination);
      ttsAnalyser.current = analyser;
    } catch {
      ttsAnalyser.current = null;
    }
    await new Promise<void>((resolve) => {
      player.onended = player.onerror = player.onpause = () => resolve();
      void player.play().catch(() => resolve());
    });
    URL.revokeObjectURL(url);
  };

  // Speaks sentence by sentence: the first part plays while the next ones are
  // still being synthesised, so the first word comes much sooner.
  const speak = async (text: string, main: boolean, startedAt?: number) => {
    if (!text || !voiceReady) return;
    if (!main && (speakingMain.current || fridayTalking())) return; // never talk over a reply
    stopSpeaking();
    const token = speechToken.current;
    speakingMain.current = main;
    if (main) setPhase('speaking');
    const now = Date.now();
    spokenLately.current = [
      ...spokenLately.current.filter((item) => now - item.at < 30_000),
      { text, at: now },
    ];
    const voice = live.current;
    if (voice?.connected) {
      // The live pipeline streams Friday's voice; wait until it has finished
      // (or was interrupted), with a cap in case a message is lost.
      try {
        await new Promise<void>((resolve) => {
          const cap = setTimeout(resolve, 4000 + text.length * 90);
          spokenWaiter.current = () => {
            clearTimeout(cap);
            resolve();
          };
          if (main) logTiming();
          voice.say(text);
        });
      } finally {
        if (token === speechToken.current) {
          spokenWaiter.current = null;
          if (main) {
            speakingMain.current = false;
            setPhase('off');
          }
        }
      }
      return;
    }
    const parts = speechParts(text);
    const clips = parts.map(() => null as Promise<Blob> | null);
    const clip = (index: number) =>
      (clips[index] ??= speechAudio(parts[index], languageRef.current));
    try {
      for (let index = 0; index < parts.length; index++) {
        const next = clip(index);
        if (index + 1 < parts.length) void clip(index + 1).catch(() => {});
        const blob = await next;
        if (token !== speechToken.current) break;
        if (index === 0 && startedAt)
          timing.current.firstAudio = performance.now() - startedAt;
        if (index === 0 && main) logTiming();
        await playPart(blob, token);
      }
    } catch (e) {
      if (main) setError(friendlyError(e, 'Friday could not speak.'));
    } finally {
      if (token === speechToken.current) {
        audio.current = null;
        // A turn that started on Friday's own voice would swallow whatever the
        // owner says next; start that turn afresh instead.
        if (overFriday.current && handsFree.current?.hearing) {
          handsFree.current.dropTurn();
          overFriday.current = false;
        }
        if (main) {
          speakingMain.current = false;
          setPhase('off');
        }
      }
    }
  };

  const pendingApproval = useMemo(
    () => pendingApprovalOf(messages),
    [messages],
  );

  useEffect(() => {
    if (
      !pendingApproval ||
      spokenApproval.current === pendingApproval.toolCallId
    )
      return;
    spokenApproval.current = pendingApproval.toolCallId;
    void speak(
      `I need your OK: ${pendingApproval.summary}. Say yes or no.`,
      true,
    );
  }, [pendingApproval]);

  const setHistory = (next: Message[]) => {
    messagesRef.current = next;
    setMessages(next);
    saveHistory(next, threadId.current);
  };

  // One run of Friday on the server; afterwards either speak the reply or
  // wait for the owner's OK on a WhatsApp send or call.
  const runTurn = async (history: Message[]) => {
    let finished = () => {};
    turnDone.current = new Promise((resolve) => (finished = resolve));
    runningRef.current = true;
    setRunning(true);
    setPhase('thinking');
    setError('');
    const controller = new AbortController();
    turn.current = controller;
    // The reply is spoken sentence by sentence while it is still being
    // written, one after another, never waiting for the whole text.
    let written = '';
    let pending = '';
    let spokenText = '';
    let voiceQueue: Promise<void> = Promise.resolve();
    const heardAt = talkedOver.current;
    const say = (sentence: string) => {
      spokenText += `${sentence} `;
      voiceQueue = voiceQueue.then(async () => {
        if (
          controller.signal.aborted ||
          stopping.current ||
          talkedOver.current !== heardAt
        )
          return;
        replying.current = true;
        await speak(sentence, true, performance.now());
      });
    };
    try {
      const asked = performance.now();
      const result = await runFriday(history, controller.signal, (delta) => {
        written += delta;
        setSubtitle(written);
        const [ready, rest] = readySentences(pending + delta);
        pending = rest;
        ready.forEach(say);
      });
      timing.current.reply = performance.now() - asked;
      threadId.current = result.threadId;
      const next = [...history, ...result.newMessages];
      setHistory(next);
      if (pendingApprovalOf(next)) return;
      // Friday always answers something: silence looks like it never heard.
      const reply =
        lastReplyText(result.newMessages) ||
        'Sorry, I missed that. Please say it again.';
      caption('friday', reply);
      setSubtitle(reply);
      if (pending.trim()) say(pending.trim());
      // A reply that was not written as text (a quick action's sentence, an
      // instant command) is said now.
      const norm = (text: string) => text.replace(/\s+/gu, ' ').trim();
      if (!norm(spokenText).includes(norm(reply))) say(reply);
      await voiceQueue;
    } catch (e) {
      if (!controller.signal.aborted)
        setError(friendlyError(e, 'That did not work. Try again.'));
    } finally {
      replying.current = false;
      turn.current = null;
      runningRef.current = false;
      setRunning(false);
      finished();
      if (stopping.current) {
        stopping.current = false;
        await setStopped(false);
      }
      setPhase((current) => (current === 'speaking' ? current : 'off'));
    }
  };

  // Traces Caps Lock and approvals into data/friday-voice.log.
  const trace = (
    event: 'ptt' | 'heard' | 'decide' | 'decide_failed',
    detail: Record<string, string | number | boolean>,
  ) =>
    void fetch('/api/friday/voice/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...ownerHeaders() },
      body: JSON.stringify({ event, detail: { ...detail, window: variant } }),
    }).catch(() => {});

  const decide = async (approve: boolean) => {
    const pending = pendingApproval;
    trace('decide', {
      approve,
      pending: !!pending,
      running,
      thread: !!threadId.current,
    });
    if (!pending || running) return;
    caption('friday', approve ? 'Approved.' : 'Declined.');
    let answer: Record<string, unknown> = {
      approved: false,
      message:
        'The owner declined this action. Do not perform it or try another way.',
    };
    if (approve) {
      if (!threadId.current) {
        setError('That request expired. Please ask again.');
        return;
      }
      setRunning(true);
      setPhase('thinking');
      try {
        answer = {
          approved: true,
          ...(await runApprovedAction(threadId.current, pending)),
        };
      } catch (e) {
        trace('decide_failed', { error: String(e) });
        setRunning(false);
        setPhase('off');
        setError(friendlyError(e, 'Could not do that. Try again.'));
        // Answer the request so its card goes away instead of staying stuck.
        setHistory([
          ...messagesRef.current,
          toolAnswer(pending, {
            approved: false,
            message: `This could not run: ${String(e)}`,
          }),
        ]);
        return;
      }
    }
    await runTurn([...messagesRef.current, toolAnswer(pending, answer)]);
  };

  const send = async (text: string) => {
    if (!text.trim() || runningRef.current || !loaded) return;
    if (paused) {
      setError('Friday is paused. Tap Resume to continue.');
      return;
    }
    caption('you', text);
    setSubtitle(text);
    const history = [
      ...messagesRef.current,
      {
        id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`,
        role: 'user',
        content: text,
      } as Message,
    ];
    setHistory(history);
    await runTurn(history);
  };

  const stopEverything = async () => {
    stopSpeaking();
    caption('friday', 'Stopping.');
    if (running) {
      stopping.current = true;
      await setStopped(true);
      turn.current?.abort();
    }
  };

  const newConversation = () => {
    if (running) return;
    stopSpeaking();
    setHistory([]);
    setCaptions([]);
    setSubtitle('');
    setError('');
  };

  const heard = async (
    wav: Uint8Array,
    endedAt: number,
    startedOverFriday: boolean,
  ) => {
    overFriday.current = false; // that turn is over
    if (!startedOverFriday) setPhase('transcribing');
    timing.current = { endedAt };
    let text = '';
    try {
      const job = transcribing.current.then(() =>
        transcribeClip(toBase64(wav), languageRef.current),
      );
      transcribing.current = job.catch(() => {});
      text = (await job).trim();
      timing.current.heard = performance.now() - endedAt;
    } catch (e) {
      setError(friendlyError(e, 'Could not hear that.'));
    }
    await respond(text, startedOverFriday);
  };

  // What to do with something the owner said (from either voice path).
  const respond = async (heardText: string, startedOverFriday: boolean) => {
    let text = fixMishearings(heardText, languageRef.current);
    // A Caps Lock hold is always meant for Friday, quiet or not.
    const held = heldTurn.current;
    heldTurn.current = false;
    if (text && alwaysOn && !held) {
      // Called a while ago and not spoken to since: asleep again, unless
      // Friday is still talking or working on something.
      if (
        !quietRef.current &&
        Date.now() - awakeAt.current > AWAKE_MS &&
        !runningRef.current &&
        !replying.current &&
        !fridayTalking()
      ) {
        quietRef.current = true;
        setQuiet(true);
      }
      // Asleep: only "Friday" / "Hey Friday" counts, so the room's talk is
      // not answered (and a stray "stop" cancels nothing).
      if (quietRef.current) {
        const sleepCall = isSleepCommand(text) && mentionsFriday(text);
        const back = resumeRequest(text);
        if (!back.resume && !sleepCall) return; // heard, not answered
        if (!sleepCall) {
          quietRef.current = false;
          setQuiet(false);
          awakeAt.current = Date.now();
          if (!back.rest) {
            caption('you', text);
            return void speak('Haan, boliye.', true);
          }
          text = back.rest;
        }
      }
    }
    if (text) awakeAt.current = Date.now();
    // "Stop", "stop now": cancel everything and stop listening until "Friday"
    // or "Hey Friday". Caps Lock still works throughout.
    if (text && isSleepCommand(text)) {
      quietRef.current = true;
      setQuiet(true);
      caption('you', text);
      await stopEverything();
      setPhase('off');
      setSubtitle(
        'Stopped. Say “Hey Friday” or hold Caps Lock when you need me.',
      );
      return;
    }
    if (text && alwaysOn && !held) {
      // "Wait", "kuch mat bolo abhi": stop talking, keep listening; the task
      // that is running carries on.
      if (isQuietCommand(text)) {
        quietRef.current = true;
        setQuiet(true);
        stopSpeaking();
        caption('you', text);
        setSubtitle('Quiet. Say “suno Friday” when you need me.');
        return;
      }
    } else if (held && quietRef.current) {
      quietRef.current = false;
      setQuiet(false);
    }
    if (!text) {
      if (!startedOverFriday) setPhase(runningRef.current ? 'thinking' : 'off');
      return;
    }
    const intent = intentOf(text);
    trace('heard', {
      intent,
      pending: !!pendingApproval,
      running: runningRef.current,
    });
    // "Stop" stops Friday only while it is talking or working; otherwise it is
    // a request ("stop" the song that is playing).
    if (
      intent === 'stop' &&
      (startedOverFriday ||
        runningRef.current ||
        speakingMain.current ||
        fridayTalking())
    )
      return stopEverything();
    // Speech that began while Friday was talking: ignore it if it is Friday's
    // own voice coming back through the mic, otherwise the owner is talking
    // over Friday, which interrupts it.
    if (startedOverFriday && intent === 'request') {
      const lately = spokenLately.current.map((item) => item.text).join(' ');
      if (isEcho(text, lately)) return;
      stopSpeaking();
    }
    if (runningRef.current && replying.current) {
      // Friday was only reading out its last answer: that turn is over.
      stopSpeaking();
      await turnDone.current;
    }
    if (pendingApproval) {
      caption('you', text);
      if (intent === 'yes' || intent === 'no') return decide(intent === 'yes');
      return speak('Please say yes or no first.', true);
    }
    if (runningRef.current) {
      caption('you', text);
      return speak('I am still on the last task. Say stop to cancel it.', true);
    }
    await send(text);
  };
  const heardRef = useRef(heard);
  heardRef.current = heard;

  // Opens the mic. auto: turns start by voice (hands-free); otherwise only
  // push-to-talk records, so nothing is heard until Caps Lock is held.
  const openMic = async (auto: boolean) => {
    const current = handsFree.current;
    if (current?.active) {
      if (auto) current.auto = true;
      return current;
    }
    if (starting.current) return null;
    const next = new HandsFree({
      onSpeechStart: () => {
        overFriday.current = fridayTalking();
        setPhase((phase) => (phase === 'listening' ? 'hearing' : phase));
      },
      onUtterance: (wav, endedAt) =>
        void heardRef.current(wav, endedAt, overFriday.current),
      onError: setError,
      isFridaySpeaking: () => !!audio.current,
      auto,
    });
    starting.current = true;
    try {
      await next.start();
    } catch {
      setError('Allow microphone access so Friday can hear you.');
      return null;
    } finally {
      starting.current = false;
    }
    handsFree.current = next;
    return next;
  };

  // Connects the live voice pipeline once; null when it is unavailable.
  const ensureLive = async () => {
    if (live.current?.connected) return live.current;
    liveConnecting.current ??= (async () => {
      const voice = new LiveVoice({
        language: languageRef.current,
        onEvent: (event) => liveEventRef.current(event),
        onClosed: () => {
          if (live.current !== voice) return;
          live.current = null;
          // Connect again at once (the services restarted, say), so the next
          // Caps Lock press is heard from its first word, not lost while
          // connecting.
          const reconnect = () => {
            if (leaving.current || live.current) return;
            void ensureLive().then((next) => {
              if (!next) setTimeout(reconnect, 5000);
            });
          };
          setTimeout(reconnect, 2000);
        },
      });
      try {
        await voice.start();
        live.current = voice;
        // Always-on conversation: everything said reaches Friday.
        if (alwaysOn) voice.handsFree(true);
        return voice;
      } catch {
        voice.stop();
        return null;
      } finally {
        liveConnecting.current = null;
      }
    })();
    return liveConnecting.current;
  };

  // Gnani's stream sends a sentence in pieces when you pause ("open" ...
  // "youtube"), so pieces are joined into one request: everything heard while
  // Caps Lock is held and shortly after, or within a short pause hands-free.
  const heardTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const heardSoFar = useRef<string[]>([]);
  const finishHearing = () => {
    clearTimeout(heardTimer.current);
    const text = heardSoFar.current.join(' ').trim();
    heardSoFar.current = [];
    const startedOverFriday = overFriday.current;
    overFriday.current = false;
    void respond(text, startedOverFriday);
  };
  const finishHearingIn = (ms: number) => {
    clearTimeout(heardTimer.current);
    heardTimer.current = setTimeout(finishHearing, ms);
  };
  liveEventRef.current = (event) => {
    if (event.event === 'heard') {
      heardSoFar.current.push(event.text);
      // While Caps Lock is held, wait for the release; otherwise for a pause.
      if (!holding.current) finishHearingIn(HEARD_PAUSE_MS);
    } else if (event.event === 'speech_start') {
      // Hands-free: the owner talking over Friday interrupts it at once.
      overFriday.current = fridayTalking();
      if (overFriday.current) stopSpeaking();
      setPhase((phase) =>
        phase === 'listening' || phase === 'speaking' ? 'hearing' : phase,
      );
    } else if (event.event === 'speaking') {
      if (event.on) setPhase('speaking');
      else {
        spokenWaiter.current?.();
        spokenWaiter.current = null;
      }
    } else if (event.event === 'error') {
      // Friday cannot be waiting to finish speaking after a voice problem.
      spokenWaiter.current?.();
      spokenWaiter.current = null;
      setError(friendlyError(new Error(event.message), 'Voice problem.'));
    }
  };

  // Hold Caps Lock to talk: recording runs while it is held and is sent when
  // it is let go. Signals come from the page itself or, in any app, from the
  // services' keyboard hook.
  const holding = useRef(false);
  // The turn now being heard began with a Caps Lock hold (or "Hey Friday").
  const heldTurn = useRef(false);
  const pushToTalk = async (down: boolean) => {
    trace('ptt', {
      down,
      live: !!live.current?.connected,
      holding: holding.current,
      loaded,
      voiceReady: String(voiceReady),
      paused,
    });
    if (down) heldTurn.current = true;
    if (!down) {
      if (!holding.current) return;
      holding.current = false;
      if (live.current?.connected) {
        live.current.pushToTalk(false);
        setPhase('transcribing');
        // Gnani sends the last piece after the trailing second of silence.
        finishHearingIn(RELEASE_WAIT_MS);
      } else if (handsFree.current?.active) handsFree.current.release();
      // Let go before the voice connected: nothing was heard, so the notch
      // must not keep saying "Listening…".
      else setPhase((phase) => (phase === 'hearing' ? 'off' : phase));
      return;
    }
    // A failed config probe only disables the clip fallback; a live session
    // can still be ready to take this key hold.
    if (holding.current || !loaded || paused) return;
    holding.current = true;
    talkedOver.current += 1; // sentences still waiting are not said
    stopSpeaking();
    setError('');
    setSubtitle('');
    setPhase('hearing'); // "Listening…" the moment the key goes down
    overFriday.current = false;
    const voice = await ensureLive();
    if (voice) {
      if (!holding.current) {
        // Let go while connecting: nothing to hear.
        setPhase((phase) => (phase === 'hearing' ? 'off' : phase));
        return;
      }
      voice.pushToTalk(true);
      return;
    }
    if (!holding.current) {
      setPhase((phase) => (phase === 'hearing' ? 'off' : phase));
      return;
    }
    // Show why the hold cannot proceed instead of silently ignoring it.
    if (voiceReady === false) {
      holding.current = false;
      setPhase((phase) => (phase === 'hearing' ? 'off' : phase));
      setError(
        'Friday voice is unavailable. Check the app and voice services, then try again.',
      );
      return;
    }
    const mic = await openMic(false);
    if (!mic) return setPhase('off');
    if (!holding.current) return;
    mic.hold();
  };
  const speakRef = useRef((text: string) => speak(text, true));
  speakRef.current = (text: string) => speak(text, true);
  const pushRef = useRef(pushToTalk);
  pushRef.current = pushToTalk;
  controlRef.current = (action, detail) => {
    if (action === 'announce' && detail) {
      // A timer, reminder or deadline came due: Friday says it.
      caption('friday', detail);
      setSubtitle(detail);
      void speakRef.current(detail);
    } else if (action === 'wake' && detail) {
      quietRef.current = false;
      setQuiet(false);
      awakeAt.current = Date.now();
      // "Hey Friday" or a double clap: hello about what is on screen, then
      // friday/services starts listening once it has been said.
      caption('friday', detail);
      setSubtitle(detail);
      void speakRef
        .current(detail)
        .finally(
          () =>
            void fetch('/api/friday/wake/ready', { method: 'POST' }).catch(
              () => undefined,
            ),
        );
    } else if (action === 'ptt_down') void pushRef.current(true);
    else if (action === 'ptt_up') void pushRef.current(false);
    else if (action === 'notch_toggle' && variant === 'notch')
      (
        globalThis as { fridayDesktop?: { toggle: () => void } }
      ).fridayDesktop?.toggle();
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'CapsLock' && !e.repeat) void pushRef.current(true);
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === 'CapsLock') void pushRef.current(false);
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, []);

  // Connect the live voice at once when the mic is already allowed, so the
  // first Caps Lock press is heard from its first word.
  useEffect(() => {
    if (!loaded) return;
    void navigator.permissions
      ?.query({ name: 'microphone' as PermissionName })
      .then((state) => {
        if (state.state === 'granted') void ensureLive();
      })
      .catch(() => {});
  }, [loaded, voiceReady]);

  // Gnani hears one language per connection: reconnect when it changes.
  useEffect(() => {
    const voice = live.current;
    if (!voice || !loaded) return;
    voice.stop();
    live.current = null;
    void ensureLive();
  }, [language]);

  // When friday/services come back (a restart), connect the live voice anew.
  // An old connection can keep looking "connected" for minutes, and until it
  // is replaced, "Hey Friday" and claps hear nothing.
  const wasOnline = useRef(false);
  useEffect(() => {
    const cameBack = helpersOnline && !wasOnline.current;
    wasOnline.current = helpersOnline;
    if (!cameBack || !loaded || !live.current) return;
    live.current.stop();
    live.current = null;
    void ensureLive();
  }, [helpersOnline]);

  // Show helper activity while a task runs. It is not spoken: Friday talks
  // only to answer the owner, never in the middle of the work.
  const narrated = useRef(0);
  useEffect(() => {
    const latest = events.at(-1);
    if (!latest || latest.id <= narrated.current) return;
    narrated.current = latest.id;
    const line = narrate(latest);
    if (line) setSubtitle(line);
  }, [events]);

  const recent = events.filter(
    (event) => Date.now() - event.at * 1000 < 120_000,
  );
  const busyHelpers = [
    ...new Set(
      recent
        .filter((event) => event.actor !== 'friday' && running)
        .map((event) => ACTOR_NAMES[event.actor] ?? event.actor),
    ),
  ];
  const screenshot = [...recent].reverse().find((event) => event.image)?.image;
  const listening = phase === 'hearing';
  const clock = useClock(listening || running);

  const mode: OrbMode =
    phase === 'speaking'
      ? 'speaking'
      : running && busyHelpers.length
        ? 'working'
        : phase === 'thinking' || phase === 'transcribing'
          ? 'thinking'
          : phase === 'listening' || phase === 'hearing'
            ? 'listening'
            : 'idle';
  const title = paused
    ? 'Friday is paused'
    : pendingApproval
      ? 'I need your OK'
      : phase === 'speaking'
        ? 'Speaking…'
        : running && busyHelpers.length
          ? `Working with ${busyHelpers.join(' & ')}…`
          : phase === 'thinking'
            ? 'Thinking…'
            : phase === 'transcribing'
              ? 'Got it…'
              : listening
                ? 'Listening…'
                : 'Hold Caps Lock to talk';
  const line =
    error ||
    (paused
      ? 'Nothing runs while paused. Tap Resume to continue.'
      : pendingApproval
        ? 'Say yes or no, or use the buttons.'
        : subtitle ||
          (listening
            ? 'Let go of Caps Lock when you are done.'
            : 'Friday hears you only while the key is held.'));
  const status =
    voiceReady === false && !live.current?.connected
      ? { tone: 'warn' as const, label: 'Voice needs GNANI_API_KEY' }
      : !helpersOnline
        ? { tone: 'warn' as const, label: 'Helpers offline' }
        : { tone: 'ok' as const, label: 'Connected' };

  if (variant === 'notch') {
    const notchState: NotchState = paused
      ? 'offline'
      : pendingApproval
        ? 'asking'
        : phase === 'hearing'
          ? 'hearing'
          : phase === 'speaking'
            ? 'speaking'
            : running || phase === 'thinking' || phase === 'transcribing'
              ? 'thinking'
              : quiet
                ? 'quiet'
                : 'idle';
    return (
      <Notch
        state={notchState}
        linked={helpersOnline}
        level={level}
        spectrum={spectrum}
        note={
          paused
            ? 'Friday is paused. Resume it from the main page.'
            : pendingApproval
              ? `OK to ${pendingApproval.summary.replace(/[.!]+$/, '')}?`
              : error || undefined
        }
        onDecide={(approve) => void decide(approve)}
      />
    );
  }
  return (
    <div className={`friday-stage mode-${mode}`}>
      <Orb
        mode={mode}
        level={level}
        spectrum={spectrum}
        helpers={busyHelpers}
      />
      <Header
        status={status}
        clock={clock}
        language={language}
        onLanguage={onLanguage}
        onExit={onExit}
        onStop={
          running || phase === 'speaking'
            ? () => void stopEverything()
            : undefined
        }
        onNewChat={messages.length && !running ? newConversation : undefined}
      />
      <section className="stage-title" aria-live="polite">
        <h1>{title}</h1>
        <p className={error ? 'error' : ''}>{line}</p>
        {paused && (
          <button
            type="button"
            className="stage-resume"
            onClick={() => {
              setError('');
              onResume();
            }}
          >
            Resume
          </button>
        )}
      </section>
      <button
        type="button"
        className={`stage-orb-hit ${listening ? 'on' : ''}`}
        aria-label="How to talk to Friday"
        disabled={!loaded}
        onClick={() => setSubtitle('Hold Caps Lock while you speak.')}
      />
      {recent.length > 0 && (
        <aside
          className="stage-live"
          aria-label="What the helper Dots are doing"
        >
          <header>
            <span className="stage-live-dot" />
            Live{busyHelpers.length ? ` · ${busyHelpers.join(', ')}` : ''}
          </header>
          {screenshot && (
            <img
              src={`data:image/jpeg;base64,${screenshot}`}
              alt="The helper's screen right now"
            />
          )}
          <ol>
            {recent
              .filter(
                (event) => event.state !== 'running' || event === recent.at(-1),
              )
              .slice(-6)
              .map((event) => (
                <li key={event.id} className={event.state}>
                  {event.state === 'running' ? (
                    <Loader2 size={13} className="spin" />
                  ) : event.state === 'done' ? (
                    <CircleCheck size={13} />
                  ) : (
                    <CircleX size={13} />
                  )}
                  <b>{ACTOR_NAMES[event.actor] ?? event.actor}</b>
                  <span>{event.detail}</span>
                </li>
              ))}
          </ol>
        </aside>
      )}
      {pendingApproval && (
        <div
          className="stage-approval"
          role="dialog"
          aria-label="Friday needs your OK"
        >
          <p className="stage-approval-title">Friday needs your OK</p>
          <p className="stage-approval-text">{pendingApproval.summary}</p>
          <div className="stage-approval-actions">
            <button
              type="button"
              disabled={running}
              onClick={() => void decide(false)}
            >
              Decline
            </button>
            <button
              type="button"
              className="approve"
              disabled={running}
              onClick={() => void decide(true)}
            >
              Approve
            </button>
          </div>
        </div>
      )}
      <footer className="stage-footer">
        <div className="stage-captions" aria-live="polite">
          {captions.slice(-2).map((item, index) => (
            <p key={`${captions.length}-${index}`} className={item.who}>
              <b>{item.who === 'you' ? 'You' : 'Friday'}</b>
              <span>{item.text}</span>
            </p>
          ))}
        </div>
      </footer>
    </div>
  );
}
