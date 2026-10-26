import '@testing-library/jest-dom/vitest';
import { preloadMarkdown } from '../components/parts/Markdown';

// The markdown renderer is a lazy chunk in the app (QA-P8); load it up front so render tests stay synchronous.
await preloadMarkdown();

// jsdom has no Element.scrollTo / scrollIntoView; the shell calls them after each turn.
if (typeof Element !== 'undefined') {
  if (!Element.prototype.scrollTo) Element.prototype.scrollTo = function scrollTo() {} as Element['scrollTo'];
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function scrollIntoView() {};
}
