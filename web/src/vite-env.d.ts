/// <reference types="vite/client" />

interface CcRenderStats {
  commits: number;
  actual: number;
  worst: number;
  children: number;
}

interface CcAudit {
  seedMessages: (count: number) => void;
  seedTable: (rows: number) => void;
  burst: (tokens: number, gapMs?: number) => Promise<void>;
}

interface Window {
  __ccAudit?: CcAudit;
  __ccRenderStats?: CcRenderStats;
}
