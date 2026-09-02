---
name: verification-before-completion
description: The concrete pre-completion checklist to run before declaring any task done, fixed, or passing — restate the original ask, re-read your own diff, re-run the tests and read their actual output, and check named acceptance criteria explicitly. Evidence before the claim, every time.
whenToUse: Immediately before saying a task is complete, a bug is fixed, or tests are passing — and before committing or opening a PR on that basis. Use it even (especially) when the change feels small or obviously correct; that feeling is exactly what this checklist exists to check, not to trust.
disable-model-invocation: false
user-invocable: true
---

# Verification Before Completion

This is the human/model-readable procedure form of the gap `graph/verify.py`
enforces in code: a persona-configurable pre-completion check that must run
before a turn is allowed to close as "done." Every harness surveyed while
researching Tapestry's design (DeepSeek Harness, openhands-tools, Hermes Bot
Mode) was missing a real version of this step — see
`docs/vendor-research/ANALYSIS-deepseek-harness.md` §2. It is a genuine gap,
not boilerplate, and it is Tapestry's own differentiator to get right. Do
not treat this skill as a formality to nod at before saying "done" anyway —
its entire value is in actually running the four checks below and being
willing to report back "not actually done" when one of them fails.

Do not declare a task complete, fixed, or passing until all four checks
below have been run and their actual output read — not inferred, not
remembered from a previous step, not assumed because the change "looks
right."

## 1. Restate the original ask, in your own words

Before checking anything else, write one or two sentences stating what was
actually asked for — not what you ended up doing. Then compare: does the
change you made address ALL of that, or only the part that was easiest, or
only the part you noticed first? A restatement that quietly narrows or
drifts from the original ask is the most common way a task gets marked done
while actually being half-done. If the ask had multiple parts (fix the bug
*and* add a regression test; implement the feature *and* update the docs
that reference it), name each part and check each one separately — don't
let "the interesting part" stand in for the whole ask.

## 2. Re-read your own diff, top to bottom, as if reviewing someone else's PR

Look at the actual, current diff — not your mental model of what you
changed. Specifically check for:

- **Leftover debug code**: stray print/log statements, commented-out old
  code, temporary hacks added while investigating that were never removed.
- **Unintended changes**: files touched that have nothing to do with the
  task, formatting-only changes mixed into a substantive diff, a config
  value flipped for local testing and never flipped back.
- **Scope creep or scope gaps**: changes beyond what was asked (flag them,
  don't silently keep them) or, more commonly, files that *should* have
  changed together but didn't (an interface changed but not all its
  callers; a new field added but not every place that already serializes
  that type).
- **TODOs and placeholders**: anything you left as "good enough for now"
  that the original ask didn't actually authorize leaving unfinished.

## 3. Re-run the tests and read the actual output

Run the real test command for this codebase — not just the one test you
were focused on while making the change, the broader suite that could
plausibly be affected. Then actually read the output:

- Confirm the count of passed/failed/skipped, not just "no red text
  scrolled by."
- If something is skipped or was already failing before your change,
  confirm that's expected and not something your change caused or is now
  hiding.
- If you wrote new tests as part of this task, confirm you watched them
  fail before your implementation existed (see the `test-driven-development`
  skill) — a new test that passed on its very first run without ever having
  failed is a test whose ability to catch a real bug hasn't been
  established.
- "It should pass" or "this is a minor change so it's probably fine" are
  not evidence. The command's actual output is the evidence. If you have
  not run it since the last edit, you do not yet know the answer.

## 4. Check every acceptance criterion the original ask actually named

Go back to anything the ask specified explicitly — exact output format,
performance characteristic, edge cases mentioned by name, a specific error
message, a file that had to be created in a specific location — and check
each one individually against what you actually built. A change can pass
every test in the existing suite and still miss a named requirement that
had no test written for it yet.

## If any check surfaces a problem

Fix it and re-run the checks that could be affected — don't patch the one
thing you found and declare done without re-verifying. If something is
uncertain rather than clearly wrong (a flaky test, an ambiguous piece of the
original ask, a case you're not sure was in scope), say so explicitly in
your completion report. State the uncertainty; do not round it up to "done"
because raising it feels like an admission of incompleteness — an honest
"done, except X is still unverified" is far more useful than a false "done."

## Reporting completion

Once all four checks pass, state completion with the evidence attached, not
as a bare assertion: which command you ran and what it reported, what the
diff review turned up (or that it turned up nothing), and how the result
maps back to the restated ask from step 1. "Done — ran `pytest`, 42 passed,
0 failed; diff review found one leftover debug print which I removed; both
parts of the original ask (the fix and the regression test) are covered" is
a verification report. "Should be good now" is not.
