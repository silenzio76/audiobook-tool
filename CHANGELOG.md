# Changelog

## 0.1.0 — 2026-11-11

Initial public source release of the Windows-oriented audiobook manuscript review
tool. Distributed under GPL-3.0-or-later. This release is intended for developers
and early testing; a supported clean-Windows installation remains roadmap work.

- Prepare validated reading plans from Markdown while preserving source text.
- Review speaker assignments, spoken text, language, delivery and junction timing
  in the PySide6 desktop editor, with preview and playback controls.
- Route synthesis to installed Windows voices or separately installed optional
  Qwen/eSpeak resources; export audio and associated segment metadata.
- Analyze WAV levels, silence intervals and verified index associations.
- Export an allowlisted source tree with reproducible ZIP bytes, per-file hashes,
  a source snapshot identity and a caller-supplied build identifier.
- Verify release archives against a clean Git commit and matching version tag.
- Provide one authoritative `VERSION` and the `audiolibro.py --version` command.

No manuscript, saved chapter plan, personal voice profile, pronunciation
dictionary, voice recording, generated audio, model weight or runtime environment
is included. Legacy character-name presets and Italian code comments remain;
they are source configuration, not bundled voice samples. Existing production
regression fixtures remain in the private manuscript workspace; the repository
contains new neutral smoke and source-export tests.

### Validation and limits

The neutral tests cover plan round trips, fallback configuration and pause
overrides. Export tests cover exclusions, byte-identical rebuilds, manifest
validation, invalid/missing inputs and preservation on failure. Release tests
exercise a temporary Git repository, clean-tree/tag checks and extra-file
rejection. No claim is made that pronunciation is correct or that installation,
GUI operation, optional engines and playback have been validated on clean Windows
by a second operator. Dependencies are not yet fully pinned.
