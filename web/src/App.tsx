import { ThemeProvider } from './lib/theme';
import { AppShell } from './shell/AppShell';
import { SharedView } from './shell/SharedView';
import { shareIdFromPath } from './contract/share';

/** Providers and the one path route. The app shell (layout, sidebar, composer, header, hash routing) lives in src/shell/. */
export default function App() {
  const shareId = typeof window === 'undefined' ? null : shareIdFromPath(window.location.pathname);
  return (
    <ThemeProvider>
      {shareId ? <SharedView shareId={shareId} /> : <AppShell />}
    </ThemeProvider>
  );
}
