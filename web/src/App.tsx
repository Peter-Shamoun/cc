import { ThemeProvider } from './lib/theme';
import { AppShell } from './shell/AppShell';
import { SharedView } from './shell/SharedView';
import { NotFoundPage, SignedOutPage } from './shell/StatusPage';
import { routeFromLocation } from './shell/routes';

/** Providers and the path routes. The app shell (layout, sidebar, composer, header, hash routing) lives in src/shell/. */
export default function App() {
  const route = typeof window === 'undefined' ? { kind: 'app' as const } : routeFromLocation(window.location.pathname, window.location.search, import.meta.env.BASE_URL);
  return (
    <ThemeProvider>
      {route.kind === 'shared' ? <SharedView shareId={route.id} />
        : route.kind === 'signed-out' ? <SignedOutPage expired={route.expired} signInUrl={(import.meta.env.VITE_SIGN_IN_URL as string | undefined) || undefined} />
        : route.kind === 'not-found' ? <NotFoundPage />
        : <AppShell />}
    </ThemeProvider>
  );
}
