"""Content-addressed static asset publication."""

from .builder import build
from .models import BuildError, BuildResult


__all__ = ['BuildError', 'BuildResult', 'build']
