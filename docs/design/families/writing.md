# Writing

`writing-zh` and `writing-en` measure how each system writes everyday prose in Chinese and in English when it receives the instruction alone, without a system prompt, from technical documents and messages to blog posts, essays, reviews, and fiction. Both are case families ranked through blind pairwise voting. They share the categories, sourcing, and gates below, differing in language and gate bounds.

## Systems and Samples

Both families send every case to the systems each [`family.toml`](../../../families/writing-zh/family.toml) lists, with high effort or its supported equivalent explicitly selected in `systems.toml`. [Systems](../systems.md#resolution) defines their completion and context allowances. Each case and system pair runs twice, so every vote can draw one of two samples. `gemini-3.8-flash-high` is the reference whose score anchors both boards at 1,000 when a round ranks it.

## Categories

Every case belongs to one category, which the per-category leaderboards split on:

| Category  | The instruction asks for                                                                    | Cases per family |
| --------- | ------------------------------------------------------------------------------------------- | ---------------: |
| `reply`   | An assistant's final reply after a task, or an answer to a teammate's question              |                8 |
| `message` | A commit message, PR description, issue or review reply, handoff, or announcement           |               14 |
| `docs`    | Documentation, code comments where a maintainer needs them, or a concept explained          |               21 |
| `fiction` | A scene, dialogue, continuation, or letter                                                  |                5 |
| `essay`   | A personal or critical essay, blog post, project write-up, or tutorial for a general reader |                8 |
| `review`  | A review or close reading of a work from a supplied synopsis                                |                3 |

The counts include the held-out cases, so each family has 59: 42 committed and 17 held-out.

Most instructions supply a list of facts and ask for those facts alone, so a vote can weigh fidelity, register, and structure on equal content.

Cases that explain a concept fix the reader and the length, and most leave the content to the model. Fiction, essay, and review cases state their point of view, required beats, and length band in the instruction, so the checklist can judge the same things the brief asks for.

A review case supplies a synopsis of its invented work, with scene observations for an animation close reading. Personal essays use fictional narrators. One essay in each language asks the writer to engage a well-known concept from a named thinker, and its checklist checks that the concept is used accurately. The thinker essays ban quoting the thinker and inventing quotations.

A case's `checklist` names what voters should check beyond general quality, such as reporting what was not verified.

## Case Sourcing

The committed `cases.toml` of each family holds 42 synthetic cases. The data directory's `cases/<family>.toml` holds 17 more, about 30% of each family, as synthetic `held-out` cases that keep the leaderboard from being fit to the public set. A `transcript` case drawn from the owner's real tasks belongs there too, as [Data](../../conventions/data.md#data-classes) describes. A committed case derived from such a task keeps only its scenario type, never its text.

Chinese cases are written in Chinese and keep code, paths, and commands in their original form. Code-comment cases in `writing-zh` ask for Chinese comments.

## Gates

Every collection runs the gates on each output of a trial that finished without an error and records the failed gates in the output's `gate_failures`:

| Gate        | Fails when                                                                                       | Setting                          |
| ----------- | ------------------------------------------------------------------------------------------------ | -------------------------------- |
| `empty`     | The output is blank, in which case only `truncated` is also checked                              | Always on                        |
| `truncated` | The endpoint reported a finish reason other than `stop`, or none, such as a cut at the token cap | Always on                        |
| `length`    | The stripped output has fewer than `min_chars` or more than `max_chars` characters               | `[gates] min_chars`, `max_chars` |
| `language`  | The output's dominant language differs from the family's `language`                              | `language`                       |
| `script`    | The output holds Traditional-only characters that the case instruction lacks                     | `[gates] script = "hans"`        |

The minimums sit below the shortest valid answer in each family, a commit message or a short chat reply, so they reject only near-empty output, and `writing-zh`'s is half of `writing-en`'s, since English takes about two to three times as many characters as Chinese for the same content. The maximums sit well above every length a case asks for, at about 3x the longest zh band and 2.4x the longest en band, so they catch only runaway output that stays under the token cap.

The language gate parses the output as CommonMark with markdown-it-py and compares Han characters with Latin words in its natural language. It counts inline text and visible HTML prose, excluding inline code and HTML `code`, `pre`, `script`, and `style` content. URLs, attributes, and markup comments do not contribute. A fence tagged `markdown`, `md`, `text`, or `txt`, such as a fenced README section or commit message, is parsed the same way.

Every other fenced or indented code block contributes only its comments: the rest of a line after `#` and whitespace or after `//`, where the marker starts the line or follows whitespace, `/* */` blocks, and triple-quoted strings. Code-comment outputs are therefore judged by the language of their comments, while `#include`, CSS colors, shebangs, and `#[attribute]` lines count as code.

An output with as many Han characters as Latin words, including one with neither, passes.

The script gate works per character, using the CC-CEDICT character sets that zhon ships: a character fails when it is Traditional and never Simplified, unless the instruction also contains it, so an output may quote a Traditional name the case supplies. Because a failure is an automatic loss, the gate never flags a character valid in both scripts, such as 乾, 著, or 於. It therefore misses Traditional text written only in such characters, such as 於是, which a phrase-level converter would catch, a gap that narrows with length because longer Traditional text rarely avoids every Traditional-only character.

## Gate Verdicts

`gate_failures` is the verdict the arena reads from each record of `outputs/<family>.jsonl`:

| Value      | Meaning                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| `[]`       | The output passed every gate and is eligible for an approved public comparison                                        |
| Gate names | The output failed those gates, which make it an automatic loss in each of its pairs, or both-bad when both sides fail |
| `null`     | No verdict: a case trial errored, a task was ungraded, or the family's scorer has no gates                            |

The names are the values of `families.Gate`, which sits beside the gate settings so that readers of outputs can import it without the gate code, and they follow the gate order above. `scoring.gates.failed_gates(settings, text, finish_reason, instruction)` computes the same list for one output, and every collection prints the count of outputs that failed a gate and the failures by gate.
