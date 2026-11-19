import { useSyncExternalStore } from 'react';
export type Theme = 'light' | 'dark';
const eventName = 'tindalos-theme-change';
function snapshot(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}
function subscribe(listener: () => void) {
  window.addEventListener(eventName, listener);
  const synchronize = (event: StorageEvent) => {
    if (event.key !== 'tindalos-theme') return;
    initializeTheme();
    listener();
  };
  window.addEventListener('storage', synchronize);
  return () => {
    window.removeEventListener(eventName, listener);
    window.removeEventListener('storage', synchronize);
  };
}
export function initializeTheme() {
  try {
    document.documentElement.dataset.theme =
      localStorage.getItem('tindalos-theme') === 'dark' ? 'dark' : 'light';
  } catch {
    document.documentElement.dataset.theme = 'light';
  }
}
export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem('tindalos-theme', theme);
  } catch {
    /* Theme remains available without storage. */
  }
  window.dispatchEvent(new Event(eventName));
}
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, snapshot, () => 'light' as Theme);
  return [theme, () => setTheme(theme === 'light' ? 'dark' : 'light')] as const;
}
