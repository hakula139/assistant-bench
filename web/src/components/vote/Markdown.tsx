import { Fragment } from 'react';

import { KeptTail } from '@/components/site/Term';
import { markdown } from '@/lib/markdown';

export const prose =
  'prose max-w-none text-foreground dark:prose-invert prose-code:before:content-none prose-code:after:content-none';

/** Case or output text rendered as Markdown, which escapes raw HTML and loads no image. */
export function Markdown({ text }: { text: string }) {
  return <div className={prose} dangerouslySetInnerHTML={{ __html: markdown.render(text) }} />;
}

export function InlineMarkdown({ text }: { text: string }) {
  return <span dangerouslySetInnerHTML={{ __html: markdown.renderInline(text) }} />;
}

/** Callers must preserve whitespace. */
export function PlainText({ text }: { text: string }) {
  return text.split('\n').map((line, index) => (
    <Fragment key={index}>
      {index > 0 && '\n'}
      <KeptTail text={line} />
    </Fragment>
  ));
}
