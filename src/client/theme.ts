export type ThemePreference = 'system' | 'light' | 'dark';
const key = 'opendots-theme';
// The choice for this page, even if storage refuses to keep it.
let chosen: ThemePreference | undefined;
const query = () => window.matchMedia('(prefers-color-scheme: dark)');
// FRIDAY: Aqua Nebula's dark theme is the default; 'system' is stored when
// chosen. Storage can be unavailable (private windows, blocked site data);
// the theme is then dark for this visit.
export function themePreference(): ThemePreference {
  if (chosen) return chosen;
  try {
    const value = localStorage.getItem(key);
    if (value === 'light' || value === 'dark' || value === 'system')
      return value;
  } catch {
    // Fall through to the default theme.
  }
  return 'dark';
}
export function resolveTheme(preference: ThemePreference, systemDark: boolean) {
  return preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;
}
function apply() {
  const theme = resolveTheme(themePreference(), query().matches);
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'dark' ? '#061826' : '#f3f9fd');
}
export function setThemePreference(preference: ThemePreference) {
  chosen = preference;
  try {
    localStorage.setItem(key, preference);
  } catch {
    // Applies for this visit only.
  }
  apply();
}
export function watchSystemTheme() {
  apply();
  query().addEventListener('change', apply);
}
