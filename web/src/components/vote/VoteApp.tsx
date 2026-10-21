import { ChevronDown } from 'lucide-react';
import { useEffect, useEffectEvent, useRef, useState } from 'react';

import { OutputPane } from '@/components/vote/OutputPane';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Kbd } from '@/components/ui/kbd';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  ApiError,
  castVote,
  listFamilies,
  resumeSession,
  startSession,
  undoVote,
  type Choice,
  type PairView,
  type SessionView,
} from '@/lib/arena';
import { flagLabels, flags as flagNames, type Flag } from '@/lib/report';

const SESSION_KEY = 'bench-session';
const VIEWPORTS = [375, 768, 1280] as const;

const CHOICES: { value: Choice; label: string; key: string }[] = [
  { value: 'a', label: 'A is better', key: 'a' },
  { value: 'tie', label: 'Tie', key: 't' },
  { value: 'both_bad', label: 'Both bad', key: 'x' },
  { value: 'b', label: 'B is better', key: 'b' },
];

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function ErrorAlert({ error }: { error: string | null }) {
  return (
    error !== null && (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    )
  );
}

interface StartScreenProps {
  busy: boolean;
  error: string | null;
  onStart: (family: string) => void;
}

function StartScreen({ busy, error: startError, onStart }: StartScreenProps) {
  const [families, setFamilies] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listFamilies().then(setFamilies, (reason: unknown) => {
      setError(message(reason));
    });
  }, []);

  return (
    <div className="mx-auto max-w-xl space-y-4 px-4 py-16">
      <h1 className="text-2xl font-semibold">Start a voting session</h1>
      <ErrorAlert error={error === null ? startError : `Could not list families: ${error}`} />
      {families?.length === 0 && (
        <p className="text-muted-foreground">
          No family has outputs to vote on. Run and ingest a family, then reload this page.
        </p>
      )}
      <ul className="flex flex-wrap gap-2">
        {families?.map((family) => (
          <li key={family}>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                onStart(family);
              }}
            >
              {family}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Brief({ pair }: { pair: PairView }) {
  return (
    <Collapsible defaultOpen className="border-b">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 px-4 py-2 text-left text-sm font-semibold hover:bg-muted">
        <ChevronDown
          className="size-4 transition-transform group-data-[state=closed]:-rotate-90"
          aria-hidden="true"
        />
        Brief
      </CollapsibleTrigger>
      <CollapsibleContent className="max-h-[30dvh] space-y-3 overflow-y-auto px-4 pb-3">
        <p className="leading-relaxed whitespace-pre-wrap">{pair.instruction}</p>
        {pair.checklist.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {pair.checklist.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

/** The local vote page: blinded pairs from the arena API, voted on by keyboard or pointer. */
export function VoteApp() {
  const [session, setSession] = useState<SessionView | null>(null);
  // The page renders only in the browser, so the stored session is readable on the first render.
  const [resuming, setResuming] = useState(() => sessionStorage.getItem(SESSION_KEY));
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [flags, setFlags] = useState<Flag[]>([]);
  const [note, setNote] = useState('');
  const [asMarkdown, setAsMarkdown] = useState(false);
  const [viewport, setViewport] = useState<number>(VIEWPORTS[2]);
  const [frameFocused, setFrameFocused] = useState(false);
  const [busy, setBusy] = useState(false);
  const noteField = useRef<HTMLTextAreaElement>(null);
  const pairView = useRef<HTMLDivElement>(null);

  const pair = session?.pair ?? null;
  const hasFrames = pair !== null && (pair.a.render === 'html' || pair.b.render === 'html');
  const hasText = pair !== null && (pair.a.render === 'text' || pair.b.render === 'text');

  // A new pair takes focus from the control that loaded it, so the next key is a shortcut again.
  useEffect(() => {
    pairView.current?.focus({ preventScroll: true });
  }, [pair]);

  const show = (next: SessionView) => {
    sessionStorage.setItem(SESSION_KEY, next.id);
    setSession(next);
    setChoice(null);
    setFlags([]);
    setNote('');
    setError(null);
  };

  const run = async (action: () => Promise<SessionView>) => {
    setBusy(true);
    try {
      show(await action());
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (resuming === null) {
      return;
    }
    resumeSession(resuming)
      .then(show, (reason: unknown) => {
        if (reason instanceof ApiError && reason.status === 404) {
          sessionStorage.removeItem(SESSION_KEY);
        } else {
          setError(message(reason));
        }
      })
      .finally(() => {
        setResuming(null);
      });
  }, [resuming]);

  useEffect(() => {
    const onBlur = () => {
      setFrameFocused(document.activeElement instanceof HTMLIFrameElement);
    };
    const onFocus = () => {
      setFrameFocused(false);
    };
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  const submit = () => {
    if (pair === null || choice === null || busy) {
      return;
    }
    const text = note.trim();
    void run(() => castVote(pair.token, { choice, flags, note: text === '' ? null : text }));
  };

  const undo = () => {
    if (session === null || session.votes === 0 || busy) {
      return;
    }
    void run(() => undoVote(session.id));
  };

  const toggleFlag = (flag: Flag) => {
    setFlags((current) =>
      current.includes(flag) ? current.filter((item) => item !== flag) : [...current, flag],
    );
  };

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (pair === null || event.isComposing || event.repeat) {
      return;
    }
    const target = event.target;
    // Plain Enter in the note adds a line, since Safari reports the Enter that commits an IME
    // composition as an ordinary keypress.
    if (target instanceof HTMLTextAreaElement) {
      if (event.key === 'Escape') {
        target.blur();
      } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        submit();
      }
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    const picked = CHOICES.find(({ key }) => key === event.key);
    const flag = flagNames[Number(event.key) - 1] as Flag | undefined;
    if (picked) {
      setChoice(picked.value);
    } else if (flag) {
      toggleFlag(flag);
    } else if (event.key === 'n') {
      noteField.current?.focus();
    } else if (event.key === 'u') {
      undo();
    } else if (event.key === 'Enter') {
      submit();
    } else {
      return;
    }
    event.preventDefault();
  });

  useEffect(() => {
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  if (resuming !== null) {
    return null;
  }

  if (session === null) {
    return (
      <StartScreen
        busy={busy}
        error={error}
        onStart={(family) => {
          void run(() => startSession(family));
        }}
      />
    );
  }

  if (pair === null) {
    return (
      <div className="mx-auto max-w-xl space-y-4 px-4 py-16">
        <h1 className="text-2xl font-semibold">Session complete</h1>
        <p className="text-muted-foreground">
          {session.votes} {session.votes === 1 ? 'vote' : 'votes'} recorded on {session.family}.
        </p>
        <ErrorAlert error={error} />
        <div className="flex gap-2">
          <Button
            disabled={busy}
            onClick={() => {
              void run(() => startSession(session.family));
            }}
          >
            Start another session
          </Button>
          <Button variant="outline" onClick={undo} disabled={session.votes === 0 || busy}>
            Undo last vote
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div ref={pairView} tabIndex={-1} className="flex h-dvh flex-col outline-none">
      <Brief key={pair.token} pair={pair} />

      <div className="grid min-h-0 flex-1 grid-rows-2 divide-y md:grid-cols-2 md:grid-rows-1 md:divide-x md:divide-y-0">
        <OutputPane side="A" pane={pair.a} asMarkdown={asMarkdown} viewport={viewport} />
        <OutputPane side="B" pane={pair.b} asMarkdown={asMarkdown} viewport={viewport} />
      </div>

      <div className="space-y-3 border-t bg-muted px-4 py-3">
        {frameFocused && (
          <p role="status" className="text-sm text-muted-foreground">
            Keys go to the output frame while it has focus. Click outside the frame to vote by
            keyboard.
          </p>
        )}
        <ErrorAlert error={error} />

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={1}
            value={choice ?? ''}
            onValueChange={(value) => {
              setChoice(CHOICES.find((item) => item.value === value)?.value ?? null);
            }}
            aria-label="Verdict"
            className="flex-wrap"
          >
            {CHOICES.map(({ value, label, key }) => (
              <ToggleGroupItem
                key={value}
                value={value}
                aria-keyshortcuts={key.toUpperCase()}
                className="gap-2 bg-background px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
              >
                {label}
                <Kbd
                  aria-hidden="true"
                  className="in-data-[state=on]:bg-primary-foreground in-data-[state=on]:text-primary"
                >
                  {key.toUpperCase()}
                </Kbd>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>

          <div className="flex flex-wrap gap-1" role="group" aria-label="Flags">
            {flagNames.map((value, index) => (
              <Toggle
                key={value}
                size="sm"
                variant="outline"
                pressed={flags.includes(value)}
                aria-keyshortcuts={String(index + 1)}
                onPressedChange={() => {
                  toggleFlag(value);
                }}
                className="gap-1.5 bg-background"
              >
                {flagLabels[value]}
                <Kbd aria-hidden="true">{index + 1}</Kbd>
              </Toggle>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">
            <span className="inline-flex items-center gap-2 text-muted-foreground">
              Note <Kbd aria-hidden="true">N</Kbd>
            </span>
            <Textarea
              ref={noteField}
              rows={1}
              aria-keyshortcuts="N"
              placeholder="⌘↵ or Ctrl+↵ submits from here"
              maxLength={500}
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
              }}
              className="min-h-9 bg-background"
            />
          </label>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={undo}
              disabled={session.votes === 0 || busy}
              aria-keyshortcuts="U"
            >
              Undo <Kbd aria-hidden="true">U</Kbd>
            </Button>
            <Button onClick={submit} disabled={choice === null || busy} aria-keyshortcuts="Enter">
              Submit vote{' '}
              <Kbd aria-hidden="true" className="bg-primary-foreground text-primary">
                ↵
              </Kbd>
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-muted-foreground">
          <div className="flex min-w-48 flex-1 items-center gap-3">
            <span className="whitespace-nowrap tabular-nums">
              Session {session.votes} / {session.size}
            </span>
            <Progress
              value={session.size === 0 ? 0 : (100 * session.votes) / session.size}
              aria-label="Session progress"
              className="max-w-64"
            />
          </div>

          {hasText && (
            <Toggle
              size="sm"
              variant="outline"
              pressed={asMarkdown}
              onPressedChange={setAsMarkdown}
              className="bg-background"
            >
              Markdown
            </Toggle>
          )}

          {hasFrames && (
            <ToggleGroup
              type="single"
              size="sm"
              variant="outline"
              spacing={0}
              value={String(viewport)}
              onValueChange={(value) => {
                if (value !== '') {
                  setViewport(Number(value));
                }
              }}
              aria-label="Viewport width"
            >
              {VIEWPORTS.map((width) => (
                <ToggleGroupItem key={width} value={String(width)} className="bg-background px-2.5">
                  {width} px
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
        </div>
      </div>
    </div>
  );
}
