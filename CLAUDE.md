# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**bmlib** is a Python library project licensed under AGPL-3.0. The repository is in early setup phase — no source code, packaging, or tests have been added yet.

## Repository State

- No `pyproject.toml`, `setup.py`, or package directory exists yet
- No test infrastructure is configured
- The `.gitignore` is set up for Python development with support for pytest, tox, nox, ruff, mypy, coverage, and multiple packaging tools (poetry, pdm, pipenv, uv, pixi)

## Development Setup (to be established)

When setting up the project, the following are anticipated based on `.gitignore` configuration:
- **Packaging:** pyproject.toml-based (poetry, pdm, uv, or pixi)
- **Testing:** pytest
- **Linting/Formatting:** ruff
- **Type checking:** mypy or pyre
- **Test automation:** tox or nox
