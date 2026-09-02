---
name: test-driven-development
description: Write a failing test before writing the implementation that makes it pass — red, green, refactor — so every behavior change is driven and confirmed by a test, not asserted from memory afterward.
whenToUse: Before writing implementation code for any new feature or bugfix where the desired behavior can be stated concretely. Use it from the start of the task, not retrofitted after the code already exists — writing tests after the fact tends to describe what the code does rather than what it should do.
disable-model-invocation: false
user-invocable: true
---

# Test-Driven Development

The point of this discipline is not "have tests" — it's that the test is
what defines "done," decided *before* the implementation exists, so the
implementation can't quietly narrow the definition of correct to match
whatever it happens to do.

## The loop: red, green, refactor

### 1. Red — write a test that fails for the right reason

Write the smallest test that expresses one specific piece of the desired
behavior, using the real interface you intend the code to have (function
name, arguments, return shape) even though it doesn't exist yet or doesn't
do this yet. Run it. Confirm it fails — and read *why* it failed:

- If it fails with an import/attribute error because the function doesn't
  exist yet, that's expected and fine.
- If it fails with an assertion mismatch that doesn't match your intent, or
  passes when it shouldn't, stop — the test itself is wrong before any
  implementation exists. A test you haven't watched fail is a test you
  don't actually know is testing anything.

### 2. Green — write the minimum code to pass, nothing more

Implement just enough to make the failing test pass. Resist writing
additional behavior "while you're in there" that no test currently demands
— if it's worth having, it's worth its own test first. Run the test suite
and confirm the new test passes AND nothing else broke.

### 3. Refactor — clean up with the safety net in place

Once green, look at both the test and the implementation for
duplication, unclear naming, or structure that would make the next test
harder to add. Refactor now, with tests passing before and after each
change, rather than deferring cleanup to "later" (later rarely comes, and
the mess compounds).

### 4. Repeat for the next piece of behavior

Pick the next smallest unstated behavior (an edge case, an error path, a
second valid input shape) and go back to step 1. Build up coverage
incrementally rather than trying to write one large test that covers
everything before any implementation exists.

## What makes a good test for this loop

- **One behavior per test.** A test named for what it verifies
  (`test_rejects_empty_input`, not `test_function_1`) that fails for exactly
  one reason. Multiple assertions are fine if they're all confirming the
  same single behavior from different angles; if they're checking unrelated
  things, split the test.
- **Test behavior, not implementation.** Assert on inputs and outputs (or
  observable side effects), not on internal helper functions or private
  state — a test that breaks every time you refactor without changing
  behavior is testing the wrong thing, and will train you to fear refactoring.
- **Cover the edge cases explicitly, don't assume they're implied.** Empty
  input, boundary values, the error/rejection path, not just the obvious
  happy path — each as its own small test, driven through the same red/green
  loop.
- **Keep it fast and deterministic.** A test suite people are afraid to run
  because it's slow or flaky stops being run before every change, which
  defeats the entire point of having it drive development.

## Anti-patterns to avoid

- **Writing the implementation first, then tests to match.** This produces
  tests that document current behavior rather than intended behavior — bugs
  present at writing time get "confirmed" as correct instead of caught.
- **Writing every test up front, then all the implementation at once.**
  Loses the tight red/green feedback loop that catches a wrong test or a
  wrong assumption early, one small step at a time.
- **A test that passes before you've written any implementation.** That
  test isn't testing anything real yet — go back and figure out why (wrong
  assertion, testing something already true by default, or a mocked
  dependency doing the work the real code should).
- **Skipping the refactor step because green is good enough for now.**
  Untouched duplication and unclear structure compound with every additional
  feature built on top of it.
