# Painter 12.1 compatibility fixes

Status: done

## Task 1: Preserve channels and correct baking/runtime support

Status: done
Owner: Codex
Worktree / branch: `.worktrees/painter-12-1-fixes` / `fix/painter-12-1-compatibility`

### Goal

On Painter 12.1.4, edits preserve unaffected Fill sources, unsupported Paint-channel edits fail honestly, regular non-UDIM projects can bake, and bake-preset capability reporting matches the APIs used.

### Context and scope

The live comparison reproduced BaseColor resets on Roughness/Metallic edits, transient Python-only Paint channel assignments, and non-UDIM baking rejected for an empty tile list. Preset capture/application worked although the capability flag was false. Preserve file-root protections, confirmations and unrelated behavior. Commit, finish into dev, push dev, pull dev into main, and push main are authorized. MCP registration and installation changes remain out of scope. Keep tooling portable across Windows/macOS.

### Work

- Reproduce channel behavior against the installed Painter API and add regression tests executing actual remote snippets.
- Preserve unaffected sources with a supported API path, including affected channel-activation integrations.
- Reject unsupported Paint channel edits and recipe requests before mutation.
- Distinguish non-UDIM baking from disabled UDIM tiles in preflight and start paths.
- Correct the preset capability probe and update affected documentation/examples.
- Report both the edit error and a failed rollback, and regression-test color restoration failures.
- Run the complete automated suite and live regressions, including Unity exports and original-project restoration.

### Done

- Regression failures demonstrated before fixes; complete automated suite passes afterward.
- Live readback verifies unchanged colors/resources, rejection without mutation, both baking workflows, and accurate capability reporting.
- Record branch/worktree, verification evidence and remaining platform limits. No unverified implementation is marked complete.

Verification completed on 2026-09-09:

- The executable-snippet regression suite reproduced 21 failures against the original implementation; the expanded full suite now passes all 102 tests.
- The agreed review follow-up added three recovery cases: a retained-color write failure with successful recovery, a failed rollback mask write, and a failed rollback color write. Both double-failure diagnostics failed before the reporting fix and now pass; errors retain the original and restoration failures and explicitly warn that restoration is incomplete. No additional live fault injection was performed; the prior normal-path live results remain applicable.
- `scripts/live_compatibility.py` passed 30 live checks on Painter 12.1.4 / Python API 0.3.5: uniform/bitmap/procedural/anchor preservation, invalid-resource rejection, explicit mask preservation/guards, Paint rejection, updated recipe, both baking workflows, preset round-trips, disabled-set/empty-UDIM rejection, Unity exports, and original digest restoration.
- Output evidence: `painter-compatibility-t2qe9x1v/results.json` in this machine's temporary directory. Original project was restored; no user shelf or MCP registrations were changed.
- No installation was performed. macOS live validation remains unavailable; implementation and test tooling use portable Python APIs.
