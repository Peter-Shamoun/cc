/** `yyyy-mm-dd` of the user's local calendar day. Not `toISOString().slice(0, 10)`, which is the UTC day: after
 * 5 PM Pacific (4 PM during DST) that's already tomorrow. */
export function localYmd(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
