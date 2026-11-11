# Installation and troubleshooting

## Reference environments and scope

| Profile | Python | Python packages | External resources |
| --- | --- | --- | --- |
| Core / plan editing / source export | 3.12-3.14 target range | Standard library | Git for commit/release checks |
| Desktop editor | 3.12-3.14 target range | Four Qt packages pinned to 6.11.2 | Windows x64, audio device for playback |
| Windows WAV synthesis | Core/editor interpreter | Standard library | Windows PowerShell, Italian OneCore voices |
| Diagnostics / compressed export | Core/editor interpreter | Standard library | FFmpeg 9.0.2 on PATH |
| Optional Qwen/eSpeak | **3.12 only** | Full pinned requirements-qwen.txt | Windows x64, CUDA GPU, separately acquired models and voice references |

Core checks are exercised on Python 3.12 and 3.14; 3.13 is accepted but has not
been exercised. Qt 6.11.2 is the measured editor baseline. Qwen pins were captured
from the existing Windows/Python 3.12 runtime. This is a reproducible version
baseline, not a hermetic build or certification on all target machines.

Use 64-bit Python. Keep the repository separate from personal audio and manuscripts.
No voices, models or personal profiles are installed by the commands below.
Model downloads are a separate explicit action. Do not copy another person's
voice resources into this public repository.

## 1. Install core or editor in an isolated environment

From the repository directory, with Python installed and available to the Windows
`py` launcher (or substitute the full path to that Python executable):

```text
py -3.14 install_environment.py --profile core --dry-run
py -3.14 install_environment.py --profile core
.runtime\core\Scripts\python.exe environment_check.py --profile core
.runtime\core\Scripts\python.exe -m unittest discover -p test_*.py
```

The core uses no third-party packages, so its venv is created without pip.
For the desktop editor:

```text
py -3.14 install_environment.py --profile editor
.runtime\editor\Scripts\python.exe environment_check.py --profile editor --deep
.runtime\editor\Scripts\python.exe audiolibro.py editor example.md
```

The installer creates `.runtime/<profile>`, installs exact versions and runs
`pip check` for package-based profiles. It never upgrades global Python, changes
Windows voice settings, downloads models or overwrites another Python runtime.
An existing runtime must use the same Python version as the installer and must
be a venv. Pin validation and `--dry-run` happen before environment creation.
Installation failure leaves the environment available for diagnosis; it is not
automatically rolled back. The source and existing audio outputs are preserved.

## 2. Produce an original short sample

`example.md` is an original, neutral test fixture. Prepare a fresh plan:

```text
.runtime\core\Scripts\python.exe audiolibro.py prepara example.md .runtime\sample-plan.json
.runtime\core\Scripts\python.exe environment_check.py --profile windows
.runtime\core\Scripts\python.exe audiolibro.py esporta .runtime\sample-plan.json .runtime\sample.wav --allow-unassigned
```

The sample uses installed Windows voices, not bundled voice recordings. The
required default tokens are `MSTTS_V110_itIT_CosimoM` and `MSTTS_V110_itIT_ElsaM`.
Add Italian speech voices through Windows language/speech settings if needed.
Synthesis checks the tokens actually requested by each job before starting its
backend. Keep the sample plan and audio outside tracked files. A plan cannot
overwrite an existing plan; choose a new filename to repeat this test.
Verify that the WAV and its `.indice.json` exist, then listen to the whole sample.
Successful export does not establish correct pronunciation or timing by itself.

## 3. Optional FFmpeg

Install FFmpeg 9.0.2 for diagnostics and compressed exports, preserving its own
license, and add its `bin` directory to PATH. The installer does not download or
install this executable. Verify:

```text
python environment_check.py --profile diagnostics
python audiolibro.py analizza .runtime .runtime\sample-analysis.json
```

The checker validates the external version. A different FFmpeg version is
reported as outside the measured baseline, not silently accepted.

## 4. Optional isolated Qwen/eSpeak environment

This optional installation is large and uses the network for packages; no model
weights or voice references are downloaded. First inspect the commands:

```text
py -3.12 install_environment.py --profile qwen --dry-run
py -3.12 install_environment.py --profile qwen
python environment_check.py --profile qwen --packages-only --deep
```

`installa_qwen.py` remains a compatibility entry point to the same installer;
run it with Python 3.12. It no longer depends on a separately bootstrapped uv.
Torch/torchaudio are installed first from the official CUDA 12.6 index, then the
complete application lock is applied and `pip check` is run. The baseline uses
torch 2.14.1+cu126 and torchaudio 2.11.0+cu126. This difference is intentional:
[torchaudio 2.11 uses the stable ABI and supports PyTorch 2.11 and later](https://docs.pytorch.org/audio/stable/installation.html).
The exact Windows/Python 3.12 wheels are present in the official
[torch](https://download.pytorch.org/whl/cu126/torch/) and
[torchaudio](https://download.pytorch.org/whl/cu126/torchaudio/) indexes.

The deep check imports native libraries and tests CUDA availability without
loading a TTS model or synthesizing audio. `--packages-only` explicitly omits
model-resource checks. A regular Qwen check also looks for cached configuration
and safetensor files; this presence check does not certify all model shards.
The actual backend validates loading. References remain user-provided and are
checksum-validated by the backend. To inspect another existing runtime without
modifying it, use `--runtime-python PATH_TO_PYTHON --packages-only`.

If you deliberately choose to obtain models after reviewing their licenses:

```text
.runtime\qwen\Scripts\python.exe qwen_backend.py download
python environment_check.py --profile qwen --model base --deep
```

The backend command downloads both configured models. Their separate licenses
apply. No model files or voice resources belong in Git. The code honors `HF_HOME`
if explicitly set; otherwise its cache is `.runtime/huggingface`. Offline
generation requires the needed model to be cached in that location.

## Updates, recovery and offline use

The desktop automatically checks isolated library profiles once per day and on
lockfile changes. Approved dependency versions are installed into a new runtime
slot, checked with pip and native imports, and activated using one atomic pointer
replacement. The previous runtime remains available; nothing is renamed while
its interpreter or Qt DLLs are in use. Candidate installs require binary wheels;
if a pinned optional package has no wheel, activation is refused and the old
runtime is retained. Use the regular optional installer for that platform, or
review a new dependency baseline. The updater never downloads models or voices.

```text
python library_updates.py --profile editor
python library_updates.py --profile qwen
python library_updates.py --profile editor --rollback
```

Rollback is available when an earlier isolated runtime exists. It pauses startup
adoption until the explicit update command is run again. Source lockfiles are not
changed by rollback; if reverting to a different dependency baseline, use its
matching source commit. Set `AUDIOBOOK_AUTO_UPDATE=0` to disable startup updates.
Source-library patch proposals are generated weekly by Dependabot and require
review/verification before merge. Major/minor and CUDA-stack changes are excluded
from unattended dependency proposals. No automatic source merge is configured.

Pull a completed source commit, read CHANGELOG.md, inspect any changed lockfiles
and rerun the installer with the same profile/interpreter. The exact application
versions are reapplied; source upgrades are not silent package upgrades. Preserve
existing runtimes if changing Python; create a separate checkout/environment
instead of deleting a working installation. The installer does not uninstall
unrelated extra packages. A fully offline package installation requires a
separately prepared wheelhouse; the source ZIP alone is not an offline runtime.

| Failure | Action |
| --- | --- |
| Python outside range / Qwen on 3.14 | Invoke the installer with `py -3.12` for Qwen. |
| Qt missing or a different version | Rerun the editor installer and launch with its `.runtime/editor` Python. |
| OneCore token missing | Install Italian speech resources through Windows settings; rerun the Windows check. |
| Qwen interpreter missing | Run its optional installer with Python 3.12; avoid copying the global environment. |
| CUDA/native import fails | Check the GPU driver and the pinned environment using `--packages-only --deep`. |
| Model cache missing | Explicitly acquire models if desired; no automatic download occurs during synthesis. |
| TLS / package download fails | Check network/certificates. Never disable TLS verification to install packages. |
| Qwen package installation fails | Preserve the environment and pip error; pinned versions do not guarantee a wheel on every platform. |

Checks print structured JSON and return exit status 1 on failure. Read `fix` for
each failing item. Generation refuses missing/incompatible prerequisites before
starting a synthesis backend. Checks do not certify audible performance.

## Acceptance coverage

Observed on 2026-11-11 on the current Windows host: fresh core and editor venvs
were created successfully, the four exact Qt pins installed with no broken
requirements, Qt widget/multimedia/SVG imports succeeded, and the original sample
was exported through Windows OneCore. The neutral suite passed under Python
3.12.16 and 3.14.10 (26 tests). These are same-host installation checks, not an independent
clean-Windows acceptance run. The Qwen package baseline is inspected against the
existing optional runtime rather than downloaded again as a fresh installation.
Its package/native deep probe passed 93 checks, including CUDA and eSpeak library
availability. No Qwen model was loaded and no voice was synthesized by that probe.

Automated checks cover missing dependencies, invalid pins, absent optional
backends, dry-run isolation and preservation of an existing WAV on preflight
failure. A fresh venv test on the current host is not a clean Windows machine.
A second operator must still follow these English instructions on clean Windows,
install the editor and selected engines, export the sample and assess playback.
That human acceptance check remains open; do not report milestone 2 as fully closed
until it is recorded against the tested commit and build.

References: [Qt for Python installation](https://doc.qt.io/qtforpython-6/gettingstarted.html),
[Qwen3-TTS upstream](https://github.com/QwenLM/Qwen3-TTS), and the official indexes
and compatibility documentation linked above. Observed package pins are measured
local configuration, not an upstream promise of application-level compatibility.
