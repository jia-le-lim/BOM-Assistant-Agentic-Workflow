"""NYRA agent: a bounded tool-calling loop over read-only data plus one
staging write. Deliberately not a graph framework -- see the loop docstring.
"""

from .loop import run_agent

__all__ = ["run_agent"]
