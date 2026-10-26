import type { ImagePart } from '../../lib/contract';

export interface ImageFixture {
  id: string;
  part: ImagePart;
  /** fetchBlob rejects. */
  missing?: boolean;
  /** Expected reserved box after clamping. */
  box: { width: number; height: number };
}

const image = (id: string, extra: Partial<ImagePart>): ImagePart => ({
  type: 'image',
  id,
  blobId: id,
  mime: 'image/png',
  width: 640,
  height: 320,
  alt: id,
  ...extra,
});

export const imageFixtures: ImageFixture[] = [
  {
    id: 'missing-blob',
    missing: true,
    box: { width: 640, height: 320 },
    part: image('missing-blob', { blobId: 'missing-blob', alt: 'Denied dollars by CARC' }),
  },
  {
    id: 'huge-dimensions',
    box: { width: 192, height: 480 },
    part: image('huge-dimensions', { width: 8000, height: 20_000, alt: 'Very tall plot' }),
  },
  {
    id: 'zero-dimensions',
    box: { width: 768, height: 480 },
    part: image('zero-dimensions', { width: 0, height: 0, alt: 'Empty box' }),
  },
  {
    id: 'negative-dimensions',
    box: { width: 768, height: 480 },
    part: image('negative-dimensions', { width: -20, height: Number.NaN, alt: 'Bad box' }),
  },
  {
    id: 'xss-alt-caption',
    box: { width: 400, height: 200 },
    part: image('xss-alt-caption', {
      width: 400,
      height: 200,
      alt: '<img src=x onerror="alert(1)">',
      caption: '<script>alert(1)</script>',
    }),
  },
  {
    id: 'normal-png',
    box: { width: 640, height: 320 },
    part: image('normal-png', { alt: 'Bar chart of denial share', caption: 'Figure 1 · synthetic' }),
  },
];
