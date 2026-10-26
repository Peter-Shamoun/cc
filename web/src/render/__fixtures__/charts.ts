import type { ChartPart } from '../../lib/contract';

export interface ChartFixture {
  id: string;
  part: ChartPart;
  /** Embed should use the canvas renderer. */
  canvas?: boolean;
  /** Spec text must not contain this host after sanitizing. */
  stripsHost?: string;
}

const bar = (id: string, extra?: Partial<ChartPart>, spec?: Record<string, unknown>): ChartPart => ({
  type: 'chart',
  id,
  library: 'vega-lite',
  title: id,
  alt: `Chart ${id}`,
  spec: spec ?? { mark: 'bar', encoding: { x: { field: 'a', type: 'nominal' }, y: { field: 'b', type: 'quantitative' } } },
  ...extra,
});

const hugeValues = Array.from({ length: 5000 }, (_, i) => ({ x: i, y: i % 97 }));

export const chartFixtures: ChartFixture[] = [
  {
    id: 'missing-data',
    part: bar('missing-data', { data: undefined }, { mark: 'bar', encoding: { x: { field: 'a', type: 'nominal' } } }),
  },
  {
    id: 'empty-values',
    part: bar('empty-values', { data: { name: 'empty', values: [] } }),
  },
  {
    id: 'huge-series',
    canvas: true,
    part: bar('huge-series', {
      title: 'Huge series',
      alt: 'Five thousand points',
      data: { name: 'huge', values: hugeValues },
    }, { mark: 'line', encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' } } }),
  },
  {
    id: 'remote-data-url',
    stripsHost: 'evil.test',
    part: bar('remote-data-url', { data: { name: 'local', values: [{ a: 'ok', b: 1 }] } }, {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { url: 'https://evil.test/exfil.json' },
      mark: 'bar',
      encoding: { x: { field: 'a', type: 'nominal' }, y: { field: 'b', type: 'quantitative' } },
    }),
  },
  {
    id: 'nested-layer-url',
    stripsHost: 'evil.test',
    part: bar('nested-layer-url', { data: { name: 'local', values: [{ a: 'ok', b: 2 }] } }, {
      layer: [
        { data: { url: 'https://evil.test/layer.json' }, mark: 'point', encoding: { x: { field: 'a', type: 'nominal' } } },
        { mark: { type: 'bar', href: 'javascript:alert(1)' }, encoding: { y: { field: 'b', type: 'quantitative' } } },
      ],
      transform: [{ lookup: 'a', from: { data: { url: 'https://evil.test/lookup.json' }, key: 'a' }, as: 'extra' }],
    }),
  },
  {
    id: 'usermeta-embed',
    stripsHost: 'evil.test',
    part: bar('usermeta-embed', { data: { name: 'local', values: [{ a: 'ok', b: 3 }] } }, {
      usermeta: { embedOptions: { loader: { baseURL: 'https://evil.test/' }, ast: false, defaultStyle: 'body{background:url(https://evil.test/x)}' } },
      mark: 'bar',
      data: { url: 'https://evil.test/top.json' },
    }),
  },
  {
    id: 'nulls-in-series',
    part: bar('nulls-in-series', {
      data: { name: 'gaps', values: [{ a: 'x,y', b: null }, { b: 'z"z', c: 2 }, { a: null, b: 0, c: true }] },
    }),
  },
  {
    id: 'invalid-spec',
    part: bar('invalid-spec', { spec: null as unknown as Record<string, unknown>, data: undefined }),
  },
  {
    id: 'theme-switch',
    part: {
      type: 'chart',
      id: 'theme-switch',
      library: 'vega-lite',
      title: 'Theme',
      alt: 'One bar used to check palette and dark grid',
      data: { name: 'theme', values: [{ a: 'Cascade', b: 0.18 }] },
      spec: {
        mark: 'bar',
        encoding: { x: { field: 'a', type: 'nominal' }, y: { field: 'b', type: 'quantitative' } },
      },
    },
  },
];
