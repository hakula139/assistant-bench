# Voting

Learn how to vote on pairs by keyboard, read outputs as Markdown, and keep HTML outputs contained, once [Running](running.md#voting) has started the arena.

## Opening the Vote Page

`bench serve` builds the site. Open `http://127.0.0.1:8790/vote/`, or follow the Vote link in the header of the local site, and pick a family, listed under its aspect, to start a session. The session runs until every case has your vote, and the browser keeps one per family, so picking the family again or reloading the page resumes where you left off. Each vote saves as you submit it. Tap the vote count in the Prompt bar to see how many pairs remain.

## Casting a Vote

Each pair shows the case's prompt above two outputs, or, for text outputs, beside them on a wide screen, with A on the left and B on the right, and never names their systems. Under the prompt, What to check lists the points to judge by, which the model never saw. Nothing is selected until you choose:

| Key        | Action                                                                                                                               |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `a` / `b`  | A or B is better                                                                                                                     |
| `t`        | Tie                                                                                                                                  |
| `x`        | Both bad                                                                                                                             |
| `1` to `5` | Toggle an issue on output A, in the order the family's Issues popover lists them                                                     |
| `6` to `0` | Toggle the same issues on output B                                                                                                   |
| `n`        | Write a note, where `Enter` starts a new line, `⌘↵` or `Ctrl+↵` submits, and `Escape` leaves the field                               |
| `Enter`    | Submit the vote and load the next pair. `Enter` submits even while a control in the pair has focus, and `Space` presses that control |
| `u`        | Undo the last vote and show its pair again                                                                                           |

An issue counts against the output you tag, whatever your verdict. Each family lists its own issues, such as wrong facts or a wrong tone on writing and off-scope changes or weak tests on code, and each output's Issues button shows what each issue means and toggles the same issues, which is how a phone tags them. A family that lists none shows no Issues button. The note stays in the vote store, where no report reads it. Outputs appear exactly as written, and the Markdown toggle beside the note renders both panes as Markdown, with raw HTML shown as text and images left unloaded.

## Diffs

A task family scored by votes shows each solution as a diff of what it changed in the task's repository, with added and removed lines tinted and each file under its own header, and both solutions passed the task's hidden tests. Judge them as you would review a pull request against the prompt. The diff leaves out the repository's history and the caches that Python's tools write, so neither a commit message nor a trace of the agent's test runs shows.

## HTML Outputs

A family that renders HTML loads each output in a sandboxed frame from a second origin, under a policy that blocks network requests. The viewport presets of 375, 768, and 1280 px lay out both frames at that width, scaled down to fit the pane.

Keys pressed inside a frame stay in the frame, so the page shows a hint while one has focus. Click outside the frames before voting by keyboard.

The frame's policy cannot block WebRTC, so vote on HTML outputs of private cases in a browser profile without network access.
