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
const proceed = document.getElementById('admission-continue');

async function initialize() {
  if (
    element === null ||
    status === null ||
    challenge === null ||
    !(retry instanceof HTMLButtonElement) ||
    !(proceed instanceof HTMLButtonElement)
  ) {
    return;
  }
  const view = admissionView.parse(JSON.parse(element.textContent));
  let proof: string | null = null;
  let attempted = false;
  let rejected = false;
  let busy = false;
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
    if (rejected) {
      location.reload();
      return;
    }
    busy = true;
    retry.disabled = true;
    proceed.disabled = true;
    status.textContent = 'Checking admission...';
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
        status.textContent = 'Admission is required. Retry to continue.';
      }
    } catch (reason) {
      rejected = reason instanceof ApiError && reason.code === 'challenge_rejected';
      status.textContent =
        reason instanceof ApiError
          ? reason.message
          : 'Admission could not be confirmed. Retry the same request.';
      retry.textContent = rejected ? 'Try challenge again' : 'Retry admission';
      retry.hidden = false;
    } finally {
      busy = false;
      retry.disabled = false;
      proceed.disabled = false;
    }
  };
  retry.addEventListener('click', () => {
    void send();
  });
  proceed.addEventListener('click', () => {
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
    status.textContent = 'Complete admission to continue.';
  }

  status.textContent = 'Complete the challenge to continue.';
  const script = document.createElement('script');
  script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  script.async = true;
  script.addEventListener('load', () => {
    window.turnstile?.render(challenge, {
      'sitekey': view.sitekey,
      'action': view.action,
      'cData': view.command_id,
      'callback': (token) => {
        if (!attempted) {
          proof = token;
          void send();
        }
      },
      'error-callback': () => {
        if (!attempted) {
          status.textContent = 'The challenge is unavailable. Reload to try again.';
        }
      },
      'expired-callback': () => {
        if (!attempted) {
          proof = null;
          status.textContent = 'The challenge expired. Complete it again.';
        }
      },
    });
  });
  script.addEventListener('error', () => {
    status.textContent = 'The challenge is unavailable. Reload to try again.';
  });
  document.head.append(script);
}

void initialize().catch(() => {
  if (status !== null) {
    status.textContent = 'Admission is unavailable. Reload to try again.';
  }
});
