import {
  admissionReplay,
  admissionRequest,
  admissionView,
  ApiError,
  getStatus,
  request,
  statusView,
} from '@/lib/arena';

interface Turnstile {
  render: (
    container: HTMLElement,
    options: {
      'sitekey': string;
      'action': string;
      'cData': string;
      'size': 'compact' | 'flexible';
      'theme': 'light' | 'dark';
      'callback': (proof: string) => void;
      'error-callback': () => void;
      'expired-callback': () => void;
    },
  ) => string;
}

declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

const element = document.getElementById('admission-data');
const status = document.getElementById('admission-status');
const challenge = document.getElementById('admission-challenge');
const retry = document.getElementById('admission-retry');

async function initialize() {
  if (
    element === null ||
    status === null ||
    challenge === null ||
    !(retry instanceof HTMLButtonElement)
  ) {
    return;
  }
  const view = admissionView.parse(JSON.parse(element.textContent));
  let proof: string | null = null;
  let attempted = false;
  let restart = false;
  let busy = false;
  const reloadChallenge = (message: string) => {
    restart = true;
    status.textContent = message;
    challenge.setAttribute('aria-busy', 'false');
    challenge.hidden = true;
    retry.textContent = 'Reload challenge';
    retry.hidden = false;
  };
  const navigate = () => {
    sessionStorage.removeItem('bench-admission-command');
    location.replace(
      `/vote/${view.family === null ? '' : `?${new URLSearchParams({ family: view.family })}`}`,
    );
  };
  const send = async () => {
    if (busy) {
      return;
    }
    if (restart) {
      location.reload();
      return;
    }
    busy = true;
    retry.disabled = true;
    status.textContent = 'Verifying your browser...';
    try {
      const body = admissionRequest.parse({ command_id: view.command_id, proof });
      const replay = attempted;
      attempted = true;
      sessionStorage.setItem('bench-admission-command', view.command_id);
      const admit = () => request(statusView, 'POST', '/admission', body);
      const result = replay
        ? await request(statusView, 'POST', '/admission/replay', admissionReplay.parse(body)).catch(
            (reason: unknown) => {
              if (reason instanceof ApiError && reason.code === 'not_found') {
                return admit();
              }
              throw reason;
            },
          )
        : await admit();
      if (result.status === 'ready') {
        navigate();
      } else {
        status.textContent = 'Verification is required. Retry to continue.';
        retry.textContent = 'Retry verification';
        retry.hidden = false;
      }
    } catch (reason) {
      restart = reason instanceof ApiError && reason.code === 'challenge_rejected';
      status.textContent =
        reason instanceof ApiError
          ? reason.message
          : 'Verification could not be confirmed. Try again.';
      retry.textContent = restart ? 'Try challenge again' : 'Retry verification';
      retry.hidden = false;
    } finally {
      busy = false;
      retry.disabled = false;
    }
  };
  retry.addEventListener('click', () => {
    void send();
  });

  try {
    if ((await getStatus()).status === 'ready') {
      navigate();
      return;
    }
    const previous = sessionStorage.getItem('bench-admission-command');
    if (view.action === 'bench_renew' && previous !== null) {
      const result = await request(
        statusView,
        'POST',
        '/admission/replay',
        admissionReplay.parse({ command_id: previous }),
      );
      if (result.status === 'ready') {
        navigate();
        return;
      }
    }
  } catch {
    status.textContent = 'Complete verification to continue.';
  }

  status.textContent = 'Complete the challenge to continue.';
  const script = document.createElement('script');
  script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  script.async = true;
  script.addEventListener('load', () => {
    if (window.turnstile === undefined) {
      reloadChallenge('The challenge is unavailable. Reload to try again.');
      return;
    }

    window.turnstile.render(challenge, {
      'sitekey': view.sitekey,
      'action': view.action,
      'cData': view.command_id,
      'size': challenge.clientWidth < 300 ? 'compact' : 'flexible',
      'theme': document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
      'callback': (token) => {
        if (!attempted) {
          restart = false;
          retry.hidden = true;
          proof = token;
          void send();
        }
      },
      'error-callback': () => {
        if (!attempted) {
          reloadChallenge('The challenge is unavailable. Reload to try again.');
        }
      },
      'expired-callback': () => {
        if (!attempted) {
          proof = null;
          reloadChallenge('The challenge expired. Reload to try again.');
        }
      },
    });
    challenge.setAttribute('aria-busy', 'false');
  });
  script.addEventListener('error', () => {
    reloadChallenge('The challenge is unavailable. Reload to try again.');
  });
  document.head.append(script);
}

void initialize().catch(() => {
  if (status !== null) {
    status.textContent = 'Verification is unavailable. Reload to try again.';
  }
  challenge?.setAttribute('aria-busy', 'false');
  if (challenge !== null) {
    challenge.hidden = true;
  }

  if (retry instanceof HTMLButtonElement) {
    retry.textContent = 'Reload page';
    retry.hidden = false;
    retry.addEventListener('click', () => {
      location.reload();
    });
  }
});
