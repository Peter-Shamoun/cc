import { createContext, useContext, useEffect, useState } from 'react';
import type { AnalystAdapter } from './types';

export const AdapterCtx = createContext<AnalystAdapter | null>(null);
export const useAdapter = () => useContext(AdapterCtx);

/** Resolve an opaque blob id to a short-lived object URL (revoked on unmount). */
export function useBlobUrl(id: string | undefined) {
  const adapter = useAdapter();
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!id || !adapter) return;
    let u: string | null = null;
    let alive = true;
    adapter.fetchBlob(id).then(
      (b) => {
        if (!alive) return;
        u = URL.createObjectURL(b);
        setUrl(u);
      },
      () => alive && setErr(true),
    );
    return () => {
      alive = false;
      if (u) URL.revokeObjectURL(u);
    };
  }, [id, adapter]);
  return { url, err };
}
