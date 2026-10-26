import { ThemeProvider } from './lib/theme';
import { AppShell } from './shell/AppShell';

/** Providers only. The app shell (layout, sidebar, composer, header, routing) lives in src/shell/. */
export default function App() {
  return (
    <ThemeProvider>
      <AppShell />
    </ThemeProvider>
  );
}
