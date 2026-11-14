"""Read the single authoritative release version without importing a backend."""
from pathlib import Path
import re

PUBLIC_NAME = "will-o'-the-wisp"

__version__ = Path(__file__).with_name('VERSION').read_text(encoding='ascii').strip()
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', __version__):
    raise ValueError('VERSION must contain one MAJOR.MINOR.PATCH release version.')

_build_text = Path(__file__).with_name('BUILD').read_text(encoding='ascii').strip()
if not re.fullmatch(r'[1-9][0-9]*', _build_text):
    raise ValueError('BUILD must contain one positive integer.')
__build__ = int(_build_text)
__build_version__ = f'{__version__}+build.{__build__}'
