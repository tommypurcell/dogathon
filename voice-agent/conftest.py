"""Make the project root importable from tests/ (so `import tools`, `local_stt`, etc. work)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
