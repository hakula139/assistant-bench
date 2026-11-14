# Writing

`writing-zh` and `writing-en` measure how each system writes everyday prose in Chinese and in English when it receives the instruction alone, without a system prompt, from technical documents and messages to blog posts, essays, reviews, and fiction. Both are case families ranked through blind pairwise voting. They share the categories, sourcing, and gates below, differing in language and gate bounds.

## What the Cases Measure

Most instructions supply the material a reply must use and ask for that material alone, so a vote can weigh fidelity, register, and structure on equal content. Many cases make that material hard to use as given: sources that conflict or have gone stale, a question resting on a false premise, a decisive detail buried in a longer context, or author notes that contradict the code they describe. Their checklists name what the reply must reconcile, correct, or flag.

Cases that explain a concept fix the reader and the length, and most leave the content to the model. Fiction, essay, and review instructions specify a point of view or required beats, with length bands where needed. Their checklists follow the brief.

Voters can optionally tag missed requirements, incorrect facts, unclear writing, unsuitable tone, language errors, formulaic prose, and excessive length, in that order. These tags describe the comparison and do not change the vote's weight. Fictional invention is allowed unless it contradicts supplied material or presents invented evidence as fact.

## Case Sourcing

Each family has 59 cases: 42 synthetic cases in its committed `cases.toml` and 17 synthetic `held-out` cases in the private repository, about 30% of the family. Held-out cases keep the leaderboard from being fit to the public set. A `transcript` case drawn from the owner's real tasks also belongs in the private repository, as [Data](../../conventions/data.md#data-classes) describes. A committed case derived from such a task keeps only its scenario type, never its text.

Gemini 4 Argon (`gemini-4-argon-high`) drafts the instructions of the writing cases, committed and held-out, from a brief that gives each case's purpose, traps, and checklist. Another model checks each draft and fixes only factual errors, broken traps, and Markdown problems.

Chinese cases are written in Chinese and keep code, paths, and commands in their original form. Code-comment cases in `writing-zh` ask for Chinese comments.

## Gates

Every collection runs the gates on each output of a trial that finished without an error and records the failed gates in the output's `gate_failures`:

| Gate        | Fails when                                                                         | Setting                          |
| ----------- | ---------------------------------------------------------------------------------- | -------------------------------- |
| `empty`     | The output is blank, in which case only `truncated` is also checked                | Always on                        |
| `truncated` | The endpoint reported a finish reason other than `stop`, or none                   | Always on                        |
| `length`    | The stripped output has fewer than `min_chars` or more than `max_chars` characters | `[gates] min_chars`, `max_chars` |
| `language`  | The output's dominant language differs from the family's `language`                | `language`                       |
| `script`    | The output holds Traditional-only characters that the case instruction lacks       | `[gates] script = "hans"`        |

The minimums sit below the shortest valid answer in each family, a commit message or a short chat reply, so they reject only near-empty output, and `writing-zh`'s is half of `writing-en`'s, since English takes about two to three times as many characters as Chinese for the same content. The maximums sit well above every length a case asks for, at about 3x the longest zh band and 2.4x the longest en band, so they catch only runaway output that stays under the token cap.

The language gate compares Han characters with Latin words in visible prose and code comments. Code, URLs and markup do not determine the prose's language. A tie, including an output with neither, passes.

The script gate works per character, using the CC-CEDICT character sets that zhon ships: a character fails when it is Traditional and never Simplified, unless the instruction also contains it, so an output may quote a Traditional name the case supplies. Because a failure is an automatic loss, the gate never flags a character valid in both scripts. It therefore misses Traditional text written only in such characters, which a phrase-level converter would catch, a gap that narrows with length because longer Traditional text rarely avoids every Traditional-only character.

## Gate Verdicts

The arena reads `gate_failures` from each output in a sealed D1 collection generation, as [Data](../../conventions/data.md#the-data-directory) describes:

| Value      | Meaning                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| `[]`       | The output passed every gate and is eligible for an approved public comparison                                        |
| Gate names | The output failed those gates, which make it an automatic loss in each of its pairs, or both-bad when both sides fail |
| `null`     | No verdict: a case trial errored, a task was ungraded, or the family's scorer has no gates                            |
