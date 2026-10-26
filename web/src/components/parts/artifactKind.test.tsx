import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ArtifactCard, artifactIcon } from './misc';
import type { ArtifactPart } from '../../lib/contract';

afterEach(cleanup);
const part = (kind: string): ArtifactPart => ({ type: 'artifact', id: 'p1', artifactId: 'af_9', version: 1, kind, title: 'Payer mix', content: { markdown: '# Hi' } } as unknown as ArtifactPart);

describe('ArtifactCard: open ArtifactPart.kind enum', () => {
  it('known kinds keep their own icon and label', () => {
    render(<ArtifactCard part={part('chart')} onOpen={() => {}} />);
    expect(screen.getByTestId('artifact-kind-icon').hasAttribute('data-kind-known')).toBe(true);
    expect(screen.getByTestId('artifact-card').textContent).toMatch(/chart · Click to open/);
    expect(artifactIcon('report')).not.toBe(artifactIcon('notebook'));
  });
  it('an unknown kind renders a generic artifact icon and label instead of crashing', () => {
    const onOpen = vi.fn();
    render(<ArtifactCard part={part('notebook')} onOpen={onOpen} />);
    const icon = screen.getByTestId('artifact-kind-icon');
    expect(icon.hasAttribute('data-kind-known')).toBe(false);
    expect(icon.querySelector('svg')).toBeTruthy();
    expect(screen.getByTestId('artifact-card').textContent).toMatch(/Artifact · Click to open/);
    expect(screen.getByTestId('artifact-version').textContent).toBe('v1');
    fireEvent.click(screen.getByTestId('artifact-card'));
    expect(onOpen).toHaveBeenCalledOnce();
    // Prototype keys are not kinds.
    expect(artifactIcon('constructor')).toBe(artifactIcon('notebook'));
  });
});
