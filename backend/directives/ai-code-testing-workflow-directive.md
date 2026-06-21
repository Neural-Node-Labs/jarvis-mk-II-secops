# AI Code-Testing Workflow Directive

## Purpose
This directive defines how an AI should test code after writing or modifying it, mirroring how Claude approaches verification: test for real, cover the right cases, and report results honestly.

## 1. Always Verify, Don't Assume
- After writing or changing code, actually run it (or the relevant tests) before presenting it as done.
- Never claim code "works" or "should work fine" without having executed it when execution is possible.
- If the environment can't run the code (missing dependencies, no test harness, etc.), say so explicitly instead of implying it was tested.

## 2. Test at the Right Level
- **Unit tests**: test individual functions/classes in isolation, especially logic with branches, edge cases, or calculations.
- **Integration tests**: test how components work together (API calls, database access, file I/O) when the change affects interactions between parts.
- **End-to-end / manual checks**: for UI or full-flow changes, run the actual scenario a user would trigger.
- Match the project's existing test framework and conventions rather than introducing a new one.

## 3. Cover the Right Cases
- **Happy path**: the normal expected input/output.
- **Edge cases**: empty inputs, zero, negative numbers, very large values, boundary conditions (off-by-one).
- **Error cases**: invalid input, missing data, network/file failures — confirm errors are handled, not just that valid input works.
- **Regression cases**: if fixing a bug, add a test that reproduces the original failure and confirms it's resolved.

## 4. Run Before and After
- Where practical, run the test suite *before* making changes to establish a baseline (especially to distinguish pre-existing failures from new ones).
- Run it again *after* changes and compare results.
- If pre-existing tests fail and are unrelated to the current task, note this rather than silently ignoring or "fixing" unrelated failures.

## 5. Write Tests That Actually Test Something
- Avoid tests that always pass regardless of correctness (e.g., asserting `true == true`, or only checking that code "doesn't crash" when more specific checks are possible).
- Assert on actual expected values/outputs, not just absence of errors.
- Keep tests readable: clear setup, clear expectation, clear assertion — a failing test should make the problem obvious.

## 6. Report Results Honestly
- State what was run and the actual outcome (pass/fail, output, errors) — don't paraphrase a failure as a success.
- If something fails, investigate and either fix it or report it clearly rather than hiding or rationalizing it.
- If only partial testing was possible (e.g., couldn't test the UI but unit tests pass), say exactly what was and wasn't covered.

## 7. Clean Up After Testing
- Remove temporary debug code, print statements, or scratch test files added solely for verification, unless they're meant to remain as part of the test suite.
- Ensure any new tests are properly placed in the project's test directory/structure so they run as part of normal CI, not just ad hoc.
