import MarkdownIt from 'markdown-it';
import type { StateCore } from 'markdown-it';

/**
 * The shortest last line a block may end on, in words and in characters. A line break may fall
 * between any two Chinese characters, so a word count alone would still strand a short phrase.
 */
const TAIL_WORDS = 2;
const TAIL_CHARS = 6;
/** The longest tail kept on one line, beyond which the tail could overflow a narrow column. */
const TAIL_LIMIT = 24;

const words = new Intl.Segmenter(undefined, { granularity: 'word' });

/**
 * Splits `text` before the words its last line must hold, leaving the tail empty when those words
 * run past `TAIL_LIMIT`.
 */
export function splitTail(text: string): [string, string] {
  const segments = [...words.segment(text)];
  let at = text.length;
  let count = 0;
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    if (count >= TAIL_WORDS && text.length - at >= TAIL_CHARS) {
      break;
    }
    at = segments[i].index;
    count += segments[i].isWordLike ? 1 : 0;
  }
  return text.length - at > TAIL_LIMIT ? [text, ''] : [text.slice(0, at), text.slice(at)];
}

/** Keeps the end of each paragraph, list item, and heading on one line, so no line ends alone. */
function keepTails(state: StateCore) {
  state.tokens.forEach((token, i) => {
    const last = token.children?.at(-1);
    const closes = state.inlineMode || state.tokens[i + 1]?.nesting === -1;
    if (token.type !== 'inline' || !closes || last?.type !== 'text') {
      return;
    }
    const [head, tail] = splitTail(last.content);
    if (tail === '') {
      return;
    }
    last.content = head;
    const text = new state.Token('text', '', 0);
    text.content = tail;
    token.children?.push(
      new state.Token('tail_open', 'span', 1),
      text,
      new state.Token('tail_close', 'span', -1),
    );
  });
}

/**
 * Markdown for case and output text, with raw HTML escaped and the image rule off, so the vote
 * page never loads a remote image.
 */
export const markdown = new MarkdownIt({ html: false, linkify: false }).disable('image');
markdown.core.ruler.push('keep_tails', keepTails);
markdown.renderer.rules.tail_open = () => '<span class="whitespace-nowrap">';
markdown.renderer.rules.tail_close = () => '</span>';
