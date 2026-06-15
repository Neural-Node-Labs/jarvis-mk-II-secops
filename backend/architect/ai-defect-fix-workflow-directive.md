# AI Defect-Fixing Workflow Directive

## Purpose
This directive defines the step-by-step process an AI should follow when fixing a reported bug or defect, mirroring how Claude approaches debugging: understand first, change minimally, verify thoroughly.

## 1. Reproduce the Defect
- Before touching any code, try to reproduce the reported issue (run the failing test, trigger the bug, reproduce the error message).
- If it can't be reproduced, say so explicitly rather than guessing at a fix.
- Capture the exact error, stack trace, logs, or unexpected output — this is the baseline for "fixed."

## 2. Understand the Root Cause
- Trace the issue back to its actual source, not just where the symptom appears.
- Read the surrounding code and related modules to understand intended behavior before assuming something is "wrong."
- Distinguish between: a logic bug, a bad assumption, missing edge-case handling, a data/config issue, or a misuse of an API/library.
- Don't fix the first thing that looks suspicious — confirm it's actually the cause (e.g., add a temporary log/print, step through the logic, or write a minimal repro).

## 3. Scope the Fix
- Identify the smallest correct change that addresses the root cause — not the symptom.
- Avoid fixing multiple unrelated issues in the same change; note them separately for the user instead.
- Check whether the same bug pattern exists elsewhere in the codebase, and flag it (don't silently fix unrelated occurrences unless asked).

## 4. Implement the Fix
- Match existing code style and conventions.
- Add error handling or input validation only as needed to address this defect — don't over-engineer.
- If the fix involves a tradeoff (e.g., performance vs. correctness, broad vs. narrow condition), note it.

## 5. Verify the Fix
- Re-run the original reproduction steps and confirm the defect no longer occurs.
- Run the existing test suite to check for regressions.
- Add a new test that specifically covers this defect (and would have failed before the fix), if the project has a test setup.
- If verification isn't possible in the current environment, state that clearly rather than implying it was tested.

## 6. Communicate the Result
- Summarize: what was broken, why it was broken (root cause), and what changed to fix it.
- Note any residual risk, edge cases not covered, or related issues spotted but not fixed.
- Keep the diff focused — reviewers should be able to see exactly what fixed the bug without unrelated noise.

## 7. Honesty Checks
- Never claim a fix resolves an issue without having traced it to that root cause or verified the symptom is gone.
- If the root cause is uncertain, say so and present the fix as a hypothesis with reasoning, not a guaranteed solution.
- If multiple plausible causes exist, mention them and explain why the chosen one was most likely.
