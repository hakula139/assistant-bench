import { useEffect, useEffectEvent, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { admit, ApiError, getAdmission, replayAdmission, type AdmissionView } from '@/lib/arena';

interface Turnstile {
  render: (
    container: HTMLElement,
    options: {
      'sitekey': string;
      'action': string;
      'cData': string;
      'appearance': 'interaction-only';
      'response-field': false;
      'size': 'compact' | 'flexible';
      'theme': 'light' | 'dark';
      'callback': (proof: string) => void;
      'before-interactive-callback': () => void;
      'error-callback': () => void;
      'expired-callback': () => void;
      'unsupported-callback': () => void;
    },
  ) => string;
  remove: (widget: string) => void;
}

declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

const COMMAND_KEY = 'bench-admission-command';
let loading: Promise<Turnstile> | null = null;

function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile !== undefined) {
    return Promise.resolve(window.turnstile);
  }
  if (loading !== null) {
    return loading;
  }

  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    const detach = () => {
      script.removeEventListener('load', loaded);
      script.removeEventListener('error', failed);
    };
    const failed = () => {
      detach();
      script.remove();
      loading = null;
      reject(new Error('Verification is unavailable. Try again.'));
    };
    const loaded = () => {
      if (window.turnstile === undefined) {
        failed();
        return;
      }

      detach();
      resolve(window.turnstile);
    };

    script.addEventListener('load', loaded);
    script.addEventListener('error', failed);
    document.head.append(script);
  });

  return loading;
}

export function Verification({ onReady }: { onReady: () => void }) {
  const container = useRef<HTMLDivElement>(null);
  const contextRequest = useRef<Promise<AdmissionView> | null>(null);
  const retry = useRef<(() => void) | null>(null);
  const [status, setStatus] = useState('Checking your browser...');
  const [canRetry, setCanRetry] = useState(false);
  const ready = useEffectEvent(onReady);

  useEffect(() => {
    let disposed = false;
    let completed = false;
    let busy = false;
    let generation = 0;
    let sdk: Turnstile | null = null;
    let widget: string | null = null;
    const current = () => !disposed && !completed;

    const fail = (message: string, action: (() => void) | null) => {
      if (!current()) {
        return;
      }

      setStatus(message);
      setCanRetry(action !== null);
      retry.current =
        action === null
          ? null
          : () => {
              if (busy || !current()) {
                return;
              }

              retry.current = null;
              setCanRetry(false);
              action();
            };
    };
    const complete = () => {
      if (current()) {
        completed = true;
        sessionStorage.removeItem(COMMAND_KEY);
        ready();
      }
    };
    const render = (view: AdmissionView) => {
      if (!current() || sdk === null || container.current === null) {
        return;
      }
      if (widget !== null) {
        sdk.remove(widget);
      }

      const instance = ++generation;
      let proof: string | null = null;
      let submitted = false;
      const active = () => current() && instance === generation;
      const fresh = (
        message: string,
        action: (() => void) | null = () => {
          render(view);
        },
      ) => {
        if (active() && !submitted) {
          generation++;
          proof = null;
          fail(message, action);
        }
      };
      const send = async () => {
        if (!active() || busy || proof === null) {
          return;
        }

        busy = true;
        setStatus('Verifying your browser...');
        setCanRetry(false);
        const body = { command_id: view.command_id, proof };
        const replay = submitted;
        submitted = true;
        sessionStorage.setItem(COMMAND_KEY, view.command_id);

        try {
          const result = replay
            ? await replayAdmission(body).catch((reason: unknown) => {
                if (reason instanceof ApiError && reason.code === 'not_found') {
                  return admit(body);
                }
                throw reason;
              })
            : await admit(body);

          if (!active()) {
            return;
          }
          if (result.status === 'ready') {
            complete();
          } else {
            fail('Verification is required. Try again.', () => {
              void send();
            });
          }
        } catch (reason) {
          if (!active()) {
            return;
          }
          if (reason instanceof ApiError && reason.code === 'challenge_rejected') {
            fail(reason.message, () => {
              render(view);
            });
          } else if (
            reason instanceof ApiError &&
            (reason.code === 'admission_required' || reason.code === 'conflict')
          ) {
            fail('Verification expired. Try again.', () => {
              void initialize();
            });
          } else {
            fail(
              reason instanceof ApiError
                ? reason.message
                : 'Verification could not be confirmed. Try again.',
              () => {
                void send();
              },
            );
          }
        } finally {
          busy = false;
        }
      };

      setStatus('Checking your browser...');
      widget = sdk.render(container.current, {
        'sitekey': view.sitekey,
        'action': view.action,
        'cData': view.command_id,
        'appearance': 'interaction-only',
        'response-field': false,
        'size': container.current.clientWidth < 300 ? 'compact' : 'flexible',
        'theme': document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
        'callback': (token) => {
          if (active() && !submitted) {
            proof = token;
            void send();
          }
        },
        'before-interactive-callback': () => {
          if (active() && !submitted) {
            setStatus('Verify you are human to continue.');
          }
        },
        'error-callback': () => {
          fresh('Verification is unavailable. Try again.');
        },
        'expired-callback': () => {
          fresh('Verification expired. Try again.');
        },
        'unsupported-callback': () => {
          fresh('This browser cannot complete verification. Try another browser.', null);
        },
      });
    };
    const initialize = async () => {
      setStatus('Checking your browser...');
      try {
        contextRequest.current ??= getAdmission().finally(() => {
          contextRequest.current = null;
        });
        const view = await contextRequest.current;
        if (!current()) {
          return;
        }

        const previous = sessionStorage.getItem(COMMAND_KEY);
        if (previous !== null && previous !== view.command_id) {
          sessionStorage.removeItem(COMMAND_KEY);
        }
        if (view.action === 'bench_renew' && previous === view.command_id) {
          try {
            if ((await replayAdmission({ command_id: previous })).status === 'ready') {
              complete();
              return;
            }
          } catch (reason) {
            if (!(reason instanceof ApiError && reason.code === 'not_found')) {
              throw reason;
            }
          }
        }

        if (!current()) {
          return;
        }

        sdk = await loadTurnstile();
        render(view);
      } catch (reason) {
        fail(
          reason instanceof ApiError ? reason.message : 'Verification is unavailable. Try again.',
          () => {
            void initialize();
          },
        );
      }
    };

    void initialize();

    return () => {
      disposed = true;
      generation++;
      retry.current = null;
      if (sdk !== null && widget !== null) {
        sdk.remove(widget);
      }
    };
  }, []);

  return (
    <section aria-label="Browser verification" className="flex w-full max-w-sm flex-col gap-3">
      <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {status}
      </p>
      <div ref={container} />
      {canRetry && (
        <Button
          className="self-start"
          onClick={() => {
            retry.current?.();
          }}
        >
          Retry verification
        </Button>
      )}
    </section>
  );
}
