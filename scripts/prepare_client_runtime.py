"""Prepare the private client runtime without shell-dependent Python -c quoting."""
from pathlib import Path
import sys

from bootstrap import prepare_runtime


if __name__ == '__main__':
    prepare_runtime(Path(sys.argv[1]), python_executable=Path(sys.executable))
