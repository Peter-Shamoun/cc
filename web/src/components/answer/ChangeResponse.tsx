import { useEffect, useRef, useState } from 'react';

/**
 * "Ask to change response…" (ChatGPT's regenerate menu): one quiet input under the action row. Enter sends the
 * instruction as the next turn (the contract's regenerate has no free-text field yet; see NOTES), Escape or an empty
 * blur closes it. The text never leaves memory until it's sent like any other message (no storage, no URL).
 */
export function ChangeResponse({ onSubmit, onClose }: { onSubmit: (text: string) => void; onClose: (cancelled: boolean) => void }) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <form className="mt-2 font-sans" data-testid="change-response"
      onSubmit={(e) => { e.preventDefault(); const t = value.trim(); if (!t) return; onSubmit(t); onClose(false); }}>
      <input ref={ref} value={value} onChange={(e) => setValue(e.target.value)} maxLength={500}
        aria-label="Ask to change response" placeholder="Ask to change response…"
        onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); onClose(true); } }}
        onBlur={() => { if (!value.trim()) onClose(false); }}
        className="w-full max-w-md rounded-md border border-line bg-transparent px-2.5 py-1.5 text-sm text-fg placeholder:text-fg-subtle focus:border-fg-subtle focus:outline-none" />
    </form>
  );
}
