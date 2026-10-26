import { chartFixtures } from './charts';
import { imageFixtures } from './images';
import { markdownFixtures } from './markdown';
import { tableFixtures } from './tables';

export interface RenderFixtureRef {
  id: string;
  kind: 'markdown' | 'table' | 'chart' | 'image';
}

/** Every torture case the renderer must survive. Ids are unique. */
export const renderFixtures: RenderFixtureRef[] = [
  ...markdownFixtures.map((f) => ({ id: f.id, kind: 'markdown' as const })),
  ...tableFixtures.map((f) => ({ id: f.id, kind: 'table' as const })),
  ...chartFixtures.map((f) => ({ id: f.id, kind: 'chart' as const })),
  ...imageFixtures.map((f) => ({ id: f.id, kind: 'image' as const })),
];

export const FIXTURE_COUNT = renderFixtures.length;

export { chartFixtures } from './charts';
export { imageFixtures } from './images';
export { markdownFixtures } from './markdown';
export { tableFixtures } from './tables';
