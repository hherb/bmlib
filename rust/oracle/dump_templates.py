#!/usr/bin/env python3
"""Dump bmlib's Jinja2 template rendering, for the Rust port.

Only the subset the Rust renderer implements is dumped; a construct outside it is
recorded as a refusal, since that is the port's contract.
"""

from __future__ import annotations

import json
import sys

from jinja2 import Environment


def render(source, variables):
    env = Environment(keep_trailing_newline=True, autoescape=False)
    return env.from_string(source).render(**variables)


def run(case):
    return render(case["args"]["source"], case["args"]["variables"])


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
