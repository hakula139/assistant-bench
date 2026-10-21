# Prose

`prose-zh` and `prose-en` measure how each system writes everyday technical prose in Chinese and in English when it receives the instruction alone, without a system prompt. Both are case families ranked by the owner's blind pairwise votes, and they share the categories, sourcing, and gates below, differing only in language and gate bounds.

## Systems and Samples

Each family sends every case to the same five systems at their catalog default effort: `opus-5.5`, `gpt-6.1-sol`, `gemini-4-argon`, `gemini-3.8-flash`, and `deepseek-v4.1-flash`. Each case and system pair runs twice, so every vote can draw one of two samples, and each call may spend up to 16,000 completion tokens, reasoning included.

## Categories

Every case belongs to one category, which the per-category leaderboards split on:

| Category         | The instruction asks for                                                       |
| ---------------- | ------------------------------------------------------------------------------ |
| `reply`          | An assistant's final reply after a task, or an answer to a teammate's question |
| `docs`           | A section or paragraphs of repository or product documentation                 |
| `pr-description` | A pull request description with fixed sections                                 |
| `code-comment`   | The given code returned with comments added only where a maintainer needs them |
| `explanation`    | A concept explained to a stated reader                                         |
| `message`        | A commit message, issue reply, handoff, announcement, or review reply          |

Most instructions supply a list of facts and ask for those facts alone, so a vote can weigh fidelity, register, and structure on equal content. Explanation cases leave the content to the model and fix the reader and the length. A case's `checklist` names what the owner should check beyond general quality, such as reporting what was not verified.

## Case Sourcing

The committed `cases.jsonl` of each family holds 30 synthetic cases, five per category. The data directory's `cases/<family>.jsonl` holds the rest, about 30% of each family: synthetic `held-out` cases that keep the leaderboard from being fit to the public set, and `transcript` cases drawn from the owner's real tasks. Transcript-derived cases pass gitleaks before they enter the file, since every system receives them.

Chinese cases are written in Chinese and keep code, paths, and commands in their original form. Code-comment cases in `prose-zh` ask for Chinese comments.

## Gates

`bench ingest` runs the gates on every output of a trial that finished without an error and records the failed gates in the output's `gate_failures`:

| Gate        | Fails when                                                                                       | Setting                          |
| ----------- | ------------------------------------------------------------------------------------------------ | -------------------------------- |
| `empty`     | The output is blank, in which case only `truncated` is also checked                              | Always on                        |
| `truncated` | The endpoint reported a finish reason other than `stop`, or none, such as a cut at the token cap | Always on                        |
| `length`    | The stripped output has fewer than `min_chars` or more than `max_chars` characters               | `[gates] min_chars`, `max_chars` |
| `language`  | The output's dominant language differs from the family's `language`                              | `language`                       |
| `script`    | The output holds Traditional Chinese characters that the case instruction lacks                  | `[gates] script = "hans"`        |

`prose-zh` bounds outputs to 40 to 6,000 characters and checks the script, and `prose-en` bounds them to 80 to 10,000 characters.

The language gate compares Han characters with Latin words in the output's natural language: the prose outside fenced code blocks, without inline code or URLs, plus the line comments, block comments, and docstrings inside code blocks. Code-comment outputs are therefore judged by the language of their comments, and an output with neither Han characters nor Latin words passes.

The script gate converts the whole output with OpenCC's Traditional-to-Simplified table, whose phrase entries keep characters that are Simplified in context, such as the 乾 of 乾坤. Every Han character the conversion changes counts unless it also appears in the instruction, so an output may quote a Traditional name the case supplies.

## Gate Verdicts

`gate_failures` is the verdict the arena reads from each record of `outputs/<family>.jsonl`:

| Value      | Meaning                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| `[]`       | The output passed every gate and may be shown to the owner                                                            |
| Gate names | The output failed those gates, which make it an automatic loss in each of its pairs, or both-bad when both sides fail |
| `null`     | The trial errored, so it never enters the arena, or the family's scorer has no gates                                  |

The names follow the gate order above. `scoring.gates.failed_gates(settings, text, finish_reason, instruction)` computes the same list for one output, and `bench ingest` prints the count of outputs that failed a gate and the failures by gate.
