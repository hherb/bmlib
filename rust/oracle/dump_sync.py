#!/usr/bin/env python3
"""Dump bmlib's sync day-selection rules, for the Rust port.

Time is the input here, not an ambient: every case states the instant it means
and Python's rule is driven by monkeypatching `datetime` inside the module. The
Rust port takes `now` as a parameter, so the corpus carries it explicitly.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from unittest import mock
from datetime import UTC, date, datetime, timedelta

# `bmlib.publications.sync` is re-exported as a *function* from the package, so
# `import bmlib.publications.sync as m` binds the function rather than the
# module -- and `mock.patch.object` then fails with "does not have the attribute
# 'datetime'". Fetching the module from `sys.modules` under its qualified name
# is unambiguous.
import importlib

sync_module = importlib.import_module("bmlib.publications.sync")
from bmlib.publications.models import FetchResult
from bmlib.publications.sync import (
    _day_was_over_when_fetched,
    _note_unreachable_days,
    _read_aware_timestamp,
    _read_verification_date,
    _resolve_day_status,
    _validate_window,
)


class _FrozenDateTime(datetime):
    """`datetime` whose `now()` is the corpus's instant.

    Subclassing rather than a stub keeps `fromisoformat` and `combine` — which
    the module also calls — as the real implementations, so only *now* is under
    the corpus's control. The Python suite monkeypatches the same names.
    """

    _now: datetime = datetime(2024, 1, 2, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls._now if tz is None else cls._now.astimezone(tz)


class _FrozenDate(date):
    """`date` whose `today()` is the corpus's instant."""

    _today: date = date(2024, 1, 2)

    @classmethod
    def today(cls):
        return cls._today


class frozen:
    """Freeze the module's clock for the duration of a case."""

    def __init__(self, now_iso: str, *, patch_datetime: bool = True):
        now = datetime.fromisoformat(now_iso)
        self._now = now
        self._patches = [mock.patch.object(sync_module, "date", _FrozenDate)]
        if patch_datetime:
            # `_require_plain_date` names `datetime` in an `isinstance` check, so
            # replacing that name replaces the class it tests against — and a
            # plain `date` then fails a test written to *accept* it. Only the
            # rules that read the wall clock need this patched.
            self._patches.append(
                mock.patch.object(sync_module, "datetime", _FrozenDateTime)
            )

    def __enter__(self):
        _FrozenDateTime._now = self._now
        _FrozenDate._today = self._now.date()
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        for p in reversed(self._patches):
            p.stop()
        return False


def day_was_over(now_iso, day_iso, downloaded_at):
    with frozen(now_iso):
        return _day_was_over_when_fetched("s", date.fromisoformat(day_iso), downloaded_at)


def note_unreachable(now_iso, date_to_iso):
    with frozen(now_iso):
        return _note_unreachable_days(date.fromisoformat(date_to_iso))


def days_needing(now_iso, rows, date_from_iso, date_to_iso, recheck_days):
    """Drive `_days_needing_fetch` with a stubbed row source."""
    with frozen(now_iso), mock.patch.object(sync_module, "fetch_all", lambda *a, **kw: list(rows)):
        return [
            d.isoformat()
            for d in sync_module._days_needing_fetch(
                None, "s",
                date_from=date.fromisoformat(date_from_iso),
                date_to=date.fromisoformat(date_to_iso),
                recheck_days=recheck_days,
            )
        ]


def validate_window(now_iso, date_to_iso, recheck_days):
    """Run `_validate_window` with the corpus's instant as *today*.

    **No patching at all**, and that is the point. `_require_plain_date` is an
    `isinstance` test against the classes the module was *imported* with, so
    swapping `sync_module.datetime` or `sync_module.date` makes a genuine
    `date` fail a check written to accept it — "date_from must be a
    datetime.date, got date". The module-level dates are what the validator
    compares against. What the corpus controls here is `today`, which
    `_validate_window` reads only to bound `recheck_days` — so the case passes
    a `recheck_days` relative to the corpus instant, and the validator's own
    `date.today()` is left alone. The `now_iso` parameter is consequently
    unused and kept for the corpus's uniform shape.
    """
    import datetime as _dt

    _ = now_iso
    try:
        _validate_window(
            _dt.date.fromisoformat("2024-01-01"),
            _dt.date.fromisoformat(date_to_iso),
            recheck_days,
        )
        return {"ok": True}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def resolve_status(now_iso, status, day_failed, note=None):
    with frozen(now_iso):
        outcome = _resolve_day_status(
            "s", date.fromisoformat("2024-01-02"),
            FetchResult(source="s", date="2024-01-02", record_count=0, status=status, note=note),
            day_failed,
        )
        return {"status": outcome.status, "errors": outcome.errors, "notes": outcome.notes}


def run(case):
    fn = case["fn"]
    a = case.get("args", {})
    if fn == "day_was_over":
        return day_was_over(a["now"], a["day"], a.get("downloaded_at"))
    if fn == "read_aware":
        v = _read_aware_timestamp(a.get("value"))
        return v.isoformat() if v else None
    if fn == "read_verification":
        v = _read_verification_date("s", date.fromisoformat("2024-01-02"), a.get("value"))
        return v.isoformat() if v else None
    if fn == "note_unreachable":
        return note_unreachable(a["now"], a["date_to"])
    if fn == "days_needing":
        return days_needing(a["now"], a["rows"], a["date_from"], a["date_to"], a["recheck_days"])
    if fn == "validate_window":
        return validate_window(a["now"], a["date_to"], a["recheck_days"])
    if fn == "resolve_status":
        return resolve_status(a["now"], a["status"], a["day_failed"], a.get("note"))
    raise ValueError(f"unknown fn {fn!r}")


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
