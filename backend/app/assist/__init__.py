"""Deterministic review assistance for the active/dying rows.

The verdict is decided by rules.py and the LLM only explains it. Splitting the
two is what makes the layer replayable: same evidence in, same verdict out,
backtestable against eight cycles of real decisions.
"""
