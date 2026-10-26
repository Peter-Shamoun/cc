/** Hover timestamp for a message (QA-S23, Claude-style): time only for today, date + time this year,
 *  date with year otherwise. `full` goes in the tooltip. Invalid input gives null (nothing rendered). */
export function messageTime(iso: string | undefined, now: Date = new Date(), locale?: string): { label: string; full: string } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const sameDay = d.toDateString() === now.toDateString();
  const sameYear = d.getFullYear() === now.getFullYear();
  const opts: Intl.DateTimeFormatOptions = sameDay
    ? { hour: 'numeric', minute: '2-digit' }
    : sameYear
      ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric', year: 'numeric' };
  return {
    label: new Intl.DateTimeFormat(locale, opts).format(d),
    full: new Intl.DateTimeFormat(locale, { dateStyle: 'full', timeStyle: 'short' }).format(d),
  };
}
