import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ToastProvider, useToast } from './primitives';

afterEach(() => { cleanup(); vi.useRealTimers(); });

function Pusher({ onExpire }: { onExpire: () => void }) {
  const toast = useToast();
  return <button type="button" onClick={() => toast({ text: 'Deleted “Q3”', action: { label: 'Undo', run: () => {} }, onExpire })}>push</button>;
}

describe('toasts', () => {
  it('pause while hovered or focused and expire after the pointer leaves', () => {
    vi.useFakeTimers();
    const onExpire = vi.fn();
    render(<ToastProvider><Pusher onExpire={onExpire} /></ToastProvider>);
    fireEvent.click(screen.getByText('push'));
    act(() => { vi.advanceTimersByTime(4000); });
    fireEvent.mouseEnter(screen.getByTestId('toast'));
    act(() => { vi.advanceTimersByTime(20000); });
    expect(onExpire).not.toHaveBeenCalled();
    fireEvent.mouseLeave(screen.getByTestId('toast'));
    act(() => { vi.advanceTimersByTime(1400); });
    expect(onExpire).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(200); });
    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('toast')).toBeNull();
  });
  it('keeps later toasts running after a hovered one is clicked away', async () => {
    vi.useFakeTimers();
    const onExpire = vi.fn();
    render(<ToastProvider><Pusher onExpire={onExpire} /></ToastProvider>);
    fireEvent.click(screen.getByText('push'));
    fireEvent.mouseEnter(screen.getByTestId('toast'));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByText('push'));
    act(() => { vi.advanceTimersByTime(5100); });
    expect(onExpire).toHaveBeenCalledTimes(1);
  });
});
