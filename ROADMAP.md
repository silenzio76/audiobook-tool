# Audiobook Tool Roadmap

Recorded on 2026-11-09. Implementation is planned in the order below. The first milestone is a reproducible Windows tool for manuscript review. A public audiobook and a tool distributed to other authors are separate release decisions.

Current status (2026-11-11): the initial public source release `v0.1.0` is
published at `silenzio76/audiobook-tool`. Milestone 2 has implemented English
project materials, pinned installation profiles and preflight checks; its
clean-Windows second-operator acceptance test remains open. Verified code changes
increment BUILD and are committed automatically through `dev_commit.py`.

## 1. Standalone GitHub repository and releases

- Establish a dedicated GitHub repository for the audiobook tool. Decide ownership, visibility, licensing and the history to import before the first push. Review the import for manuscript text, chapter plans, voice samples, credentials, caches and generated outputs. Use original or cleared fixtures in the repository.
- Define the supported compatibility contract, including saved plans and profiles, documented CLI options and output formats. Use `MAJOR.MINOR.PATCH` for releases. Increment MAJOR for incompatible changes to that contract, MINOR for compatible features and PATCH for compatible fixes. Keep one authoritative release version and use tags such as `v1.2.3` with English release notes.
- Assign each build a distinct identifier linked to its version, commit and build run, for example `1.2.3+build.47` in the build manifest. Build metadata identifies the artifact and does not affect SemVer precedence. Record a checksum for each distributable and verify that a clean checkout can reproduce the build process. The initial source release is `0.1.0`; published tags and assets remain immutable.

References: [Semantic Versioning 2.0.0](https://semver.org/) and [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

### Local implementation checkpoint — 2026-11-11

Implemented `source_export.py`: explicit source allowlist, local-data exclusions,
dependency and syntax checks, deterministic ZIP metadata, per-file SHA-256,
content snapshot identity, explicit candidate build ID and validated atomic
archive publication. The candidate includes a data-excluding `.gitignore`, an
English README and new neutral tests. It can rebuild outside this manuscript
workspace. Tests cover exclusions, reproducibility and preservation on failure.

This completes the local source-export preparation only. Milestone 1 remains
open: GitHub ownership/name, visibility, licensing, history import and the initial
release version are unresolved. There is no repository, Git commit provenance,
release tag or distributable release. A supported compatibility contract,
authoritative SemVer version and commit-linked release build remain to implement
after those decisions. Legacy character names and Italian comments inside the
allowlisted code still require review before publication. See `README.md` for
commands and the exact verification limits.

### Publication decisions and implementation — 2026-11-11

The author selected the GitHub account used by RpgBuilderAndroid (verified remote:
`silenzio76/RpgBuilder`), public visibility, initial version `0.1.0` and exclusion
of voices. The copyleft requirement is implemented as GPL-3.0-or-later: conveyed
modified versions must provide corresponding source under that license; private
changes do not have to be uploaded. The dedicated target is
`https://github.com/silenzio76/audiobook-tool`, with fresh source-only history.
No recorded/generated voices, personal profiles, models, manuscripts or plans
are imported. Existing source presets are retained as code.

`VERSION` is authoritative; the CLI exposes `--version`. The source exporter now
supports release mode, requiring a clean dedicated Git checkout, an exact tracked
allowlist, bytes matching the commit and the corresponding version tag. Its
manifest records version, build ID, commit, tag, file hashes and archive identity.
`README.md` documents the initial schema/CLI/output compatibility surface;
`CHANGELOG.md` records the release scope and verification limits. Publication and
remote verification are recorded separately in the local release checkpoint.

## 2. English project materials and repeatable installation

Current checkpoint: project-owned Python comments/docstrings have been translated
in a separate pass. Executable ASTs for translation-only modules were compared
with the originals, excluding documentation strings; localized strings and
serialized values are retained. BUILD and automatic verified commits are now
part of the repository workflow. The installation/dependency pass follows.

Installation checkpoint: `install_environment.py` creates profile-specific venvs,
applies exact Qt or measured Qwen package pins and runs package checks. The offline
`environment_check.py` reports missing/incompatible Python, packages, Windows
voices, FFmpeg and optional model/native resources. Generation calls preflight
before backend execution. `INSTALLATION.md` describes setup, updates, the original
sample and recovery. No model/voice download is implicit. Automated failure and
fresh-venv checks do not replace the still-open clean-Windows second-operator
acceptance test; milestone 2 remains open for that observed human workflow.

- Use English for all project-owned code comments, docstrings and supporting files, including the README, roadmap, changelog, installation and troubleshooting guides, configuration comments, example documentation and release notes. Translate existing material in a dedicated pass and check that code behavior and serialized values are preserved. Manuscript excerpts, linguistic fixtures and localized user-facing text retain their intended languages.
- Inventory supported Python versions and separate core dependencies from optional engines, external executables, model weights and voice resources. Declare and pin the versions needed for supported builds, with a documented install and update path. Check availability, compatible versions and prerequisites before generation. Report what is missing and how to fix it.
- Verify on clean Windows with a second operator. They should be able to install the core, run a short sample and export audio using only the English instructions. Check each supported optional backend separately, including what happens when it is absent.

## 3. Error recovery and resource management

Library maintenance checkpoint: startup adoption of approved library lockfiles
uses separately validated venv slots and atomic pointer activation. Failed
candidates preserve the previous runtime. Weekly grouped patch proposals are
configured in Dependabot, without unattended merge or model/voice downloads.

- Preserve the source and the last valid output on cancellation, backend failure, missing resources or a full disk. Report the failed stage, affected segment and next action. Ensure that the published audio and its index belong to the same run. Resume from validated work when safe and restart the affected stage otherwise.
- Manage child processes, worker lifetimes, temporary files, cache, model storage and memory explicitly. Bound preloading, release resources after cancellation and offer safe cleanup of generated files. Reuse cached segments only when their source, voice, pronunciation and synthesis settings still match.
- Validate with a short multilingual fixture, deliberate interruption, restart and a pronunciation edit. Confirm that no stale audio is reused, no worker is left running and a partial export cannot replace a valid one.

## 4. PySide6 interface ergonomics and appearance

- Audit the existing Qt Widgets workflow on Windows 11 before redesigning it. Apply the project UI/UX skill during implementation. Prioritize opening a text, assigning voices, previewing, stopping, locating a failed segment and exporting.
- Clarify hierarchy and action states. Use coherent spacing, typography and alignment. Make progress and recovery controls easy to find. Check keyboard navigation, visible focus, contrast, resizing and DPI scaling on the target system.
- Observe a second operator completing the core workflow. Compare task completion, errors and points of hesitation before and after the changes. Review the appearance on the actual display at the tested sizes and DPI settings.

## 5. Segment editing and audio timeline

- Regenerate selected words within an existing clip. Align words to audio and verify that the splice preserves timing, voice and continuity.
- Keep generation history per segment. Allow preview and restoration of earlier takes, with their synthesis settings and a defined retention policy.
- Add a chapter timeline with clip boundaries, waveforms and editable gaps. Keep timeline playback and chapter export synchronized.
