# Repository workflow

- `VERSION` is the public release version. `BUILD` is the positive integer build
  counter; the original 0.1.0 source release is baseline 1.
- After each coherent code change, increment the build and commit automatically
  after verification. Use `python dev_commit.py --message "..."` to increment,
  run the neutral regression suite and commit the approved changes. Never commit
  temporary, incomplete or failed work. Documentation-only changes need no bump.
- Never move a published tag or replace its assets. Development commits do not
  create releases automatically. Choose a new version for the next release.
- Comments, docstrings and supporting material must be English. Preserve localized
  UI strings, serialized codes, linguistic fixtures and user input.
- Do not add voice samples, generated audio, personal profiles, manuscripts,
  model weights, caches or runtimes. Maintain the explicit export allowlist.
- For replacements, validate complete same-directory temporaries, preserve originals
  and replace atomically. Prefer Python for scripts. Keep work focused; no agents.
