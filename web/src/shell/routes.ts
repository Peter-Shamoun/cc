/**
 * Path routes. The app itself uses hash routes (`#/c/<id>`) under one path, the base. Anything else is a known
 * page or a 404, like ChatGPT/Claude, instead of silently showing an empty chat for a mistyped link.
 */
export type Route =
  | { kind: 'app' }
  | { kind: 'signed-out'; expired: boolean }
  | { kind: 'not-found' };

export const SIGNED_OUT = 'signed-out';

export function routeFromLocation(pathname: string, search: string, base = '/'): Route {
  const b = base.endsWith('/') ? base : `${base}/`;
  if (pathname === b || pathname === b.slice(0, -1) || pathname === `${b}index.html`) return { kind: 'app' };
  if (pathname === `${b}${SIGNED_OUT}` || pathname === `${b}${SIGNED_OUT}/`) {
    return { kind: 'signed-out', expired: new URLSearchParams(search).get('reason') === 'expired' };
  }
  return { kind: 'not-found' };
}

/** Where to land after signing out (or when a session expires) when the deployment has no sign-in page. */
export function signedOutUrl(base = '/', expired = false): string {
  const b = base.endsWith('/') ? base : `${base}/`;
  return `${b}${SIGNED_OUT}${expired ? '?reason=expired' : ''}`;
}
