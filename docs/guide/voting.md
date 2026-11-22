# Voting

Open the [vote page](https://bench.hakula.xyz/vote/) and choose a family. Allow cookies so the site can remember your browser. On your first visit, complete the verification if prompted.

For private cases, sign in to the private vote page with an allowed identity.

Each pair compares two systems' different, passing outputs on one case, and you vote at most once on each comparison. A family ends with No pairs left once you have voted on every comparison it offers. Tap the vote count in the Prompt bar to see how many pairs remain.

## Casting a Vote

Judge outputs A and B against the prompt and What to check, whose criteria the model never saw. The page hides system identities. Choose a verdict, optionally tag issues on each output, then submit.

| Key        | Action                                                                               |
| ---------- | ------------------------------------------------------------------------------------ |
| `a` / `b`  | A or B is better                                                                     |
| `t`        | Tie                                                                                  |
| `x`        | Both bad                                                                             |
| `1` to `5` | Toggle the first five issues on output A, in the order the Issues popover lists them |
| `6` to `0` | Toggle the first five issues on output B                                             |
| `Enter`    | Submit from the pair background, or activate the focused control                     |
| `s`        | Skip to another case for the same two systems                                        |
| `u`        | Undo the last vote and show its pair again                                           |

Letter and digit shortcuts work while the voting panel has focus. Each new pair focuses the panel. Header controls and open popovers keep their own keyboard behavior.

Each family's Issues menu defines the tags. An issue counts against the output you tag, whatever your verdict.

Outputs appear exactly as written. The Render Markdown toggle renders both text panes as Markdown, with raw HTML shown as text and images left unloaded.

## Changing or Recovering a Vote

Skip moves to another case for the same two systems when one is available. Undo withdraws your latest eligible vote and reopens its pair. You can step back through votes cast in the last 10 minutes.

Your tabs share a current pair. Reloading restores the tab's draft without submitting it. If another tab has moved on, the page opens the current pair and tells you when it cleared an unsent choice.

After a connection failure, **Retry vote** resends your choice and issue tags. **Retry next pair** means your vote was saved and the next pair still needs to load.
