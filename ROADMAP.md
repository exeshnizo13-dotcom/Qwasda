# Qwasda Development Roadmap

## Phase 1: Reliability and Clean CI

### Goals

- Stabilize the modular application lifecycle.
- Make all automated quality gates reliable and green.
- Ensure that distributable builds use the modular `qwasda` package.

### Scope

- UAC elevation without restart loops.
- Crash reporting and structured logging.
- Health monitoring and watchdog handling.
- Idempotent startup and cleanup behavior.
- Ruff, Black, and strict mypy cleanup.
- Unit, lifecycle, and regression tests.
- Coverage reporting.
- Modular PyInstaller build and EXE smoke test.

### Completion criteria

- All tests pass on supported Python versions.
- Ruff, Black, and strict mypy pass without ignored failures.
- The modular EXE builds and completes a Windows smoke test.
- Startup failures and all exit paths release acquired resources.
- CI is green on the default branch.

## Phase 2A: Custom Dictionaries

### Goals

- Allow users to extend autocorrection with their own word lists.

### Scope

- Add, remove, enable, and disable custom dictionaries.
- Import validated word-list files.
- Integrate enabled dictionaries into autocorrection.
- Persist dictionary metadata and state.
- Migrate existing configuration safely.
- Handle missing, malformed, duplicate, and oversized dictionaries.

### Completion criteria

- Dictionary operations are covered by unit and integration tests.
- Invalid dictionaries cannot break application startup.
- Enabling or disabling a dictionary takes effect predictably.
- Existing configurations migrate without losing user data.

## Phase 2B: Hotkey Customization

### Goals

- Let users customize layout switching and application actions.

### Scope

- Configure supported hotkeys.
- Validate unsupported and conflicting combinations.
- Apply changes without requiring a full reinstall.
- Restore default hotkeys.
- Persist hotkey configuration.

### Completion criteria

- Conflicting combinations produce a clear validation error.
- Default hotkeys can always be restored.
- Customized hotkeys work after application restart.
- Existing double-tap behavior remains covered by regression tests.

## Phase 2C: Privacy-Safe Statistics

### Goals

- Show useful usage statistics without collecting typed content.

### Scope

- Count layout switches, autocorrections, and manual corrections.
- Store only aggregate counters and timestamps required for summaries.
- Never persist typed words, phrases, or clipboard content.
- Add controls to disable and clear statistics.
- Display a compact dashboard in the tray UI.

### Completion criteria

- Statistics contain no typed text or other sensitive content.
- Collection can be disabled and all stored data can be deleted.
- Corrupted statistics data cannot prevent startup.
- Dashboard values match the underlying counters.

## Phase 3: Packaging and Distribution

### Goals

- Provide a predictable Windows installation and upgrade experience.

### Scope

- Create an NSIS installer.
- Install application files, dictionaries, shortcuts, and startup options.
- Preserve compatible user configuration during upgrades.
- Provide a clean uninstall path without deleting user data unexpectedly.
- Produce versioned and reproducible release artifacts.
- Test clean install, upgrade, repair, and uninstall scenarios.

### Completion criteria

- Installation and uninstall work on supported Windows versions.
- Upgrading preserves supported settings and learned data.
- Release artifacts contain the modular application.
- CI produces a versioned installer and standalone artifact.

## Phase 4: Secure Updater

### Goals

- Deliver application updates safely and recoverably.

### Scope

- Check a trusted release source for updates.
- Verify signatures or cryptographic checksums before installation.
- Download updates over authenticated HTTPS.
- Replace application files atomically.
- Roll back after a failed update.
- Support stable and optional beta channels.
- Provide clear user consent and progress reporting.

### Completion criteria

- Invalid or unsigned update packages are rejected.
- Interrupted updates leave a runnable application or restore the previous version.
- Update checks can be disabled.
- Upgrade and rollback paths are covered by automated tests.

## Phase 5: Advanced Behavior and Accessibility

### Goals

- Improve context awareness, learning quality, and accessibility.

### Scope

- Add per-application settings and hotkeys.
- Improve autocorrection and learning algorithms using measurable rules.
- Provide keyboard-only access to all user-facing controls.
- Improve screen-reader labels, focus order, notifications, and contrast.
- Add controls for reviewing and clearing learned behavior.

### Completion criteria

- Per-application settings do not leak between applications.
- Algorithm changes are measured against a regression corpus.
- False-correction rates do not regress beyond an agreed threshold.
- Core workflows are usable without a mouse.
- Accessibility checks are part of release verification.

## Delivery Rules for Every Phase

- Define acceptance tests before implementation is considered complete.
- Keep configuration and user-data migrations backward compatible.
- Do not mark a phase complete while required CI jobs are failing.
- Update the changelog and version when releasing user-visible behavior.
- Build and smoke-test the same modular entry point that is distributed to users.
