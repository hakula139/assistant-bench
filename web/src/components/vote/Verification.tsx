import { Turnstile, type TurnstileInstance } from '@marsidev/react-turnstile';
import { useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { admit, ApiError, NetworkError } from '@/lib/vote-flow';

const UNAVAILABLE = 'Verification is unavailable. Try again.';

/** Admits this browser through Turnstile, which shows a challenge only when it needs one. */
export function Verification({ sitekey, onReady }: { sitekey: string; onReady: () => void }) {
  const widget = useRef<TurnstileInstance>(null);
  const [status, setStatus] = useState('Checking your browser...');
  const [retry, setRetry] = useState(false);

  const fail = (message: string, retryable = true) => {
    setStatus(message);
    setRetry(retryable);
  };

  return (
    <section aria-label="Browser verification" className="flex w-full max-w-sm flex-col gap-3">
      <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {status}
      </p>
      <Turnstile
        ref={widget}
        siteKey={sitekey}
        options={{
          appearance: 'interaction-only',
          responseField: false,
          size: 'flexible',
          theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
        }}
        scriptOptions={{
          onError: () => {
            fail(UNAVAILABLE);
          },
        }}
        onBeforeInteractive={() => {
          setStatus('Verify you are human to continue.');
        }}
        onSuccess={(token) => {
          setStatus('Verifying your browser...');
          admit(token).then(onReady, (reason: unknown) => {
            fail(
              reason instanceof ApiError || reason instanceof NetworkError
                ? reason.message
                : UNAVAILABLE,
            );
          });
        }}
        onError={() => {
          fail(UNAVAILABLE);
        }}
        onExpire={() => {
          fail('Verification expired. Try again.');
        }}
        onUnsupported={() => {
          fail('This browser cannot complete verification. Try another browser.', false);
        }}
      />
      {retry && (
        <Button
          className="self-start"
          onClick={() => {
            setRetry(false);
            setStatus('Checking your browser...');
            widget.current?.reset();
          }}
        >
          Retry verification
        </Button>
      )}
    </section>
  );
}
