# Audiobook manuscript review tool

Copyright (C) 2026 silenzio76. SPDX-License-Identifier: GPL-3.0-or-later.

Version: see [`VERSION`](VERSION). Repository: [silenzio76/audiobook-tool](https://github.com/silenzio76/audiobook-tool).

Windows-oriented Python software for preparing reading plans, assigning voices,
reviewing delivery and exporting audio. The desktop editor uses PySide6.

## Repository preparation: source candidate

`source_export.py` implements a reproducible source export for review and release
packaging. Repository creation and publication are separate Git/GitHub operations;
the exporter never pushes files or publishes a release. This repository begins
with a fresh source-only history; manuscript-project history is not imported.

From this directory:

```text
python -X utf8 -m unittest test_source_export test_standalone
python -X utf8 source_export.py repository_candidates --build-id local-review-001
```

The command prints the full archive SHA-256 and the content snapshot SHA-256.
The archive filename includes its full checksum. `SOURCE_MANIFEST.json` inside
the archive records the build identifier, every included file and its hash,
exclusions, pending decisions and review limits. Rebuilding identical source with
the same build identifier produces identical archive bytes. Timestamps, directory
locations and filesystem enumeration order do not affect the result. Stored ZIP
members avoid compressor-version differences. A complete temporary archive is
validated before atomic publication. An existing damaged candidate is preserved
and reported as an error.

The export includes only the explicit `EXPORT_FILES` allowlist. It excludes local
profiles, pronunciation dictionaries, chapters and saved plans, voice samples,
models, runtime environments, caches, generated audio, backups, Git history and
book-specific scripts. Only the two new neutral test modules are exported.
The exported directory can rebuild itself using the same command.
The included `.gitignore` also excludes local plans, voice data, media, runtime
directories and temporary files from a future repository. It does not remove
anything that has already been committed and is not a substitute for review.

The allowlist is an inclusion boundary, not proof that included code is free of
credentials or third-party material. Review the actual candidate before upload.
Existing source code still contains series character names and Italian comments;
English translation belongs to roadmap milestone 2. The public repository and
initial version `0.1.0` are authorized by the owner. `VERSION` is the only
authoritative release version. Default exports record `release: false` and
`source_commit: null`; hashes identify a candidate without commit provenance.

For a source release, start from a clean dedicated checkout whose tracked files
exactly match the allowlist. The `v` tag must match `VERSION` and point to HEAD:

```text
python -X utf8 source_export.py repository_candidates --build-id release.001 --release
```

The exporter checks each source byte against the commit, then records the commit,
tag, version and build identifier in the archive manifest. The ZIP checksum is
printed and included in the filename. Use a distinct build identifier for each
release build run; repeating the same run against the same commit is reproducible.
A source release is not a bundled runtime or a certified clean-Windows build.

## Isolated verification

Extract the ZIP into an empty directory. From that directory run:

```text
python -I -X utf8 -c "import sys, unittest; sys.path.insert(0, '.'); result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(['test_source_export', 'test_standalone'])); sys.exit(not result.wasSuccessful())"
python -I -X utf8 -c "import runpy, sys; sys.path.insert(0, '.'); sys.argv = ['audiolibro.py', '--help']; runpy.run_path('audiolibro.py', run_name='__main__')"
```

These tests use the standard library and original, neutral text; the release
provenance test additionally requires Git and skips if Git is unavailable. They exercise
plan preparation and reading, fallback configuration, junction timing, archive
membership, reproducibility, validation failures and preservation of existing
artifacts, clean release checkouts and matching version tags. They do not load
speech models or certify audible pronunciation.
The project-wide production tests remain in the manuscript workspace.

## Current compatibility and dependencies

The existing serialized plan and profile schema version remains `1`. This change
does not migrate saved plans, profiles, narration settings or pronunciation rules.
Existing CLI commands remain `prepara`, `editor`, `leggi`, `esporta` and `analizza`.
`python audiolibro.py --version` prints the authoritative release version.
Use `python audiolibro.py --help` and command-specific `--help` for arguments:

```text
python audiolibro.py prepara sample.md sample.json
python audiolibro.py editor sample.json
python audiolibro.py leggi sample.md
python audiolibro.py esporta sample.json sample.wav --allow-unassigned
python audiolibro.py analizza recordings report.json
```

`esporta` also accepts `--voice-mode narratore|cast` and `--voice-design`.
Plans retain a source path
and source SHA-256 and must be prepared against the actual input file. WAV exports
use `.indice.json`; MP4 exports use `.mp4.indice.json` metadata. This documents
the `0.1.0` compatibility surface; internal Python functions are not a stable API.
The software is in initial development. Follow [Semantic Versioning](https://semver.org/)
for subsequent releases and document any schema, CLI or output changes. Do not
modify a published version; publish another version instead.

Source export and neutral tests require only Python's standard library. The
desktop editor requires `requirements-editor.txt`. Windows synthesis uses the
installed OneCore voices and `voce_windows.ps1`; audio transformations need FFmpeg.
Qwen synthesis additionally requires its isolated runtime, model weights and
voice resources. The existing Qwen installer expects a local uv executable and
a CUDA GPU; those resources are not bundled. A supported Python-version matrix,
pinned optional dependencies, repeatable installation and clean-Windows testing
remain milestone 2 work. Do not treat a successful source smoke test as proof of
installation, desktop playback or synthesis on a clean machine.

## License and optional resources

The project-owned software and documentation are licensed under the GNU General
Public License, version 3 or (at your option) any later version. See `LICENSE`.
The program comes WITHOUT ANY WARRANTY. Recipients may redistribute and modify it
under those terms. When conveying modified versions, provide the corresponding
source under the same license and state the changes. Private modifications do
not have to be uploaded to this repository. See the [GPLv3 conditions](https://choosealicense.com/licenses/gpl-3.0/).

This repository contains no voice samples, recorded voices, generated audio,
personal profiles, manuscripts, runtime environments or model weights. Existing
source presets contain character names and voice descriptions; these are not
voice recordings. Optional packages, external executables, Windows voice resources
and model weights retain their own licenses and are installed separately. This
software license does not grant rights to third-party voices or models, input
manuscripts or generated media. Contributors should identify material they add
and retain applicable notices.
