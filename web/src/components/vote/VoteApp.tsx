import { cn } from 'cn';
import { Check, ChevronDown, CircleCheck } from 'lucide-react';
import { useEffect, useEffectEvent, useId, useRef, useState, type ReactNode } from 'react';

import { AspectTitle } from '@/components/site/Method';
import { pageTitle, sectionTitle, wideContainer } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
import { InlineMarkdown, Markdown, prose } from '@/components/vote/Markdown';
import { OutputPane } from '@/components/vote/OutputPane';
import { band, panel, scroll } from '@/components/vote/panel';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Kbd } from '@/components/ui/kbd';
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
  type FamilyView,
  type PairView,
  type SessionView,
} from '@/lib/arena';
import { familyTitle, groupByAspect } from '@/lib/aspects';
import { formatCountOf } from '@/lib/format';
import { flagLabels, flags as flagNames, type Flag } from '@/lib/report';

const FAMILY_PARAM = 'family';
const SAVED_MS = 1500;
const VIEWPORTS = [375, 768, 1280] as const;

/** The verdicts from left to right. On phones, two to a row, A and B share the first. */
const CHOICES: { value: Choice; label: string; key: string; className?: string }[] = [
  { value: 'a', label: 'A is better', key: 'a' },
  { value: 'tie', label: 'Tie', key: 't', className: 'max-sm:order-1' },
  { value: 'both_bad', label: 'Both bad', key: 'x', className: 'max-sm:order-1' },
  { value: 'b', label: 'B is better', key: 'b' },
];

const sessionKey = (family: string) => `bench-session:${family}`;

/** Resumes the family's session from this browser, or starts one if the arena has no such session. */
async function enterFamily(family: string): Promise<SessionView> {
  const stored = localStorage.getItem(sessionKey(family));
  if (stored !== null) {
    try {
      return await resumeSession(stored);
    } catch (reason) {
      if (!(reason instanceof ApiError && reason.status === 404)) {
        throw reason;
      }
    }
  }
  return startSession(family);
}

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
      <h1 className={pageTitle}>{title}</h1>
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
  const [families, setFamilies] = useState<FamilyView[] | null>(null);
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
      {families &&
        groupByAspect(families).map(({ aspect, members }) => {
          const id = `aspect-${aspect ?? 'unlisted'}`;
          return (
            <section key={id} className="space-y-3" aria-labelledby={id}>
              <h2 id={id} className={cn(sectionTitle, 'flex items-center gap-2')}>
                {aspect ? <AspectTitle aspect={aspect} /> : 'Unlisted'}
              </h2>
              <ul className="flex flex-wrap gap-2">
                {members.map((family) => (
                  <li key={family.name}>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        onStart(family.name);
                      }}
                    >
                      {family.display_name}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
    </Screen>
  );
}

const checklistHint = {
  term: 'What to check',
  definition: 'Points for you to judge by. The model never saw them.',
};

/**
 * The case's prompt and checklist, with the session's status in its band. It spans the top of the
 * pair while `stacked`, and otherwise takes a column beside the outputs from `xl` up.
 */
function Brief({ pair, status, stacked }: { pair: PairView; status: ReactNode; stacked: boolean }) {
  const checklistTitle = useId();
  return (
    <Collapsible defaultOpen asChild>
      <Card
        size="sm"
        className={cn(
          panel,
          'data-[state=closed]:self-start md:col-span-2',
          !stacked && 'xl:col-span-1',
        )}
      >
        <div className={cn(band, 'group-data-[state=open]/card:border-b')}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ml-2">
              <ChevronDown
                className="transition-transform in-data-[state=closed]:-rotate-90"
                aria-hidden="true"
              />
              Prompt
            </Button>
          </CollapsibleTrigger>
          {status}
        </div>
        <CollapsibleContent asChild>
          <CardContent
            className={cn(
              scroll,
              'space-y-3 text-base',
              stacked ? 'max-h-[25dvh]' : 'max-xl:max-h-[25dvh]',
            )}
          >
            <Markdown text={pair.instruction} />
            {pair.checklist.length > 0 && (
              <section
                aria-labelledby={checklistTitle}
                className="space-y-1 rounded-lg bg-muted p-3 ring-1 ring-foreground/10"
              >
                <h2 id={checklistTitle} className="text-sm font-medium text-muted-foreground">
                  <Hint term={checklistHint} />
                </h2>
                <ul className={cn(prose, 'text-muted-foreground')}>
                  {pair.checklist.map((item) => (
                    <li key={item} className="flex gap-2 ps-0">
                      <CircleCheck className="h-lh w-4 shrink-0" aria-hidden="true" />
                      <InlineMarkdown text={item} />
                    </li>
                  ))}
                </ul>
              </section>
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
  // The page renders only in the browser, so its URL is readable on the first render.
  const [resuming, setResuming] = useState(() =>
    new URLSearchParams(window.location.search).get(FAMILY_PARAM),
  );
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
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

  // A new pair takes focus from the control that loaded it, so the next key is a shortcut again,
  // and opens at the top of the page on phones, where the page scrolls.
  useEffect(() => {
    pairView.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [pair]);

  const show = (next: SessionView) => {
    localStorage.setItem(sessionKey(next.family.name), next.id);
    history.replaceState(null, '', `?${new URLSearchParams({ [FAMILY_PARAM]: next.family.name })}`);
    setSession(next);
    setChoice(null);
    setFlags([]);
    setNote('');
    setError(null);
  };

  /** Shows the session that `action` returns, and returns whether it succeeded. */
  const run = async (action: () => Promise<SessionView>) => {
    setBusy(true);
    try {
      show(await action());
      return true;
    } catch (reason) {
      setError(message(reason));
      return false;
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (resuming === null) {
      return;
    }
    enterFamily(resuming)
      .then(show, (reason: unknown) => {
        setError(message(reason));
      })
      .finally(() => {
        setResuming(null);
      });
  }, [resuming]);

  useEffect(() => {
    if (savedAt === null) {
      return;
    }
    const timer = setTimeout(() => {
      setSavedAt(null);
    }, SAVED_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [savedAt]);

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
    void run(() => castVote(pair.token, { choice, flags, note: text === '' ? null : text })).then(
      (saved) => {
        if (saved) {
          setSavedAt(Date.now());
        }
      },
    );
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
          void run(() => enterFamily(family));
        }}
      />
    );
  }

  if (pair === null) {
    return (
      <Screen title="Session complete">
        <p className="text-muted-foreground">
          {formatCountOf(session.votes, 'vote')} recorded on {familyTitle(session.family)}.
        </p>
        <ErrorAlert error={error} />
        <div className="flex gap-2">
          <Button
            disabled={busy}
            onClick={() => {
              void run(() => startSession(session.family.name));
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
      <span role="status" className="flex items-center gap-1">
        {savedAt !== null && (
          <>
            <Check className="size-4" aria-hidden="true" />
            Saved
          </>
        )}
      </span>
      <span className="whitespace-nowrap tabular-nums">
        {familyTitle(session.family)},{' '}
        <Hint
          term={{
            term: 'This session',
            definition: `${formatCountOf(session.remaining, 'pair')} left to vote on.`,
          }}
        >
          {formatCountOf(session.votes, 'vote')}
        </Hint>
      </span>

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
      aria-label={`Pair ${session.votes + 1}`}
      className={cn(wideContainer, 'flex flex-col gap-3 py-3 outline-none md:min-h-0 md:flex-1')}
    >
      <div
        className={cn(
          'grid gap-3 md:min-h-0 md:flex-1 md:grid-cols-2 md:grid-rows-[auto_minmax(0,1fr)]',
          !hasFrames && 'xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)_minmax(0,1fr)] xl:grid-rows-1',
        )}
      >
        <Brief key={pair.token} pair={pair} status={status} stacked={hasFrames} />
        <OutputPane
          key={`a-${pair.token}`}
          side="A"
          pane={pair.a}
          asMarkdown={asMarkdown}
          viewport={viewport}
        />
        <OutputPane
          key={`b-${pair.token}`}
          side="B"
          pane={pair.b}
          asMarkdown={asMarkdown}
          viewport={viewport}
        />
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
            size="line"
            rows={1}
            aria-label="Note"
            aria-keyshortcuts="N"
            placeholder="Note (N), ⌘↵ or Ctrl+↵ submits"
            maxLength={500}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            className="min-w-64 flex-1"
          />
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

        <div className="flex gap-2">
          <Button
            variant="outline"
            size="lg"
            onClick={undo}
            disabled={session.votes === 0 || busy}
            aria-keyshortcuts="U"
          >
            Undo
            <Kbd aria-hidden="true">U</Kbd>
          </Button>
          <Button
            size="lg"
            onClick={submit}
            disabled={choice === null || busy}
            aria-keyshortcuts="Enter"
            className="max-sm:flex-1"
          >
            Submit vote
            <Kbd aria-hidden="true" className="bg-primary-foreground text-primary">
              ↵
            </Kbd>
          </Button>
        </div>
      </div>
    </div>
  );
}
