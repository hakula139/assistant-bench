import { cn } from 'cn';
import { Check, ChevronDown, CircleCheck } from 'lucide-react';
import { useEffect, useEffectEvent, useId, useRef, useState, type ReactNode } from 'react';

import { AspectTitle } from '@/components/site/Method';
import { bleed, pageTitle, sectionTitle, wideContainer } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Kbd } from '@/components/ui/kbd';
import { Textarea } from '@/components/ui/textarea';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { DefectPicker, type DefectKeys } from '@/components/vote/DefectPicker';
import { InlineMarkdown, Markdown, prose } from '@/components/vote/Markdown';
import { OutputPane } from '@/components/vote/OutputPane';
import { band, panel, scroll } from '@/components/vote/panel';
import {
  ApiError,
  castVote,
  listFamilies,
  NetworkError,
  resumeSession,
  startSession,
  undoVote,
  type Choice,
  type FamilyView,
  type PairView,
  type SessionView,
  type TaggedDefects,
} from '@/lib/arena';
import { familyTitle, groupByAspect } from '@/lib/aspects';
import { formatCountOf } from '@/lib/format';
import { defects, type Defect } from '@/lib/report';

const FAMILY_PARAM = 'family';
const SAVED_MS = 1500;
const STALE_PAIR = 'This pair changed in another tab.';
const GONE_FAMILY = (
  <>
    This family has no outputs to vote on now. Restart <code>uv run bench serve</code> after a run.
  </>
);
const VIEWPORTS = [375, 768, 1280] as const;

/** The verdicts from left to right. On phones, two to a row, A and B share the first. */
const CHOICES: { value: Choice; label: string; key: string; className?: string }[] = [
  { value: 'a', label: 'A is better', key: 'a' },
  { value: 'tie', label: 'Tie', key: 't', className: 'max-sm:order-1' },
  { value: 'both_bad', label: 'Both bad', key: 'x', className: 'max-sm:order-1' },
  { value: 'b', label: 'B is better', key: 'b' },
];

/** The two panes, with the tagged state and keys each one reads. */
const SIDES: { side: 'A' | 'B'; key: keyof TaggedDefects }[] = [
  { side: 'A', key: 'a' },
  { side: 'B', key: 'b' },
];

const UNTAGGED: TaggedDefects = { a: [], b: [] };

/** The digits that tag each defect, the left half on output A and the right half on B. */
const DEFECT_KEYS: Record<keyof TaggedDefects, DefectKeys> = {
  a: ['1', '2', '3', '4', '5'],
  b: ['6', '7', '8', '9', '0'],
};

/** The defect each digit toggles, on the side that owns it. */
const KEYED_DEFECTS = new Map(
  SIDES.flatMap(({ key }) =>
    DEFECT_KEYS[key].map((digit, index) => [digit, { key, defect: defects[index] }] as const),
  ),
);

const sessionKey = (family: string) => `bench-session:${family}`;

const notFound = (reason: unknown) => reason instanceof ApiError && reason.status === 404;

/** Resumes the family's session from this browser, or starts one if the arena has no such session. */
async function enterFamily(family: string): Promise<SessionView> {
  const stored = localStorage.getItem(sessionKey(family));
  if (stored !== null) {
    try {
      return await resumeSession(stored);
    } catch (reason) {
      if (!notFound(reason)) {
        throw reason;
      }
    }
  }
  try {
    return await startSession(family);
  } catch (reason) {
    if (notFound(reason)) {
      localStorage.removeItem(sessionKey(family));
    }
    throw reason;
  }
}

/** What went wrong, in words that say what to do when the server is out of reach. */
function message(error: unknown): ReactNode {
  if (error instanceof NetworkError) {
    return (
      <>
        Could not reach the vote server. Check that <code>uv run bench serve</code> is still
        running, then reload.
      </>
    );
  }
  return error instanceof Error ? error.message : String(error);
}

function ErrorAlert({ error }: { error: ReactNode }) {
  return (
    error !== null && (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    )
  );
}

function Notice({ notice }: { notice: string | null }) {
  return (
    notice !== null && (
      <p role="status" className="text-sm text-muted-foreground">
        {notice}
      </p>
    )
  );
}

/** A centered page for the states around a session, with its heading. */
function Screen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4 px-4 py-16">
      <h1 className={pageTitle}>{title}</h1>
      {children}
    </div>
  );
}

interface StartScreenProps {
  busy: boolean;
  error: ReactNode;
  onStart: (family: string) => void;
}

function StartScreen({ busy, error: startError, onStart }: StartScreenProps) {
  const [families, setFamilies] = useState<FamilyView[] | null>(null);
  const [error, setError] = useState<ReactNode>(null);

  useEffect(() => {
    listFamilies().then(setFamilies, (reason: unknown) => {
      setError(message(reason));
    });
  }, []);

  return (
    <Screen title="Start a voting session">
      <ErrorAlert error={error === null ? startError : <>Could not list families: {error}</>} />
      {families?.length === 0 && (
        <p className="text-muted-foreground">
          No family has outputs to vote on. Run <code>uv run bench run &lt;family&gt;</code>, then
          restart <code>uv run bench serve</code>.
        </p>
      )}
      {families &&
        groupByAspect(families).map(({ aspect, members }) => {
          const id = `aspect-${aspect ?? 'unlisted'}`;
          return (
            <section key={id} className="flex flex-col gap-3" aria-labelledby={id}>
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
  // The prompt opens for each pair unless the owner collapsed that pair's own prompt. Keeping this
  // component mounted across pairs is what lets the status band's live region announce a change.
  const [collapsed, setCollapsed] = useState<string | null>(null);
  return (
    <Collapsible
      open={collapsed !== pair.token}
      onOpenChange={(next) => {
        setCollapsed(next ? null : pair.token);
      }}
      asChild
    >
      <Card
        size="sm"
        className={cn(
          panel,
          'data-[state=closed]:self-start md:col-span-2',
          !stacked && 'xl:col-span-1 xl:data-[state=closed]:row-span-1',
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
              'flex flex-col gap-3 text-base',
              stacked ? 'max-h-[25dvh]' : 'max-xl:max-h-[25dvh]',
            )}
          >
            <Markdown text={pair.instruction} />
            {pair.checklist.length > 0 && (
              <section
                aria-labelledby={checklistTitle}
                className="flex flex-col gap-1 rounded-lg bg-muted p-3 ring-1 ring-foreground/10"
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
  const [resumingFamily, setResumingFamily] = useState(() =>
    new URLSearchParams(window.location.search).get(FAMILY_PARAM),
  );
  const [error, setError] = useState<ReactNode>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [tagged, setTagged] = useState<TaggedDefects>(UNTAGGED);
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
    setTagged(UNTAGGED);
    setNote('');
    setError(null);
    setNotice(null);
    setSavedAt(null);
  };

  // Shows the pair the family's session holds now, which another tab may have moved on.
  const follow = async (family: string) => {
    try {
      show(await enterFamily(family));
      setNotice(STALE_PAIR);
    } catch (reason) {
      setError(message(reason));
    }
  };

  // Shows the session that `action` returns, and marks `saved` once a vote is recorded.
  const run = async (action: () => Promise<SessionView>, saved = false) => {
    setBusy(true);
    try {
      show(await action());
      if (saved) {
        setSavedAt(Date.now());
      }
    } catch (reason) {
      if (session !== null && notFound(reason)) {
        await follow(session.family.name);
      } else if (notFound(reason)) {
        setError(GONE_FAMILY);
      } else {
        setError(message(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (resumingFamily === null) {
      return;
    }
    enterFamily(resumingFamily)
      .then(show, (reason: unknown) => {
        if (notFound(reason)) {
          history.replaceState(null, '', window.location.pathname);
          setError(`No family named ${resumingFamily} to vote on.`);
        } else {
          setError(message(reason));
        }
      })
      .finally(() => {
        setResumingFamily(null);
      });
  }, [resumingFamily]);

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
    void run(
      () => castVote(pair.token, { choice, defects: tagged, note: text === '' ? null : text }),
      true,
    );
  };

  const undo = () => {
    if (session === null || session.votes === 0 || busy) {
      return;
    }
    void run(() => undoVote(session.id));
  };

  const toggleDefect = (side: keyof TaggedDefects, defect: Defect) => {
    setTagged((current) => ({
      ...current,
      [side]: defects.filter((item) => (item === defect) !== current[side].includes(item)),
    }));
  };

  // Enter on a focused control outside the pair, such as a header link, keeps its own action. The
  // pair's popovers render in a portal and keep focus through their closing animation, so they
  // count as part of the pair.
  const outside = (target: EventTarget | null) =>
    target instanceof Element &&
    target !== document.body &&
    !pairView.current?.contains(target) &&
    target.closest('[data-slot=popover-content]') === null;

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
    const keyed = KEYED_DEFECTS.get(event.key);
    if (picked) {
      setChoice(picked.value);
    } else if (keyed !== undefined) {
      toggleDefect(keyed.key, keyed.defect);
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

  if (resumingFamily !== null) {
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
        <Notice notice={notice} />
        <ErrorAlert error={error} />
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={busy}
            onClick={() => {
              void run(() => startSession(session.family.name));
            }}
          >
            Start another session
          </Button>
          <Button variant="outline" className="text-foreground" asChild>
            <a href={window.location.pathname}>Choose another family</a>
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
      <span role="status" className="flex items-center gap-1 empty:sr-only">
        {savedAt !== null ? (
          <>
            <Check className="size-4" aria-hidden="true" />
            Saved
          </>
        ) : (
          notice
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
          !hasFrames &&
            'xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)_minmax(0,1fr)] xl:gap-y-0 xl:*:row-span-2 xl:*:grid xl:*:grid-rows-subgrid',
        )}
      >
        <Brief pair={pair} status={status} stacked={hasFrames} />
        {SIDES.map(({ side, key }) => (
          <OutputPane
            key={`${key}-${pair.token}`}
            side={side}
            pane={pair[key]}
            asMarkdown={asMarkdown}
            viewport={viewport}
            controls={
              <DefectPicker
                side={side}
                tagged={tagged[key]}
                keys={DEFECT_KEYS[key]}
                onToggle={(defect) => {
                  toggleDefect(key, defect);
                }}
              />
            }
          />
        ))}
      </div>

      <div className="flex flex-col gap-3">
        {frameFocused && (
          <p role="status" className="text-sm text-muted-foreground">
            Keys go to the output frame while it has focus. Click outside the frame to vote by
            keyboard.
          </p>
        )}
        <ErrorAlert error={error} />

        <div className="flex flex-wrap items-center gap-3">
          {hasText && (
            <Toggle variant="outline" pressed={asMarkdown} onPressedChange={setAsMarkdown}>
              Markdown
            </Toggle>
          )}
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
            className="min-w-72 flex-1"
          />
        </div>
      </div>

      <div
        className={cn(
          bleed,
          'grid gap-2 max-md:sticky max-md:bottom-0 max-md:border-t max-md:bg-background max-md:py-3 sm:grid-cols-[1fr_auto]',
        )}
      >
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
