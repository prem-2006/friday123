import type { AtRule, Declaration, Plugin, Rule } from 'postcss';
// Build-time dark theme. Every rule with colors gets a sibling rule under
// :root[data-theme='dark'] with only its color declarations, mapped by
// flipping perceptual (OKLab) lightness and keeping hue. New styles get a
// dark variant automatically, and cascade order is preserved because every
// dark rule gains the same specificity and sits right after its original.
// Wrap intentionally dark surfaces in /* theme: fixed */ ... /* theme: end */.
export const DARK = ":root[data-theme='dark']";
const toLinear = (c: number) =>
  c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const toSrgb = (c: number) =>
  c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
function rgbToOklab([r, g, b]: number[]) {
  const [lr, lg, lb] = [r, g, b].map(toLinear);
  const l = Math.cbrt(
    0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb,
  );
  const m = Math.cbrt(
    0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb,
  );
  const s = Math.cbrt(
    0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb,
  );
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}
function oklabToRgb([L, a, b]: number[]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((c) => Math.min(1, Math.max(0, toSrgb(c))));
}
function parseHex(hex: string) {
  // FRIDAY: rgb()/rgba() colors too, so the Friday screens get re-tinted.
  const functional = hex.match(/^rgba?\(([^)]*)\)$/);
  if (functional) {
    const parts = functional[1].split(/[\s,/]+/).filter(Boolean);
    const alpha = parts[3]?.endsWith('%')
      ? parseFloat(parts[3]) / 100
      : parts[3] === undefined
        ? undefined
        : Number(parts[3]);
    return { rgb: parts.slice(0, 3).map((c) => Number(c) / 255), alpha };
  }
  let value = hex.slice(1);
  if (value.length <= 4) value = [...value].map((char) => char + char).join('');
  const channels = value.match(/../g)!.map((pair) => parseInt(pair, 16) / 255);
  return { rgb: channels.slice(0, 3), alpha: channels[3] };
}
const hex = (rgb: number[], alpha?: number) =>
  '#' +
  [...rgb, ...(alpha === undefined ? [] : [alpha])]
    .map((c) =>
      Math.round(c * 255)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('');
// FRIDAY: the Aqua Nebula palette, darkest to lightest. Every color is
// re-tinted onto it: greys become steel blue and accents follow the palette
// at their own lightness, while reds, oranges and yellows keep their meaning
// and greens turn Lagoon teal (#4ca7a1).
export const NEBULA = ['#061826', '#1c4e75', '#2fa0c6', '#58c9f3', '#bde5ff'];
type Lch = [number, number, number];
const toLch = ([L, a, b]: number[]): Lch => [
  L,
  Math.hypot(a, b),
  Math.atan2(b, a),
];
const fromLch = ([L, c, h]: Lch) => [L, c * Math.cos(h), c * Math.sin(h)];
const nebulaStops = NEBULA.map((value) =>
  toLch(rgbToOklab(parseHex(value).rgb)),
);
const LAGOON_HUE = toLch(rgbToOklab(parseHex('#4ca7a1').rgb))[2];
// Chroma and hue of the palette at a lightness, fading to a pale tint at white.
function nebulaAt(L: number): [number, number] {
  const first = nebulaStops[0];
  const last = nebulaStops[nebulaStops.length - 1];
  if (L <= first[0]) return [first[1] * Math.max(L / first[0], 0.4), first[2]];
  if (L >= last[0]) {
    const t = (L - last[0]) / (1 - last[0]);
    return [last[1] + (0.02 - last[1]) * t, last[2]];
  }
  const i = nebulaStops.findIndex((stop) => stop[0] > L) - 1;
  const [from, to] = [nebulaStops[i], nebulaStops[i + 1]];
  const t = (L - from[0]) / (to[0] - from[0]);
  return [from[1] + (to[1] - from[1]) * t, from[2] + (to[2] - from[2]) * t];
}
function nebula(lab: number[]) {
  const [L, c, h] = toLch(lab);
  const degrees = ((h * 180) / Math.PI + 360) % 360;
  if (c > 0.04 && (degrees < 110 || degrees >= 345)) return lab;
  if (c > 0.04 && degrees < 175) return fromLch([L, c, LAGOON_HUE]);
  // Deep surfaces carry the full abyss tint; near-white text only a hint.
  const [tone, hue] = nebulaAt(L);
  const tint = 0.9 - 0.55 * Math.max(0, (L - 0.6) / 0.4);
  return fromLch([L, tone * Math.min(1, tint + c / 0.12), hue]);
}
// The light theme: same lightness, re-tinted onto the palette.
export function nebulaColor(value: string) {
  const { rgb, alpha } = parseHex(value);
  return hex(oklabToRgb(nebula(rgbToOklab(rgb))), alpha);
}
// White maps to Midnight Abyss, black to near-white glacier. The exponent
// lifts mid-tones so secondary text keeps AA contrast on the dark canvas.
export function darkColor(value: string) {
  const { rgb, alpha } = parseHex(value);
  const [L, a, b] = rgbToOklab(rgb);
  const next = 0.95 - 0.75 * L ** 1.3;
  return hex(oklabToRgb(nebula([next, a * 0.85, b * 0.85])), alpha);
}
// WCAG relative luminance and contrast, for checking generated pairs.
const luminance = (rgb: number[]) => {
  const [r, g, b] = rgb.map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export function contrast(a: string, b: string) {
  const [x, y] = [luminance(parseHex(a).rgb), luminance(parseHex(b).rgb)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
const withLightness = (value: string, next: (L: number) => number) => {
  const { rgb, alpha } = parseHex(value);
  const [L, a, b] = rgbToOklab(rgb);
  return hex(oklabToRgb([next(L), a, b]), alpha);
};
// Dark text sits on surfaces from #1b1b1b to about #2a2a2a; this floor
// keeps any text color at 4.5:1 or better there.
const TEXT_FLOOR = 0.72;
export const readableText = (value: string) =>
  withLightness(value, (L) => Math.max(L, TEXT_FLOOR));
const LIGHT_TEXT = '#eaf6ff';
const DARK_TEXT = '#061826';
// A rule that sets both its text and background must stay readable after
// mapping: pick the better text extreme, then darken a mid-tone background
// until the pair reaches AA.
export function readablePair(text: string, background: string) {
  if (contrast(text, background) >= 4.5) return { text, background };
  const best = [LIGHT_TEXT, DARK_TEXT].reduce((a, b) =>
    contrast(a, background) >= contrast(b, background) ? a : b,
  );
  if (contrast(best, background) >= 4.5) return { text: best, background };
  let next = background;
  while (contrast(LIGHT_TEXT, next) < 4.5)
    next = withLightness(next, (L) => L - 0.02);
  return { text: LIGHT_TEXT, background: next };
}
const named: Record<string, string> = { white: '#ffffff', black: '#000000' };
const colorToken = /#[0-9a-fA-F]{3,8}\b|\b(?:white|black)\b/g;
export function darkValue(prop: string, value: string) {
  // Shadows stay shadows: keep their alpha, drop their tint.
  const shadow = /shadow/.test(prop) || value.includes('drop-shadow(');
  return value.replace(colorToken, (token) => {
    const color = named[token] ?? token;
    if (!shadow) return darkColor(color);
    const { alpha } = parseHex(color);
    return hex([0.004, 0.03, 0.06], Math.min(1, (alpha ?? 0.35) * 1.6));
  });
}
// FRIDAY: the light value of a declaration, re-tinted onto the palette.
const anyColor = /#[0-9a-fA-F]{3,8}\b|\brgba?\([\d\s.,%/]*\)/g;
export function nebulaValue(value: string) {
  if (value.includes('url(')) return value;
  return value.replace(anyColor, (token) =>
    parseHex(token).rgb.some(Number.isNaN) ? token : nebulaColor(token),
  );
}
const colorProp =
  /^(color|background(-color|-image)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|fill|stroke|accent-color|caret-color|text-decoration(-color)?|box-shadow|text-shadow)$/;
// Every declaration of a color property gets a twin, even `none` or
// `var(--x)`: otherwise a later light rule without a literal color would
// lose to an earlier rule's dark twin and the cascade would invert.
const themed = (decl: Declaration) =>
  !decl.value.includes('url(') &&
  (new RegExp(colorToken.source).test(decl.value) ||
    (colorProp.test(decl.prop) && !decl.prop.startsWith('--')));
export function darkSelector(selector: string) {
  return selector
    .split(',')
    .map((part) => {
      const item = part.trim();
      if (item.startsWith(':root')) return DARK + item.slice(5);
      if (/^html\b/.test(item))
        return `html[data-theme='dark']${item.slice(4)}`;
      return `${DARK} ${item}`;
    })
    .join(', ');
}
const solid = (value: string) => {
  const token = value.trim();
  const color = named[token] ?? token;
  return /^#[0-9a-fA-F]{3,8}$/.test(color) ? color : undefined;
};
// Light values of custom properties, so `var(--x)` backgrounds can be
// checked against their text in dark (last definition wins, like :root).
type Variables = Map<string, string>;
const variable = /^var\(\s*(--[\w-]+)\s*\)$/;
function backgroundColor(value: string, variables: Variables) {
  const literal = solid(value);
  if (literal) return { color: literal, literal: true };
  const name = value.trim().match(variable)?.[1];
  const light = name && variables.get(name);
  return light ? { color: darkColor(light), literal: false } : undefined;
}
function darkDeclarations(colors: Declaration[], variables: Variables) {
  const mapped = colors.map((decl) =>
    decl.clone({ value: darkValue(decl.prop, decl.value) }),
  );
  const text = mapped.find((decl) => decl.prop === 'color');
  const textColor = text && solid(text.value);
  if (!text || !textColor) return mapped;
  const background = mapped.find(
    (decl) =>
      /^background(-color)?$/.test(decl.prop) &&
      backgroundColor(decl.value, variables),
  );
  if (!background) {
    text.value = readableText(textColor);
    return mapped;
  }
  const fill = backgroundColor(background.value, variables)!;
  const pair = readablePair(textColor, fill.color);
  text.value = pair.text;
  // A variable background stays a variable unless it must change for AA.
  if (fill.literal || pair.background !== fill.color)
    background.value = pair.background;
  return mapped;
}
// Generated rules skip fixed surfaces and everything inside them, so global
// rules (like a focus ring) keep their light values there.
export function excludeFixed(selector: string, containers: string[]) {
  if (!containers.length) return selector;
  const not = `:not(${containers.flatMap((item) => [item, `${item} *`]).join(', ')})`;
  return selector
    .split(',')
    .map((part) => {
      const item = part.trim();
      // :not() must come before any pseudo-element, including the legacy
      // single-colon forms and ones followed by states (::thumb:hover).
      const pseudo = item.search(
        /::[\w-]+|:(?:before|after|first-line|first-letter)(?![\w-])/,
      );
      return pseudo < 0
        ? `${item}${not}`
        : `${item.slice(0, pseudo)}${not}${item.slice(pseudo)}`;
    })
    .join(', ');
}
const DOTS = /\.mascot|\.dot-body/;
export function darkTheme(): Plugin {
  return {
    postcssPlugin: 'opendots-dark-theme',
    Once(root) {
      let fixed = false;
      const containers: string[] = [];
      const variables: Variables = new Map();
      const rules: { rule: Rule; fixed: boolean }[] = [];
      // FRIDAY: rules the palette pass leaves alone: the Dot characters and
      // `/* palette: exact */ ... /* palette: end */` regions.
      let exact = false;
      const kept = new Set<object>();
      root.each(function visit(node): void {
        if (node.type === 'comment') {
          // `/* theme: fixed .call-view */` names the surface it keeps.
          const start = node.text.match(/^\s*theme:\s*fixed\s+(.+?)\s*$/);
          if (start) {
            fixed = true;
            containers.push(start[1]);
          }
          if (/^\s*theme:\s*end\s*$/.test(node.text)) fixed = false;
          if (/^\s*palette:\s*exact\s*$/.test(node.text)) exact = true;
          if (/^\s*palette:\s*end\s*$/.test(node.text)) exact = false;
          return;
        }
        if (node.type === 'atrule') {
          // Animations interpolate their own colors; leave them alone.
          if (exact) kept.add(node);
          if (!/keyframes$/.test((node as AtRule).name))
            (node as AtRule).each(visit);
          return;
        }
        if (node.type === 'rule' && !node.selector.startsWith(DARK)) {
          rules.push({ rule: node, fixed });
          if (exact || DOTS.test(node.selector)) kept.add(node);
          if (!fixed)
            node.each((decl) => {
              if (decl.type === 'decl' && decl.prop.startsWith('--')) {
                const color = solid(decl.value);
                if (color) variables.set(decl.prop, color);
              }
            });
        }
      });
      for (const { rule, fixed } of rules) {
        // Fixed surfaces keep their light colors and get no dark rules.
        if (fixed) continue;
        const colors = rule.nodes.filter(
          (node): node is Declaration => node.type === 'decl' && themed(node),
        );
        if (!colors.length) continue;
        const dark = rule.clone({
          selector: excludeFixed(darkSelector(rule.selector), containers),
        });
        dark.removeAll();
        for (const decl of darkDeclarations(colors, variables))
          dark.append(decl);
        rule.after(dark);
      }
      // Light values last, so dark twins are derived from the originals.
      root.walkDecls((decl) => {
        for (
          let node: { parent?: unknown; type?: string } | undefined =
            decl.parent;
          node && node !== root;
          node = node.parent as typeof node
        )
          if (
            kept.has(node) ||
            (node.type === 'rule' && (node as Rule).selector.startsWith(DARK))
          )
            return;
        decl.value = nebulaValue(decl.value);
      });
    },
  };
}
darkTheme.postcss = true;
