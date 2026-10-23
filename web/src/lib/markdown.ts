import MarkdownIt from 'markdown-it';
import type { StateCore, Token } from 'markdown-it';

/**
 * The shortest last line a block may end on, in words and in characters. A line break may fall
 * between any two Chinese characters, so a word count alone would still strand a short phrase.
 */
const TAIL_WORDS = 2;
const TAIL_CHARS = 6;
/** The longest tail kept on one line, beyond which the tail could overflow a narrow column. */
const TAIL_LIMIT = 24;

/** Keeps a tail on one line, leaving the spaces of preformatted text as written. */
export const TAIL_CLASS = 'text-nowrap';

const words = new Intl.Segmenter(undefined, { granularity: 'word' });

/** The end of a line, measured backwards until it holds the words a last line must hold. */
class Tail {
  private chars = 0;
  private words = 0;

  get full(): boolean {
    return this.words >= TAIL_WORDS && this.chars >= TAIL_CHARS;
  }

  get fits(): boolean {
    return this.chars <= TAIL_LIMIT;
  }

  /** Takes `text` from its end until the tail is full, and returns where the tail starts in it. */
  take(text: string, whole = false): number {
    const segments = [...words.segment(text)];
    let at = text.length;
    for (let i = segments.length - 1; i >= 0 && (whole || !this.full); i -= 1) {
      at = segments[i].index;
      this.chars += segments[i].segment.length;
      this.words += segments[i].isWordLike ? 1 : 0;
    }
    return at;
  }
}

/**
 * Splits `text` before the words its last line must hold, leaving the tail empty when those words
 * run past `TAIL_LIMIT`.
 */
export function splitTail(text: string): [string, string] {
  const tail = new Tail();
  const at = tail.take(text);
  return tail.fits ? [text.slice(0, at), text.slice(at)] : [text, ''];
}

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
