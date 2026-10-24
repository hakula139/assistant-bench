// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { COMMAND_ID, requestBody } from '@/test/arena';

type Fetch = (path: string, init?: RequestInit) => Promise<Response>;
type ChallengeOptions = Parameters<NonNullable<Window['turnstile']>['render']>[1];

const publicView = {
  command_id: COMMAND_ID,
  action: 'bench_admit',
  sitekey: 'synthetic-site-key',
  family: 'fixture',
};

function mount(view: unknown) {
  document.body.innerHTML = `
    <script id="admission-data" type="application/json"></script>
    <p id="admission-status" role="status"></p>
    <div id="admission-challenge"></div>
    <button id="admission-retry" hidden>Retry admission</button>
    <button id="admission-continue" hidden>Continue</button>
  `;
  const data = document.getElementById('admission-data');
  const status = document.getElementById('admission-status');
  const retry = document.getElementById('admission-retry');
  const proceed = document.getElementById('admission-continue');
  if (
    data === null ||
    status === null ||
    !(retry instanceof HTMLButtonElement) ||
    !(proceed instanceof HTMLButtonElement)
  ) {
    throw new Error('Expected the admission document');
  }
  data.textContent = JSON.stringify(view);
  return { status, retry, proceed };
}

function challengeScript(): HTMLScriptElement {
  const script = document.querySelector('script[src]');
  if (!(script instanceof HTMLScriptElement)) {
    throw new Error('Expected the challenge script');
  }
  return script;
}

describe('admission document', () => {
  beforeEach(() => {
    vi.resetModules();
    sessionStorage.clear();
    localStorage.clear();
    document.head.replaceChildren();
    delete window.turnstile;
  });

  test.each(['bench_admit', 'bench_renew'])(
    'binds %s to its exact command and replays a committed proof after uncertainty',
    async (action) => {
      const page = mount({ ...publicView, action });
      const replace = vi.fn();
      vi.stubGlobal('location', { replace });
      let options: ChallengeOptions | undefined;
      window.turnstile = {
        render: (_container, callbacks) => {
          options = callbacks;
          return 'widget';
        },
      };
      const fetch = vi.fn<Fetch>((path) => {
        if (path === '/api/status') {
          return Promise.resolve(Response.json({ status: 'admission_required' }));
        }
        if (path === '/api/admission') {
          throw new TypeError('Private provider failure');
        }
        return Promise.resolve(Response.json({ status: 'ready' }));
      });
      vi.stubGlobal('fetch', fetch);

      await import('@/admission/client');
      await vi.waitFor(() => {
        expect(challengeScript().src).toBe(
          'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit',
        );
      });
      challengeScript().dispatchEvent(new Event('load'));
      if (options === undefined) {
        throw new Error('Expected challenge callbacks');
      }
      expect(options).toMatchObject({
        action,
        cData: COMMAND_ID,
        sitekey: publicView.sitekey,
      });
      options.callback('private-original-proof');
      await vi.waitFor(() => {
        expect(page.retry.hidden).toBe(false);
      });

      expect(page.status.textContent).not.toContain('Private provider failure');
      expect(sessionStorage.getItem('bench-admission-command')).toBe(COMMAND_ID);
      expect(JSON.stringify(Object.entries(sessionStorage))).not.toContain(
        'private-original-proof',
      );
      expect(localStorage.length).toBe(0);

      options['expired-callback']();
      options['error-callback']();
      page.retry.click();
      await vi.waitFor(() => {
        expect(replace).toHaveBeenCalledWith('/vote/?family=fixture');
      });

      expect(fetch.mock.calls.map(([path]) => path)).toEqual([
        '/api/status',
        '/api/admission',
        '/api/admission/replay',
      ]);
      expect(requestBody(fetch.mock.calls[2][1])).toEqual(requestBody(fetch.mock.calls[1][1]));
      expect(requestBody(fetch.mock.calls[2][1])).toEqual({
        command_id: COMMAND_ID,
        proof: 'private-original-proof',
      });
    },
  );

  test.each(['not_found', 'unavailable'] as const)(
    'retries the original admission only after replay confirms %s',
    async (resolution) => {
      const page = mount(publicView);
      const replace = vi.fn();
      vi.stubGlobal('location', { replace });
      let options: ChallengeOptions | undefined;
      window.turnstile = {
        render: (_container, callbacks) => {
          options = callbacks;
          return 'widget';
        },
      };
      let attempts = 0;
      const fetch = vi.fn<Fetch>((path) => {
        if (path === '/api/status') {
          return Promise.resolve(Response.json({ status: 'admission_required' }));
        }
        if (path === '/api/admission') {
          attempts += 1;
          return attempts === 1
            ? Promise.reject(new TypeError('Uncertain first request'))
            : Promise.resolve(Response.json({ status: 'ready' }));
        }
        return Promise.resolve(
          Response.json({ error: resolution }, { status: resolution === 'not_found' ? 404 : 503 }),
        );
      });
      vi.stubGlobal('fetch', fetch);

      await import('@/admission/client');
      await vi.waitFor(challengeScript);
      challengeScript().dispatchEvent(new Event('load'));
      if (options === undefined) {
        throw new Error('Expected challenge callbacks');
      }
      options.callback('original-proof');
      await vi.waitFor(() => {
        expect(page.retry.hidden).toBe(false);
      });

      page.retry.click();
      await vi.waitFor(() => {
        expect(fetch.mock.calls).toHaveLength(resolution === 'not_found' ? 4 : 3);
      });

      const original = requestBody(fetch.mock.calls[1][1]);
      expect(requestBody(fetch.mock.calls[2][1])).toEqual(original);
      if (resolution === 'not_found') {
        expect(fetch.mock.calls[3][0]).toBe('/api/admission');
        expect(requestBody(fetch.mock.calls[3][1])).toEqual(original);
        await vi.waitFor(() => {
          expect(replace).toHaveBeenCalledWith('/vote/?family=fixture');
        });
      } else {
        expect(attempts).toBe(1);
        expect(replace).not.toHaveBeenCalled();
        expect(sessionStorage.getItem('bench-admission-command')).toBe(COMMAND_ID);
      }
    },
  );

  test('recovers renewal from its old signed context before requesting another challenge', async () => {
    mount({ ...publicView, action: 'bench_renew' });
    sessionStorage.setItem('bench-admission-command', COMMAND_ID);
    const replace = vi.fn();
    vi.stubGlobal('location', { replace });
    const fetch = vi.fn<Fetch>((path) =>
      Promise.resolve(
        Response.json({
          status: path === '/api/status' ? 'renewal_required' : 'ready',
        }),
      ),
    );
    vi.stubGlobal('fetch', fetch);

    await import('@/admission/client');
    await vi.waitFor(() => {
      expect(replace).toHaveBeenCalledWith('/vote/?family=fixture');
    });

    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/status',
      '/api/admission/replay',
    ]);
    expect(requestBody(fetch.mock.calls[1][1])).toEqual({ command_id: COMMAND_ID });
    expect(document.querySelector('script[src]')).toBeNull();
  });

  test('starts a fresh challenge after a definite rejection without replaying the rejected proof', async () => {
    const page = mount(publicView);
    const reload = vi.fn();
    vi.stubGlobal('location', { reload, replace: vi.fn() });
    let options: ChallengeOptions | undefined;
    window.turnstile = {
      render: (_container, callbacks) => {
        options = callbacks;
        return 'widget';
      },
    };
    const fetch = vi.fn<Fetch>((path) =>
      Promise.resolve(
        path === '/api/status'
          ? Response.json({ status: 'admission_required' })
          : Response.json({ error: 'challenge_rejected' }, { status: 400 }),
      ),
    );
    vi.stubGlobal('fetch', fetch);

    await import('@/admission/client');
    await vi.waitFor(challengeScript);
    challengeScript().dispatchEvent(new Event('load'));
    if (options === undefined) {
      throw new Error('Expected challenge callbacks');
    }
    options.callback('rejected-proof');
    await vi.waitFor(() => {
      expect(page.retry.hidden).toBe(false);
    });
    page.retry.click();

    expect(reload).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/status', '/api/admission']);
    expect(page.retry.textContent).toBe('Try challenge again');
  });

  test('rejects unexpected admission data before making requests', async () => {
    const page = mount({ ...publicView, return_url: 'https://other.example/' });
    const fetch = vi.fn<Fetch>();
    vi.stubGlobal('fetch', fetch);

    await import('@/admission/client');
    await vi.waitFor(() => {
      expect(page.status.textContent).toBe('Admission is unavailable. Reload to try again.');
    });

    expect(fetch).not.toHaveBeenCalled();
    expect(document.querySelector('script[src]')).toBeNull();
  });
});
