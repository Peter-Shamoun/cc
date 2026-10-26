import { test as base, expect } from '@playwright/test';
import { distRoutes } from './static.mjs';
export const test = base.extend({
  context: async ({ context }, use) => {
    await distRoutes(context);
    await use(context);
  },
});
export { expect };
