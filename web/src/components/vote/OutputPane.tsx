import { cn } from 'cn';
import type { ReactNode } from 'react';

import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { Markdown, PlainText } from '@/components/vote/Markdown';
import { band, panel, scroll } from '@/components/vote/panel';
import type { PairView } from '@/lib/arena';

interface OutputPaneProps {
  side: 'A' | 'B';
  render: PairView['render'];
  text: string;
  /** Renders text outputs as Markdown, with raw HTML disabled. */
  asMarkdown: boolean;
  controls: ReactNode;
  /** Receives the pane's scroll container, whose offset a reload restores. */
  scrollRef?: (element: HTMLDivElement | null) => void;
}

type DiffLine = 'file' | 'hunk' | 'added' | 'removed' | 'context';

const diffLineClasses: Record<DiffLine, string> = {
  file: 'bg-muted font-semibold data-start:not-first:mt-4',
  hunk: 'bg-muted text-muted-foreground',
  added: 'bg-emerald-500/15',
  removed: 'bg-red-500/15',
  context: '',
};

/**
 * Classifies each line of a unified diff. A file's header runs from its `diff --git` line to its
 * first hunk, so a removed line that reads `--- ` stays a removal.
 */
function classifyDiff(text: string): { line: string; kind: DiffLine; start: boolean }[] {
  let inHunk = false;

  return text
    .replace(/\n$/, '')
    .split('\n')
    .map((line) => {
      const start = line.startsWith('diff --git ');
      if (start) {
        inHunk = false;
      } else if (line.startsWith('@@')) {
        inHunk = true;
        return { line, kind: 'hunk', start };
      }

      if (!inHunk) {
        return { line, kind: 'file', start };
      }

      const kind = line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : 'context';
      return { line, kind, start };
    });
}

/** Uses `break-all` to avoid early hyphen breaks. */
function Diff({ text }: { text: string }) {
  return (
    <div className="font-mono text-sm leading-relaxed break-all whitespace-pre-wrap">
      {classifyDiff(text).map(({ line, kind, start }, index) => (
        <div
          key={index}
          data-diff={kind}
          data-start={start || undefined}
          className={cn(
            'min-h-lh ps-[calc(var(--card-spacing)+1ch)] pe-(--card-spacing) -indent-[1ch]',
            diffLineClasses[kind],
          )}
        >
          {line}
        </div>
      ))}
    </div>
  );
}

export function OutputPane({
  side,
  render,
  text,
  asMarkdown,
  controls,
  scrollRef,
}: OutputPaneProps) {
  return (
    <Card
      size="sm"
      role="region"
      aria-label={`Output ${side}`}
      className={cn(panel, 'text-base md:min-h-0')}
    >
      <div className={cn(band, 'border-b')}>
        <CardTitle role="heading" aria-level={2}>
          Output {side}
        </CardTitle>
        <div className="ml-auto min-h-7">{controls}</div>
      </div>
      {render === 'diff' ? (
        <CardContent ref={scrollRef} className={cn(scroll, 'px-0')}>
          <Diff text={text} />
        </CardContent>
      ) : asMarkdown ? (
        <CardContent ref={scrollRef} className={scroll}>
          <Markdown text={text} />
        </CardContent>
      ) : (
        <CardContent ref={scrollRef} className={cn(scroll, 'leading-relaxed whitespace-pre-wrap')}>
          <PlainText text={text} />
        </CardContent>
      )}
    </Card>
  );
}
