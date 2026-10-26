import { useEffect, useState } from 'react';
import { flushSync } from 'react-dom';

/**
 * True between beforeprint and afterprint. The update is flushed synchronously so the print snapshot already has
 * every turn rendered (the long-thread virtualizer steps aside) and prints in light colors even in dark mode.
 */
export function usePrinting(): boolean {
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    let wasDark = false;
    const before = () => {
      const root = document.documentElement;
      wasDark = root.classList.contains('dark');
      if (wasDark) root.classList.remove('dark');
      flushSync(() => setPrinting(true));
    };
    const after = () => {
      if (wasDark) document.documentElement.classList.add('dark');
      setPrinting(false);
    };
    window.addEventListener('beforeprint', before);
    window.addEventListener('afterprint', after);
    return () => { window.removeEventListener('beforeprint', before); window.removeEventListener('afterprint', after); };
  }, []);
  return printing;
}
