# Test Rules

Read the repository root `AGENTS.md` and `docs/architecture/` first.

- Tests are contracts: do not delete, skip, or weaken assertions to make CI pass.
- Every new Port/Adapter/Profile change needs a contract or regression test in the matching existing test area.
- Label evidence honestly: `Mock`/fixture tests are not real-device validation. Hardware evidence must name the device, port, command and observed result.
- Architecture boundary changes must update `tests/unit/architecture/test_import_boundaries.py`.
- Preserve the documented 14 known baseline failures; do not count them as new failures or hide new ones behind broad skips.
- For iOS hardware, follow `docs/ios-rig-playbook.md` and record the exact smoke/target.
