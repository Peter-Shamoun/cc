/**
 * Fail-safe SSE decoding for the chat stream (contract 1.12.0, CONTRACT.md §1 and §3).
 *
 * Frames are separated by a blank line. `data:` lines are joined with "\n", and `id:` is `<messageId>:<seq>`. A frame
 * whose data isn't JSON, or isn't an object with a string `type`, is **skipped and counted**. It never throws, so one
 * bad frame can't kill a stream; the reducer then drops anything malformed or out of order (`wellFormed`, seq).
 * Comment lines (`:`) and frames without data (keep-alives) are ignored. CRLF and lone CR line endings are
 * accepted.
 */
export interface SseFrame { id?: string; event: { type: string } & Record<string, unknown> }

export class SseDecoder {
  private buf = '';
  /** Frames dropped as malformed so far (for a dev-only warning; never log their content, it may be PHI). */
  malformed = 0;

  /** Feed a decoded text chunk; returns the complete frames it finished. */
  push(chunk: string): SseFrame[] {
    this.buf += chunk;
    // a trailing CR may be the first half of a CRLF split across chunks: hold it back
    const hold = this.buf.endsWith('\r') ? '\r' : '';
    const norm = (hold ? this.buf.slice(0, -1) : this.buf).replace(/\r\n?/g, '\n');
    const lastSep = norm.lastIndexOf('\n\n');
    if (lastSep < 0) {
      this.buf = norm + hold;
      return [];
    }
    this.buf = norm.slice(lastSep + 2) + hold;
    return norm.slice(0, lastSep).split('\n\n').flatMap((f) => this.frame(f));
  }

  /** Flush a trailing frame without its blank line (end of body). */
  end(): SseFrame[] {
    const rest = this.buf.replace(/\r\n?/g, '\n');
    this.buf = '';
    return rest.trim() ? this.frame(rest) : [];
  }

  private frame(text: string): SseFrame[] {
    let id: string | undefined;
    const data: string[] = [];
    for (const line of text.split('\n')) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'data') data.push(value);
      else if (field === 'id') id = value;
    }
    if (!data.length) return [];
    try {
      const ev: unknown = JSON.parse(data.join('\n'));
      if (ev !== null && typeof ev === 'object' && !Array.isArray(ev) && typeof (ev as { type?: unknown }).type === 'string') {
        return [{ id, event: ev as SseFrame['event'] }];
      }
    } catch {
      /* counted below */
    }
    this.malformed += 1;
    return [];
  }
}

/** Decode a whole SSE body (string). */
export function parseSseText(text: string): { frames: SseFrame[]; malformed: number } {
  const d = new SseDecoder();
  const frames = [...d.push(text), ...d.end()];
  return { frames, malformed: d.malformed };
}

/** Fail-safe async SSE reader over a fetch body. Drop-in for a `JSON.parse`-per-frame parser. */
export async function* readSse(body: ReadableStream<Uint8Array>, decoder = new SseDecoder()): AsyncIterable<SseFrame> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    yield* decoder.push(dec.decode(value, { stream: true }));
  }
  yield* decoder.push(dec.decode());
  yield* decoder.end();
}
