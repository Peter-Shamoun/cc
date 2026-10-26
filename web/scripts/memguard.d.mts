export type SlotInfo = { pid: number; label: string; cmd: string; at: string };
export type Acquired = { slot: number; file: string; release: () => void };
export type GuardOpts = { minGiB: number; slots: number; timeoutS: number; label: string; dir: string; cmd: string[] };
export function availableGiB(meminfo: string): number | null;
export function readAvailableGiB(read?: () => string): number | null;
export function pidAlive(pid: number): boolean;
export function tryAcquire(o: { dir: string; slots: number; info: SlotInfo; isAlive?: (pid: number) => boolean; fsx?: typeof import('node:fs') }): Acquired | { holders: SlotInfo[]; release?: undefined };
export function parseArgs(argv: string[], env?: Record<string, string | undefined>): GuardOpts;
export function waitForTurn(o: GuardOpts, deps?: { now?: () => number; sleep?: (ms: number) => Promise<void>; avail?: () => number | null; log?: (s: string) => void; acquire?: typeof tryAcquire }): Promise<Acquired>;
