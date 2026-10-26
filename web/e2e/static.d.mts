import type { BrowserContext } from '@playwright/test';
export const API_PORT: number;
export const LIVE: string;
export const MOCK: string;
export const DIST: string;
export function distRoutes(context: BrowserContext): Promise<void>;
