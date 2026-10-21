# Writing

Conventions for comments, docstrings, documentation, commit and PR text, and strings read by users or models.

## Comments

- Default to no comments, adding one only when a future maintainer needs a non-obvious constraint that the code and names cannot convey.
- Do not restate what the code does, describe its layout or order, narrate the change or refer to the task behind it, or record provenance such as "split out so tests can reach it", because commit messages and PR descriptions preserve that history.
- Explain cryptic flags, literals, and API quirks, and label what a non-obvious regex matches.
- When editing, delete comments that repeat the code and correct any that have become misleading. Before deleting a comment that records a constraint, check whether the constraint still applies.
- Delete commented-out code because version control preserves it.
- Write `#` comments as prose with one line per thought, using additional lines only when the rationale needs them.
- Wrap Python comments and docstrings at 88 columns to match Ruff's `line-length`, and comments in Nix, TOML, YAML, and shell files at 100. Wrap them by hand because no formatter wraps them, and tighten the wording if the final line would contain just one short word.

## Docstrings

- The module docstring of each file under `src/` describes the file's responsibility.
- A docstring states the contract callers need, such as invariants, units, ownership, or errors. Default to one line, using more only when the contract requires it, and list raised exceptions under `Raises:`. Omit docstrings that merely restate the item name or implementation.
- Write docstrings in the third person. A function's docstring says what it does, though a pure function's may name the value it returns, while other docstrings say what the item is. A Typer command's docstring and an option's `help` are `--help` text, so they give a command.

## Prose

These rules apply to comments, docstrings, docs, commit messages, PR and issue text, and strings shown to users or models.

- State conclusions directly and match each claim's strength to its evidence. Describe what you checked, and avoid absolute claims such as "bug-free" or "guaranteed".
- Say each thing once, using the fewest words that preserve its meaning. Give messages of the same kind a consistent shape, such as stating the cause followed by the step that fixes it.
- Connect related thoughts with a period or a linking word such as `since`, `because`, `while`, `so`, or `but`. Use an em-dash only as a paired parenthetical or to separate a list label from its gloss.
- Avoid semicolons in prose, though they are fine in code, in literal syntax, and between list items that contain commas.
- Define things by what they are. Add a contrast with what they are not only when omitting it could lead to a materially wrong reading.
- Avoid lists of three or more similarly structured phrases when they add rhythm without supporting the argument.
- Use American spelling in prose and identifiers, such as `behavior`, `catalog`, and `recognize`.
- In strings shown to users, terminal punctuation separates sentences. One-sentence text, such as a status line, label, or error, takes no final period, and a question keeps its `?`. Text with two or more sentences ends each sentence with terminal punctuation, including the last. Text a model reads, such as case instructions and system prompts, keeps its punctuation.
- Begin standalone text with a capital letter, and begin a fragment that forms part of an error chain with a lowercase letter. Keep identifiers and proper nouns in their original case wherever they appear.
- Place commas and periods outside closing quotation marks.
- Use straight quotes. Arrows and comparison operators (`→`, `≤`, `≠`) are welcome.
- Put spaces around `/` and arrows when they separate words or phrases, as in `Read / Write` and `low → high`. Keep compound terms such as `I/O` tight.

## Markdown

- Put each paragraph on one line without hard wrapping, and separate paragraphs with a blank line.
- Use prose for explanations, bullets for parallel options or requirements, numbered lists for sequences, and tables for comparisons.
- Align trailing comments in directory trees and similar listings in one column, several spaces beyond the longest entry.
- Write headings in Title Case, capitalizing every word except articles, coordinating conjunctions, and prepositions of four letters or fewer, such as `a`, `and`, `for`, `from`, and `with`, unless the word starts or ends the heading. Keep code spans and lowercase names such as assistant-bench as written. PR and issue bodies follow GitHub's sentence case, so the PR template keeps `## Test plan`.
- Write "ID" in prose, and keep lowercase `id` for identifiers and placeholders. Write percentages without a space, as in 80%.

## Documentation

- Keep `README.md` focused on readers evaluating or running the bench: what it measures, how to run it, and where results live.
- Use `docs/` to describe current behavior and its rationale. Track planned work, deferred follow-ups, and open design questions in GitHub issues, and link to the relevant issue from the docs.
- Keep every command shown in the docs minimal. When a command would need extra flags or environment variables, move the setting into the tool's config, rely on its default, or add a project script, then document the short form.
- Organize each page around the questions its readers bring. Keep each paragraph focused on one idea, and verify every claim against the code before citing it.
- Name the endpoint that serves models only as an OpenAI-compatible endpoint configured in `.env`, and refer to models by their public names.
