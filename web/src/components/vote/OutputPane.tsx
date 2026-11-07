import { cn } from 'cn';
import type { ReactNode } from 'react';

import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { HtmlPreview } from '@/components/vote/HtmlPreview';
import { Markdown, PlainText } from '@/components/vote/Markdown';
import { band, panel, scroll } from '@/components/vote/panel';

interface PaneProps {
  side: 'A' | 'B';
  /** Renders text outputs as Markdown, with raw HTML disabled. */
  asMarkdown: boolean;
  controls: ReactNode;
  /** Receives the pane's scroll container, whose offset a reload restores. */
  scrollRef?: (element: HTMLDivElement | null) => void;
}

type OutputPaneProps = PaneProps &
  (
    | { render: 'html'; previewUrl: string; text?: never }
    | { render: 'text' | 'diff'; text: string; previewUrl?: never }
  );

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
  previewUrl,
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
      {render === 'html' ? (
        <HtmlPreview url={previewUrl} title={`Output ${side} preview`} />
      ) : (
        <CardContent
          ref={scrollRef}
          role="region"
          aria-label={`Output ${side} contents`}
          tabIndex={0}
          className={cn(
            scroll,
            render === 'diff' ? 'px-0' : !asMarkdown && 'leading-relaxed whitespace-pre-wrap',
          )}
        >
          {render === 'diff' ? (
            <Diff text={text} />
          ) : asMarkdown ? (
            <Markdown text={text} />
          ) : (
            <PlainText text={text} />
          )}
        </CardContent>
      )}
    </Card>
  );
}
