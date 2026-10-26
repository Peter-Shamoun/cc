import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ShareDialog, type ShareLinkApi } from './ShareDialog';
import type { ShareLink } from '../contract/v1.gen';

afterEach(cleanup);

const link = (includeData: boolean): ShareLink => ({ shareId: 'sh_test', conversationId: 'c_1', url: '/shared/sh_test', scope: 'organization', includeData, createdAt: '2026-10-25T20:00:00Z' });

function setup(initial: ShareLink | null) {
  const api: ShareLinkApi = {
    get: vi.fn(async () => initial),
    set: vi.fn(async (d: boolean) => link(d)),
    revoke: vi.fn(async () => true),
    copy: vi.fn(async () => true),
  };
  render(<ShareDialog title="Denial rate by payer" messageCount={2} onClose={() => {}} onCopyMarkdown={() => {}} onDownloadMarkdown={() => {}} onPrint={() => {}} link={api} />);
  return api;
}

describe('share link (contract 1.11.0)', () => {
  it('creates a link without data by default, copies the absolute URL, and can revoke it', async () => {
    const api = setup(null);
    const create = await screen.findByRole('button', { name: /Create link/ });
    await waitFor(() => expect(create).toBeEnabled());
    const sw = screen.getByRole('switch', { name: 'Include result data' });
    expect(sw).not.toBeChecked();
    fireEvent.click(create);
    await screen.findByTestId('share-url');
    expect(api.set).toHaveBeenCalledWith(false, undefined);
    expect(api.copy).toHaveBeenCalledWith(`${window.location.origin}/shared/sh_test`);
    expect((screen.getByTestId('share-url') as HTMLInputElement).value).toBe(`${window.location.origin}/shared/sh_test`);
    expect(screen.getByRole('button', { name: /Copied/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stop sharing' }));
    await screen.findByRole('button', { name: /Create link/ });
    expect(api.revoke).toHaveBeenCalled();
    expect(screen.queryByTestId('share-url')).toBeNull();
  });

  it('shows an existing link with its data setting, and the toggle updates it', async () => {
    const api = setup(link(false));
    await screen.findByTestId('share-url');
    const sw = screen.getByRole('switch', { name: 'Include result data' });
    fireEvent.click(sw);
    await waitFor(() => expect(api.set).toHaveBeenCalledWith(true, undefined));
    await waitFor(() => expect(sw).toBeChecked());
    expect(sw).toHaveAccessibleDescription(/Table previews/);
  });

  it('a failed request says so instead of failing silently', async () => {
    const api = setup(null);
    (api.set as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('share request failed: 500'));
    const create = await screen.findByRole('button', { name: /Create link/ });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    expect(await screen.findByText("Couldn't create the link. Try again.")).toBeInTheDocument();
  });
});
