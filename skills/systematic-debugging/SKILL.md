---
name: systematic-debugging
description: A disciplined loop for finding the actual root cause of a bug before touching code — reproduce it reliably, localize it to the smallest failing unit, form one falsifiable hypothesis at a time, and verify the hypothesis before writing any fix.
whenToUse: Any time you hit an error, a failing test, or behavior that doesn't match what the code is supposed to do — before proposing or writing a fix. Especially important when a plausible explanation occurs to you immediately, since that's exactly the guess most likely to be wrong and most tempting to skip verifying.
disable-model-invocation: false
user-invocable: true
---

# Systematic Debugging

The failure mode this exists to prevent: seeing an error, recognizing a
familiar shape, and changing code based on that pattern-match without ever
confirming the pattern-match was actually correct for *this* bug. That
produces fixes that make the symptom go away by accident (or don't fix
anything at all) while the real defect stays in place. Follow this loop
instead. Do not skip steps because the bug "seems obvious."

## 1. Reproduce it, reliably, first

Before reading a single line of source in search of a cause, get a minimal,
repeatable way to trigger the failure — a specific test, command, or input.
If you can't reproduce it on demand, you cannot verify a fix later; you can
only hope. If the failure is intermittent, spend real effort narrowing what
makes it appear (a specific input size, a race, a particular ordering)
before moving on — "sometimes it fails" is not yet a debuggable bug.

## 2. Read the actual code and the actual output

Read the real, current source at the failure site and the real error
output/stack trace/log — not your memory of what that code does, and not
what you'd expect a function with that name to do. Note the exact error
message, exact line, exact input values. If there's a stack trace, read it
from the innermost frame outward; the first frame that's actually in code
you own is usually more informative than the outermost one.

## 3. Localize before you theorize

Narrow the failure to the smallest scope that still reproduces it: which
function, which branch, which input value flips it from pass to fail. Binary
search is a legitimate tool here — comment out half the suspect code path
(or bisect commits, or bisect input size) to cut the search space in half
each round, rather than staring at the whole thing hoping for insight.

## 4. Form exactly one hypothesis, and state it

Write down, explicitly, what you believe is wrong and why — in a sentence
you could say out loud. "I think X is null here because Y never initializes
it when Z" is a hypothesis. "Something's probably off with the state" is
not — it's not falsifiable, so it can't guide the next step. If you have
several candidate causes, rank them and test the most likely one first; do
not test them by changing code for all of them at once, since a fix that
"works" then leaves you unable to say which change actually mattered.

## 5. Verify the hypothesis BEFORE writing the fix

Add a log line, a print, an assertion, or a debugger breakpoint that would
prove or disprove the hypothesis — then run it and look at the actual
result. Do not skip straight to changing code because the hypothesis feels
right. A hypothesis that survives this check is one you can fix with
confidence; a wrong guess costs one small verification step instead of a
wasted edit and a second round of debugging.

## 6. Make the smallest fix that addresses the verified cause

Once the cause is confirmed, write the minimal change that fixes it —
resist the urge to also refactor nearby code, rename things, or fix
unrelated things you noticed along the way in the same pass. A larger diff
is harder to verify and more likely to introduce a second bug while fixing
the first.

## 7. Re-run the original reproduction, then the full suite

Confirm the exact repro from step 1 now passes. Then run the broader test
suite (not just the one test you were focused on) — a fix for one bug can
break something else, and you want to know that now, not after declaring
done. (See the `verification-before-completion` skill for the full
pre-completion checklist this feeds into.)

## 8. Add a regression test, if one doesn't already cover this

If the bug wasn't already caught by an existing test, that's a coverage
gap — write a test that reproduces the original failure and would catch it
if it recurred, using the same minimal repro from step 1.

## Anti-patterns to avoid

- **Shotgun debugging**: changing several things at once and re-running,
  hoping one of them fixes it. You'll rarely know which change mattered, and
  you'll often leave dead, irrelevant changes behind.
- **Fixing the symptom in the wrong layer**: e.g., adding a null check at
  the call site instead of fixing why the value is null in the first place.
  Sometimes a defensive check is the right call, but only after you know
  *why* the value can be null — not as a substitute for finding out.
- **Declaring victory because the specific error stopped appearing.** The
  error disappearing is consistent with "fixed" but also with "now failing
  silently" or "now failing somewhere else." Confirm the actual expected
  behavior, not just the absence of the original message.
