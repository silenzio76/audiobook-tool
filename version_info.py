"""Read the single authoritative release version without importing a backend."""
from pathlib import Path
import re

__version__ = Path(__file__).with_name('VERSION').read_text(encoding='ascii').strip()
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', __version__):
    raise ValueError('VERSION must contain one MAJOR.MINOR.PATCH release version.')
