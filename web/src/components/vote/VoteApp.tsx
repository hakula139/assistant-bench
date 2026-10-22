import { cn } from 'cn';
import { ChevronDown } from 'lucide-react';
import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from 'react';

import { wideContainer } from '@/components/site/PageParts';
import { OutputPane } from '@/components/vote/OutputPane';
import { band, panel, scroll } from '@/components/vote/panel';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
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
import { formatCountOf } from '@/lib/format';
import { flagLabels, flags as flagNames, type Flag } from '@/lib/report';

const SESSION_KEY = 'bench-session';
const VIEWPORTS = [375, 768, 1280] as const;

/** The verdicts in key order. On phones, two to a row, A and B share the first. */
const CHOICES: { value: Choice; label: string; key: string; className?: string }[] = [
  { value: 'a', label: 'A is better', key: 'a' },
  { value: 'tie', label: 'Tie', key: 't', className: 'max-sm:order-1' },
  { value: 'both_bad', label: 'Both bad', key: 'x', className: 'max-sm:order-1' },
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

/** A centered page for the states around a session, with its heading. */
function Screen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-xl space-y-4 px-4 py-16">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children}
    </div>
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
    <Screen title="Start a voting session">
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
    </Screen>
  );
}

/** The case's brief and checklist, with the session's status in its band. */
function Brief({ pair, status }: { pair: PairView; status: ReactNode }) {
  return (
    <Collapsible defaultOpen asChild>
      <Card
        size="sm"
        className={cn(panel, 'data-[state=closed]:self-start md:col-span-2 xl:col-span-1')}
      >
        <div className={cn(band, 'group-data-[state=open]/card:border-b')}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ml-2">
              <ChevronDown
                className="transition-transform in-data-[state=closed]:-rotate-90"
                aria-hidden="true"
              />
              Brief
            </Button>
          </CollapsibleTrigger>
          {status}
        </div>
        <CollapsibleContent asChild>
          <CardContent className={cn(scroll, 'space-y-3 text-base max-xl:max-h-[30dvh]')}>
            <p className="leading-relaxed whitespace-pre-wrap">{pair.instruction}</p>
            {pair.checklist.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                {pair.checklist.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </CollapsibleContent>
      </Card>
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

  // Enter on a focused control outside the pair, such as a header link, keeps its own action.
  const outside = (target: EventTarget | null) =>
    target instanceof Node && target !== document.body && !pairView.current?.contains(target);

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.isComposing || event.repeat) {
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
    const key = event.key.toLowerCase();
    if (key === 'u') {
      event.preventDefault();
      undo();
      return;
    }
    if (pair === null) {
      return;
    }
    const picked = CHOICES.find((item) => item.key === key);
    const flag = flagNames[Number(event.key) - 1] as Flag | undefined;
    if (picked) {
      setChoice(picked.value);
    } else if (flag) {
      toggleFlag(flag);
    } else if (key === 'n') {
      noteField.current?.focus();
    } else if (event.key === 'Enter' && !outside(target)) {
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
      <Screen title="Session complete">
        <p className="text-muted-foreground">
          {formatCountOf(session.votes, 'vote')} recorded on {session.family}.
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
      </Screen>
    );
  }

  const status = (
    <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
      <span className="whitespace-nowrap tabular-nums">
        {session.family}, pair {session.votes + 1} of {session.size}
      </span>
      <Progress
        value={session.size === 0 ? 0 : (100 * session.votes) / session.size}
        aria-label="Session progress"
        className="w-32 max-sm:hidden xl:hidden"
      />

      {hasText && (
        <Toggle size="sm" variant="outline" pressed={asMarkdown} onPressedChange={setAsMarkdown}>
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
            <ToggleGroupItem key={width} value={String(width)}>
              {width} px
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
    </div>
  );

  return (
    <div
      ref={pairView}
      tabIndex={-1}
      role="group"
      aria-label={`Pair ${session.votes + 1} of ${session.size}`}
      className={cn(wideContainer, 'flex flex-col gap-3 py-4 outline-none md:min-h-0 md:flex-1')}
    >
      <div className="grid gap-4 md:min-h-0 md:flex-1 md:grid-cols-2 md:grid-rows-[auto_minmax(0,1fr)] xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)_minmax(0,1fr)] xl:grid-rows-1">
        <Brief key={pair.token} pair={pair} status={status} />
        <OutputPane side="A" pane={pair.a} asMarkdown={asMarkdown} viewport={viewport} />
        <OutputPane side="B" pane={pair.b} asMarkdown={asMarkdown} viewport={viewport} />
      </div>

      <div className="space-y-3">
        {frameFocused && (
          <p role="status" className="text-sm text-muted-foreground">
            Keys go to the output frame while it has focus. Click outside the frame to vote by
            keyboard.
          </p>
        )}
        <ErrorAlert error={error} />

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
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
              >
                {flagLabels[value]}
                <Kbd aria-hidden="true">{index + 1}</Kbd>
              </Toggle>
            ))}
          </div>

          <Textarea
            ref={noteField}
            rows={1}
            aria-label="Note"
            aria-keyshortcuts="N"
            placeholder="Note (N), where ⌘↵ or Ctrl+↵ submits"
            maxLength={500}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            className="min-h-9 min-w-64 flex-1"
          />

          <Button
            variant="outline"
            onClick={undo}
            disabled={session.votes === 0 || busy}
            aria-keyshortcuts="U"
          >
            Undo <Kbd aria-hidden="true">U</Kbd>
          </Button>
        </div>
      </div>

      <div className="grid gap-2 max-md:sticky max-md:bottom-0 max-md:-mx-4 max-md:border-t max-md:bg-background max-md:px-4 max-md:py-3 sm:grid-cols-[1fr_auto] sm:max-md:-mx-6 sm:max-md:px-6">
        <ToggleGroup
          type="single"
          variant="choice"
          size="lg"
          spacing={2}
          value={choice ?? ''}
          onValueChange={(value) => {
            setChoice(CHOICES.find((item) => item.value === value)?.value ?? null);
          }}
          aria-label="Verdict"
          className="grid w-full grid-cols-2 sm:grid-cols-4"
        >
          {CHOICES.map(({ value, label, key, className }) => (
            <ToggleGroupItem
              key={value}
              value={value}
              aria-keyshortcuts={key.toUpperCase()}
              className={className}
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

        <Button
          size="lg"
          onClick={submit}
          disabled={choice === null || busy}
          aria-keyshortcuts="Enter"
        >
          Submit vote{' '}
          <Kbd aria-hidden="true" className="bg-primary-foreground text-primary">
            ↵
          </Kbd>
        </Button>
      </div>
    </div>
  );
}
