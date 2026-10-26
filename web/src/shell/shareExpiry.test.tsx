import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ShareDialog, type ShareLinkApi } from './ShareDialog';
import type { ShareExpiryDays, ShareLink } from '../contract/v1.gen';

afterEach(() => { cleanup(); vi.useRealTimers(); });

const DAY = 86_400_000;
const link = (o: Partial<ShareLink> = {}): ShareLink => {
  const days = (o.expiresInDays ?? 30) as number;
  return { shareId: 'sh_a', conversationId: 'c_1', url: '/shared/sh_a', scope: 'organization', includeData: false,
    createdAt: new Date().toISOString(), expiresAt: new Date(Date.now() + days * DAY - 60_000).toISOString(), expiresInDays: days, ...o } as ShareLink;
};
const expiry = { options: [7, 30, 90] as ShareExpiryDays[], default: 30 as ShareExpiryDays };

function setup(initial: ShareLink | null, set: ShareLinkApi['set'] = vi.fn(async (d: boolean, n?: ShareExpiryDays) => link({ includeData: d, expiresInDays: n ?? 30 })), withExpiry = true) {
  const api: ShareLinkApi = { get: vi.fn(async () => initial), set, revoke: vi.fn(async () => true), copy: vi.fn(async () => true) };
  render(<ShareDialog title="Denial rate by payer" messageCount={2} onClose={() => {}} onCopyMarkdown={() => {}} onDownloadMarkdown={() => {}} onPrint={() => {}} link={api} expiry={withExpiry ? expiry : undefined} />);
  return api;
}

describe('share link expiry and rotation (contract 1.15.0)', () => {
  it('the picker offers the capability options, defaults to 30, and creates with the chosen lifetime', async () => {
    const api = setup(null);
    const pick = await screen.findByRole('combobox', { name: 'Link expires after' });
    expect((pick as HTMLSelectElement).value).toBe('30');
    expect([...(pick as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(['7 days', '30 days', '90 days']);
    fireEvent.change(pick, { target: { value: '7' } });
    const create = screen.getByRole('button', { name: /Create link/ });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await screen.findByTestId('share-url');
    expect(api.set).toHaveBeenCalledWith(false, 7);
    expect(screen.getByTestId('share-dates')).toHaveTextContent(/Expires .+, in 7 days/);
  });

  it('changing the lifetime of an existing link restarts it; an existing link shows its own lifetime', async () => {
    const api = setup(link({ expiresInDays: 90 }));
    await screen.findByTestId('share-url');
    const pick = screen.getByRole('combobox', { name: 'Link expires after' }) as HTMLSelectElement;
    expect(pick.value).toBe('90');
    expect(screen.getByTestId('share-dates')).toHaveTextContent('in 90 days');
    fireEvent.change(pick, { target: { value: '30' } });
    await waitFor(() => expect(api.set).toHaveBeenCalledWith(false, 30));
    await waitFor(() => expect(screen.getByTestId('share-dates')).toHaveTextContent('in 30 days'));
  });

  it('turning data on re-sends the chosen expiry, shows the replaced link and says the old one stopped working', async () => {
    const set = vi.fn(async (d: boolean) => link({ includeData: d, shareId: 'sh_b', url: '/shared/sh_b', rotated: d }));
    setup(link(), set);
    await screen.findByTestId('share-url');
    expect(screen.queryByTestId('share-rotated')).toBeNull();
    expect(screen.getByText(/SQL is not shared\. Turning this on replaces the link\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Include result data' }));
    await screen.findByTestId('share-rotated');
    expect(set).toHaveBeenCalledWith(true, 30);
    expect((screen.getByTestId('share-url') as HTMLInputElement).value).toBe(`${window.location.origin}/shared/sh_b`);
    expect(screen.getByRole('status')).toHaveTextContent('New link created. The old link no longer works.');
    expect(screen.getByText(/charts and SQL are shared/)).toBeInTheDocument();
  });

  it('a pre-1.15 server (no shareExpiry, no expiresAt) shows no picker and no expiry date', async () => {
    setup(link({ expiresAt: undefined, expiresInDays: undefined }), undefined, false);
    await screen.findByTestId('share-url');
    expect(screen.queryByRole('combobox', { name: 'Link expires after' })).toBeNull();
    expect(screen.getByTestId('share-dates')).not.toHaveTextContent('Expires');
  });
});
