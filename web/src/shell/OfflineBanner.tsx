import { WifiOff } from 'lucide-react';

/**
 * Shown above the composer while the browser is offline (answer team's useOnline). ChatGPT and Claude
 * keep the draft and pause sending rather than failing the request; we do the same.
 */
export function OfflineBanner() {
  return (
    <div role="status" data-testid="offline-banner"
      className="mx-auto mb-2 flex w-fit max-w-full items-center gap-2 rounded-full border border-line bg-elev px-3.5 py-1.5 text-[13px] text-fg-muted shadow-sm motion-safe:animate-[cc-drop_180ms_ease-out]">
      <WifiOff size={14} aria-hidden className="shrink-0" />
      <span>You’re offline. Sending resumes when you reconnect.</span>
    </div>
  );
}
