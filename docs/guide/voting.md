# Voting

Vote on blind pairs at [bench.hakula.xyz/vote/](https://bench.hakula.xyz/vote/), using the same service on a Mac, phone, or another device.

## Opening the Vote Page

Follow the site's Vote link and pick a family under its aspect. Admission opens a separate challenge page, then returns to voting. All real votes go to the production D1 database. The staging deployment has its own database for validation.

A session records one active vote per eligible case and ends when none remain. Cases need passing, different outputs from two systems to be eligible. The browser keeps a session locator per family, so picking the family again or reloading resumes it while the admission cookie remains valid. Each accepted vote is recorded before the next pair is requested. Tap the vote count in the Prompt bar to see how many pairs remain.

If a connection fails, retry checks the same command. The tab retains the choice and issue tags needed to resolve an uncertain submission after a reload or admission renewal. Resolve it before moving to another pair or family. If the pending pair changed, review the new outputs and choose again. Undo withdraws the exact last eligible vote acknowledged by the server.

## Casting a Vote

Each pair shows the case's prompt above two outputs, or, for text outputs, beside them on a wide screen, with A on the left and B on the right, and never names their systems. Under the prompt, What to check lists the points to judge by, which the model never saw. Nothing is selected until you choose:

| Key        | Action                                                                           |
| ---------- | -------------------------------------------------------------------------------- |
| `a` / `b`  | A or B is better                                                                 |
| `t`        | Tie                                                                              |
| `x`        | Both bad                                                                         |
| `1` to `5` | Toggle an issue on output A, in the order the family's Issues popover lists them |
| `6` to `0` | Toggle the same issues on output B                                               |
| `Enter`    | Submit from the pair background, or activate the focused control                 |
| `u`        | Undo the last vote and show its pair again                                       |

Letter and digit shortcuts work while the voting panel has focus. Each new pair focuses the panel. Header controls and open popovers keep their own keyboard behavior.

An issue counts against the output you tag, whatever your verdict. Each family lists its own issues, such as wrong facts or a wrong tone on writing and off-scope changes or weak tests on code. Each output's Issues button explains and toggles them, including on phones. A family that lists none shows no Issues button.

Outputs appear exactly as written. The Markdown toggle renders both text panes as Markdown, with raw HTML shown as text and images left unloaded.

## Diffs

A task family scored by votes shows each solution as a diff of what it changed in the task's repository, with added and removed lines tinted and each file under its own header, and both solutions passed the task's hidden tests. Judge them as you would review a pull request against the prompt. The diff leaves out the repository's history and the caches that Python's tools write, so neither a commit message nor a trace of the agent's test runs shows.

## HTML Outputs

A family that renders HTML loads each output in a sandboxed frame from a second origin, under a policy that blocks network requests. The viewport presets of 375, 768, and 1280 px lay out both frames at that width, scaled down to fit the pane.

Keys pressed inside a frame stay in the frame, so the page shows a hint while one has focus. Click outside the frames before voting by keyboard.

Only approved public or synthetic content is published to the public arena. The frame's policy cannot block WebRTC, so publication review must treat executable HTML as active content.
