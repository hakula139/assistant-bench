import MarkdownIt from 'markdown-it';
import { useEffect, useRef, useState } from 'react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { Pane } from '@/lib/arena';

const markdown = new MarkdownIt({ html: false, linkify: false }).disable('image');

interface OutputPaneProps {
  side: 'A' | 'B';
  pane: Pane;
  /** Renders text outputs as Markdown, with raw HTML disabled. */
  asMarkdown: boolean;
  /** The width an HTML output lays out at, scaled down to fit the pane, in CSS pixels. */
  viewport: number;
}

function FramedOutput({ url, viewport, side }: { url: string; viewport: number; side: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      setScale(Math.min(1, entry.contentRect.width / viewport));
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [viewport]);

  return (
    <div ref={container} className="h-full overflow-hidden">
      <iframe
        src={url}
        title={`Output ${side}`}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="block origin-top-left border-0 bg-white"
        style={{
          width: viewport,
          marginInline: scale === 1 ? 'auto' : 0,
          height: `${100 / scale}%`,
          transform: `scale(${scale})`,
        }}
      />
    </div>
  );
}

/** One side of a pair, shown exactly as written unless the Markdown view is on. */
export function OutputPane({ side, pane, asMarkdown, viewport }: OutputPaneProps) {
  return (
    <Card
      role="region"
      aria-label={`Output ${side}`}
      className="h-[60dvh] gap-0 py-0 md:h-auto md:min-h-0"
    >
      <CardHeader className="border-b py-3">
        <CardTitle>Output {side}</CardTitle>
      </CardHeader>
      {pane.render === 'html' ? (
        <CardContent className="min-h-0 flex-1 p-0">
          <FramedOutput url={pane.url} viewport={viewport} side={side} />
        </CardContent>
      ) : asMarkdown ? (
        <CardContent
          className="prose min-h-0 max-w-none flex-1 overflow-y-auto py-3 text-foreground dark:prose-invert prose-code:before:content-none prose-code:after:content-none"
          // With `html` off markdown-it escapes raw HTML, and with the image rule off it never loads an image.
          dangerouslySetInnerHTML={{ __html: markdown.render(pane.text) }}
        />
      ) : (
        <CardContent className="min-h-0 flex-1 overflow-y-auto py-3 leading-relaxed whitespace-pre-wrap">
          {pane.text}
        </CardContent>
      )}
    </Card>
  );
}
