// Lints each case instruction as Markdown with the repository's markdownlint config, since
// systems receive instructions as Markdown. Pass case files to lint, such as a data directory's
// `cases/<family>.toml`, or none to lint every committed `families/*/cases.toml`.

import { glob, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { main } from 'markdownlint-cli2';
import { parse } from 'smol-toml';

const root = fileURLToPath(new URL('..', import.meta.url));
const paths =
  process.argv.length > 2
    ? process.argv.slice(2).map((path) => resolve(path))
    : await Array.fromAsync(glob('families/*/cases.toml', { cwd: root }), (path) =>
        resolve(root, path),
      );

const instructions = {};
for (const path of paths) {
  const { cases } = parse(await readFile(path, 'utf8'));
  for (const { id, instruction } of cases) {
    instructions[`${path}#${id}`] = instruction;
  }
}

process.exitCode = await main({
  directory: root,
  argv: [],
  nonFileContents: instructions,
  logMessage: console.log,
  logError: console.error,
});
