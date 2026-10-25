// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { Verification as VerificationComponent } from '@/components/vote/Verification';
import { admissionRequest } from '@/lib/arena';
import { COMMAND_ID, requestBody } from '@/test/arena';
import { ADMISSION, stubChallenge } from '@/test/verification';

type Fetch = (path: string, init?: RequestInit) => Promise<Response>;
let Verification: typeof VerificationComponent;

function challengeScript(): HTMLScriptElement {
  const script = document.querySelector<HTMLScriptElement>('script[src]');
  if (script === null) {
    throw new Error('Expected the managed verification script');
  }
  expect(script.src).toBe('https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
  return script;
}

describe('Verification', () => {
  beforeEach(async () => {
    vi.resetModules();
    ({ Verification } = await import('@/components/vote/Verification'));
    sessionStorage.clear();
    document.head.replaceChildren();
    delete document.documentElement.dataset.theme;
    delete window.turnstile;
  });

  afterEach(cleanup);

  test.each([
    [280, 'light', 'compact'],
    [500, 'dark', 'flexible'],
  ] as const)('automatically renders a %s-pixel %s managed widget', async (width, theme, size) => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(width);
    document.documentElement.dataset.theme = theme;
    const provider = stubChallenge();
    const pending = Promise.withResolvers<Response>();
    const fetch = vi.fn<Fetch>((_, init) =>
      init?.method === 'GET' ? Promise.resolve(Response.json(ADMISSION)) : pending.promise,
    );
    vi.stubGlobal('fetch', fetch);
    const ready = vi.fn();

    render(<Verification onReady={ready} />);
    await vi.waitFor(() => {
      expect(provider.options()).toMatchObject({
        'sitekey': ADMISSION.sitekey,
        'action': 'bench_admit',
        'cData': COMMAND_ID,
        'appearance': 'interaction-only',
        'response-field': false,
        size,
        theme,
      });
    });

    expect(provider.options()).not.toMatchObject({ execution: 'execute' });
    expect(document.querySelector('script[src]')).toBeNull();
    expect(screen.getByRole('status').getAttribute('aria-live')).toBe('polite');
    expect(screen.queryByRole('button')).toBeNull();
    act(() => {
      provider.options()['before-interactive-callback']();
    });
    expect(screen.getByRole('status').textContent).toBe('Verify you are human to continue.');
    act(() => {
      provider.options().callback('private-proof');
    });
    expect(ready).not.toHaveBeenCalled();
    expect(fetch.mock.calls[1]).toEqual([
      '/api/admission',
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command_id: COMMAND_ID, proof: 'private-proof' }),
      },
    ]);

    pending.resolve(Response.json({ status: 'ready' }));
    await vi.waitFor(() => {
      expect(ready).toHaveBeenCalledOnce();
    });
    expect(sessionStorage.getItem('bench-admission-command')).toBeNull();
  });

  test.each(['error', 'missing provider'] as const)(
    'loads a working SDK on retry after %s and admits without navigation',
    async (failure) => {
      vi.stubGlobal('fetch', (_path: string, init?: RequestInit) =>
        Promise.resolve(Response.json(init?.method === 'GET' ? ADMISSION : { status: 'ready' })),
      );
      const ready = vi.fn();
      render(<Verification onReady={ready} />);
      await vi.waitFor(challengeScript);
      const failed = challengeScript();

      fireEvent(failed, new Event(failure === 'error' ? 'error' : 'load'));
      fireEvent.click(await screen.findByRole('button', { name: 'Retry verification' }));
      await vi.waitFor(() => {
        expect(challengeScript()).not.toBe(failed);
      });
      const provider = stubChallenge();
      fireEvent.load(challengeScript());
      await vi.waitFor(() => {
        provider.options();
      });
      act(() => {
        provider.options().callback('new-proof');
      });

      await vi.waitFor(() => {
        expect(ready).toHaveBeenCalledOnce();
      });
    },
  );

  test.each([
    ['error-callback', 'Verification is unavailable. Try again.', true],
    ['expired-callback', 'Verification expired. Try again.', true],
    [
      'unsupported-callback',
      'This browser cannot complete verification. Try another browser.',
      false,
    ],
  ] as const)(
    'retires an unsent widget after %s and ignores its late proof',
    async (callback, message, retry) => {
      const provider = stubChallenge();
      const fetch = vi.fn<Fetch>().mockResolvedValue(Response.json(ADMISSION));
      vi.stubGlobal('fetch', fetch);
      render(<Verification onReady={vi.fn()} />);
      await vi.waitFor(() => {
        provider.options();
      });
      const retired = provider.options();

      act(() => {
        retired[callback]();
      });
      expect(screen.getByRole('status').textContent).toBe(message);
      if (!retry) {
        expect(screen.queryByRole('button')).toBeNull();
        return;
      }
      fireEvent.click(screen.getByRole('button', { name: 'Retry verification' }));
      act(() => {
        retired.callback('stale-proof');
      });

      expect(provider.remove).toHaveBeenCalledWith('widget-1');
      expect(provider.options()).not.toBe(retired);
      expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/admission']);
    },
  );

  test.each([
    ['challenge_rejected', 400],
    ['admission_required', 401],
    ['conflict', 409],
  ] as const)('replaces unusable proof after %s with its valid context', async (error, status) => {
    const provider = stubChallenge();
    const nextCommand = '00000000-0000-4000-8000-000000000002';
    let contexts = 0;
    const fetch = vi.fn<Fetch>((_, init) =>
      Promise.resolve(
        init?.method === 'GET'
          ? Response.json({ ...ADMISSION, command_id: contexts++ === 0 ? COMMAND_ID : nextCommand })
          : admissionRequest.parse(requestBody(init)).proof === 'rejected-proof'
            ? Response.json({ error }, { status })
            : Response.json({ status: 'ready' }),
      ),
    );
    vi.stubGlobal('fetch', fetch);
    const ready = vi.fn();
    render(<Verification onReady={ready} />);
    await vi.waitFor(() => {
      provider.options();
    });

    act(() => {
      provider.options().callback('rejected-proof');
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Retry verification' }));
    await vi.waitFor(() => {
      expect(provider.remove).toHaveBeenCalledWith('widget-1');
    });
    act(() => {
      provider.options().callback('fresh-proof');
    });

    await vi.waitFor(() => {
      expect(ready).toHaveBeenCalledOnce();
    });
    expect(fetch.mock.calls.map(([, init]) => init?.method)).toEqual(
      status === 400 ? ['GET', 'POST', 'POST'] : ['GET', 'POST', 'GET', 'POST'],
    );
    expect(fetch.mock.calls.every(([path]) => path === '/api/admission')).toBe(true);
    expect(requestBody(fetch.mock.calls.at(-1)?.[1])).toEqual({
      command_id: status === 400 ? COMMAND_ID : nextCommand,
      proof: 'fresh-proof',
    });
  });

  test.each(['ready', 'not_found', 'unavailable'] as const)(
    'resolves an uncertain original proof before handling replay %s',
    async (resolution) => {
      const provider = stubChallenge();
      let attempts = 0;
      const fetch = vi.fn<Fetch>((path, init) => {
        if (init?.method === 'GET') {
          return Promise.resolve(Response.json(ADMISSION));
        }
        if (path === '/api/admission') {
          return ++attempts === 1
            ? Promise.reject(new TypeError('Uncertain admission'))
            : Promise.resolve(Response.json({ status: 'ready' }));
        }
        return Promise.resolve(
          resolution === 'ready'
            ? Response.json({ status: 'ready' })
            : Response.json(
                { error: resolution },
                { status: resolution === 'not_found' ? 404 : 503 },
              ),
        );
      });
      vi.stubGlobal('fetch', fetch);
      const ready = vi.fn();
      render(<Verification onReady={ready} />);
      await vi.waitFor(() => {
        provider.options();
      });

      act(() => {
        provider.options().callback('private-original-proof');
      });
      const retry = await screen.findByRole('button', { name: 'Retry verification' });
      act(() => {
        provider.options().callback('replacement-proof');
        provider.options()['expired-callback']();
      });
      fireEvent.click(retry);
      await vi.waitFor(() => {
        if (resolution === 'unavailable') {
          expect(screen.getByRole('button', { name: 'Retry verification' })).toBeDefined();
        } else {
          expect(ready).toHaveBeenCalledOnce();
        }
      });

      const original = { command_id: COMMAND_ID, proof: 'private-original-proof' };
      expect(requestBody(fetch.mock.calls[1][1])).toEqual(original);
      expect(fetch.mock.calls[2][0]).toBe('/api/admission/replay');
      expect(requestBody(fetch.mock.calls[2][1])).toEqual(original);
      expect(provider.render).toHaveBeenCalledOnce();
      if (resolution === 'not_found') {
        expect(fetch.mock.calls[3][0]).toBe('/api/admission');
        expect(requestBody(fetch.mock.calls[3][1])).toEqual(original);
      } else {
        expect(attempts).toBe(1);
      }
      if (resolution === 'unavailable') {
        expect(ready).not.toHaveBeenCalled();
        expect(sessionStorage.getItem('bench-admission-command')).toBe(COMMAND_ID);
      }
      expect(JSON.stringify(sessionStorage)).not.toContain('proof');
    },
  );

  test.each([
    ['initial', 'bench_admit', 'ready', 'widget', true],
    ['renewed', 'bench_renew', 'ready', 'ready', true],
    ['changed renewal context', 'bench_renew', 'ready', 'widget', false],
    ['absent renewal', 'bench_renew', 'not_found', 'widget', true],
    ['uncertain renewal', 'bench_renew', 'unavailable', 'retry', true],
  ] as const)(
    'recovers %s with only its stored locator',
    async (_, action, resolution, expected, matching) => {
      const previous = matching ? COMMAND_ID : '00000000-0000-4000-8000-000000000002';
      sessionStorage.setItem('bench-admission-command', previous);
      const provider = stubChallenge();
      const fetch = vi.fn<Fetch>((path, init) =>
        Promise.resolve(
          init?.method === 'GET'
            ? Response.json({ ...ADMISSION, action })
            : resolution === 'ready' || path === '/api/admission'
              ? Response.json({ status: 'ready' })
              : Response.json(
                  { error: resolution },
                  { status: resolution === 'not_found' ? 404 : 503 },
                ),
        ),
      );
      vi.stubGlobal('fetch', fetch);
      const ready = vi.fn();

      render(<Verification onReady={ready} />);
      await vi.waitFor(() => {
        if (expected === 'ready') {
          expect(ready).toHaveBeenCalledOnce();
        } else if (expected === 'retry') {
          expect(screen.getByRole('button', { name: 'Retry verification' })).toBeDefined();
        } else {
          provider.options();
        }
      });

      if (action === 'bench_renew' && matching) {
        expect(fetch.mock.calls[1][0]).toBe('/api/admission/replay');
        expect(requestBody(fetch.mock.calls[1][1])).toEqual({ command_id: previous });
      } else {
        expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/admission']);
      }
      if (expected === 'widget') {
        act(() => {
          provider.options().callback('fresh-proof');
        });
        await vi.waitFor(() => {
          expect(ready).toHaveBeenCalledOnce();
        });
        expect(requestBody(fetch.mock.calls.at(-1)?.[1])).toEqual({
          command_id: COMMAND_ID,
          proof: 'fresh-proof',
        });
      } else {
        expect(provider.render).not.toHaveBeenCalled();
        expect(document.querySelector('script[src]')).toBeNull();
      }
    },
  );

  test.each(['before-load', 'after-render'] as const)(
    'owns one signed flow and widget under effect replay, then disposes %s',
    async (phase) => {
      const pending = Promise.withResolvers<Response>();
      const fetch = vi.fn<Fetch>(() => pending.promise);
      vi.stubGlobal('fetch', fetch);
      const ready = vi.fn();
      const page = render(
        <StrictMode>
          <Verification onReady={ready} />
        </StrictMode>,
      );
      pending.resolve(Response.json(ADMISSION));
      await vi.waitFor(challengeScript);
      const script = challengeScript();
      const provider = stubChallenge();

      if (phase === 'before-load') {
        page.unmount();
      }
      await act(async () => {
        fireEvent.load(script);
        await pending.promise;
      });
      if (phase === 'after-render') {
        await vi.waitFor(() => {
          provider.options();
        });
        page.unmount();
        act(() => {
          provider.options().callback('late-proof');
          provider.options()['error-callback']();
        });
        expect(provider.remove).toHaveBeenCalledExactlyOnceWith('widget-1');
      }

      expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/admission']);
      expect(provider.render).toHaveBeenCalledTimes(phase === 'before-load' ? 0 : 1);
      expect(ready).not.toHaveBeenCalled();
    },
  );
});
