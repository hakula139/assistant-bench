import { Fragment } from 'react';

import { KeptTail } from '@/components/site/Term';
import { markdown } from '@/lib/markdown';

/** The typography of rendered Markdown, which also styles inline code in the checklist. */
export const prose =
  'prose max-w-none text-foreground dark:prose-invert prose-code:before:content-none prose-code:after:content-none';

/** Case or output text rendered as Markdown, which escapes raw HTML and loads no image. */
export function Markdown({ text }: { text: string }) {
  return <div className={prose} dangerouslySetInnerHTML={{ __html: markdown.render(text) }} />;
}

/** One line of Markdown, such as a checklist item, without the paragraph around it. */
export function InlineMarkdown({ text }: { text: string }) {
  return <span dangerouslySetInnerHTML={{ __html: markdown.renderInline(text) }} />;
}

/** Text as written, inside preformatted white space, with the end of each line kept together. */
export function PlainText({ text }: { text: string }) {
  return text.split('\n').map((line, index) => (
    <Fragment key={index}>
      {index > 0 && '\n'}
      <KeptTail text={line} />
    </Fragment>
  ));
}
