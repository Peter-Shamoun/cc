/** Inline image box. Matches the chat column cap (768) and the spec max height (480). */
export const IMAGE_MAX_WIDTH = 768;
export const IMAGE_MAX_HEIGHT = 480;

/**
 * Stable display box for an image part. Invalid sizes fall back to the cap
 * (so a 0×0 part still reserves space), and huge sizes scale down uniformly
 * so the skeleton, error tile, and loaded image share one rectangle.
 */
export function imageDisplayBox(width: number, height: number): { width: number; height: number } {
  const w = Number.isFinite(width) && width > 0 ? width : IMAGE_MAX_WIDTH;
  const h = Number.isFinite(height) && height > 0 ? height : IMAGE_MAX_HEIGHT;
  const scale = Math.min(1, IMAGE_MAX_WIDTH / w, IMAGE_MAX_HEIGHT / h);
  return {
    width: Math.max(1, Math.round(w * scale)),
    height: Math.max(1, Math.round(h * scale)),
  };
}
