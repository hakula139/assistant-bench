import MarkdownIt from 'markdown-it';
import type { StateCore, Token } from 'markdown-it';

import { Tail, TAIL_CLASS } from '@/lib/tail';

/**
 * Wraps the tail of one inline run in a span. The tail starts only between whole emphasis, links,
 * and code spans, so the span nests, and it stops at a hard break, which already ends the line.
 */
function keepTail(state: StateCore, children: Token[]) {
  const tail = new Tail();
  let depth = 0;
  let start: { index: number; at: number } | undefined;
  for (let i = children.length - 1; i >= 0 && !(depth === 0 && tail.full); i -= 1) {
    const child = children[i];
    if (child.type === 'hardbreak') {
      break;
    }
    depth -= child.nesting;
    if (depth === 0 && child.type === 'text') {
      start = { index: i, at: tail.take(child.content) };
    } else {
      tail.take(child.type === 'softbreak' ? ' ' : child.content, true);
      start = depth === 0 ? { index: i, at: 0 } : start;
    }
  }
  if (start === undefined || !tail.fits) {
    return;
  }
  let index = start.index;
  if (start.at > 0) {
    const text = children[index];
    const head = new state.Token('text', '', 0);
    head.content = text.content.slice(0, start.at);
    text.content = text.content.slice(start.at);
    children.splice(index, 0, head);
    index += 1;
  }
  children.splice(index, 0, new state.Token('tail_open', 'span', 1));
  children.push(new state.Token('tail_close', 'span', -1));
}

/** Keeps the end of every paragraph, list item, heading, and table cell on one line. */
function keepTails(state: StateCore) {
  for (const token of state.tokens) {
    if (token.type === 'inline' && token.children) {
      keepTail(state, token.children);
    }
  }
}

/**
 * Markdown for case and output text, with raw HTML escaped and the image rule off, so the vote
 * page never loads a remote image.
 */
export const markdown = new MarkdownIt({ html: false, linkify: false }).disable('image');
markdown.core.ruler.push('keep_tails', keepTails);
markdown.renderer.rules.tail_open = () => `<span class="${TAIL_CLASS}">`;
markdown.renderer.rules.tail_close = () => '</span>';
