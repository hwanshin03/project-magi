"""Run with python -B -m beta_preview; intentionally no public bind option."""
from .safety import install

install()

from .app import serve

serve()
