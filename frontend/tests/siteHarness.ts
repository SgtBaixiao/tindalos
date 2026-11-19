import { act } from 'react';
import { expect, vi } from 'vitest';

/** Wait for the real lazy route and its effects, rather than a fixed loading timer. */
export async function settleRoute(container: HTMLElement): Promise<void> {
  await vi.waitFor(
    async () => {
      await act(async () => {});
      expect(container.querySelector('.sx-route-loading')).toBeNull();
      expect(container.querySelector('.sx-route-error')).toBeNull();
    },
    { timeout: 3000, interval: 10 },
  );
}

export async function navigateRoute(container: HTMLElement, hash: string): Promise<void> {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  await settleRoute(container);
}
