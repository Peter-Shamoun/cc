import { useLayoutEffect, useRef } from 'react';

/** A ref that always holds the latest committed value, for event handlers and timers registered once.
 *  Assigned in a layout effect (not during render), so a render React throws away never leaks into it. */
export function useLatest<T>(value: T) {
  const ref = useRef(value);
  useLayoutEffect(() => { ref.current = value; });
  return ref;
}
