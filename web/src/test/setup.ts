import '@testing-library/jest-dom/vitest';

// jsdom has no Element.scrollTo / scrollIntoView; the shell calls them after each turn.
if (typeof Element !== 'undefined') {
  if (!Element.prototype.scrollTo) Element.prototype.scrollTo = function scrollTo() {} as Element['scrollTo'];
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function scrollIntoView() {};
}
