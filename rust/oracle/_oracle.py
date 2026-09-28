"""Shared helpers for the differential-oracle dumpers.

**Private to this directory.** `scripts/rerun_rust_oracle.py` runs each dumper as
a script (`[sys.executable, str(ORACLE / dumper)]`), so the script's own
directory is `sys.path[0]` and ``import _oracle`` resolves here. It is not named
``dump_*`` because it generates no corpus, and that script's
``unlisted_dumpers()`` globs ``dump_*.py`` only.

One home because the copies had already drifted once: `split_response` was
written into both the bioRxiv and the OpenAlex dumper for #349, and the
transport marker would have made a third. What belongs here is the **corpus's
response vocabulary**, which is a property of the instrument rather than of any
one source (#361).
"""

from __future__ import annotations


def named_exception(named: str) -> Exception:
    """An exception whose ``type(exc).__name__`` is the name ``named`` carries.

    ``named`` is Python's ``f"{type(exc).__name__}: {exc}"`` — the shape every
    bmlib handler stores — so ``"ConnectError: connection refused"`` builds a
    class *named* ``ConnectError`` carrying ``connection refused``.

    Built rather than imported because the real class is httpx's and the corpus is
    scripting a transport; what has to be faithful is the **name**, which is the
    half the port cannot reproduce (it has one ``FetchError::Transport`` for
    httpx's four subclasses, and names the base). An input with no ``": "`` keeps
    its whole text as the message under the base name, matching
    ``type(exc).__name__`` for a bare ``Exception``.
    """
    name, separator, message = named.partition(": ")
    if not separator:
        return Exception(named)
    return type(name, (Exception,), {})(message)


def response_marker(payload):
    """Read one word of the corpus's response vocabulary.

    Three shapes, and the two markers are objects because **JSON has no tuples**:

    * a bare value — the body, HTTP 200;
    * ``{"http_status": N, "body": B}`` — the source answered with a non-success
      status (``raise_for_status`` raises);
    * ``{"transport_error": {"name": N, "message": M}}`` — the request never
      arrived, so there is no response at all and the ``get`` itself raises. The
      name is httpx's subclass (``ConnectError``, ``ReadTimeout``, ``ReadError``);
      the port names the base class, which is the §9 divergence the corpus's
      ``corrected`` block records (#361).

    Returns ``None`` for a bare body, ``("http_status", body, status)`` or
    ``("transport_error", name, message)`` otherwise. Every shape is checked and
    **fails closed**: a marker with the wrong keys is a corpus error, not a body,
    because reading it as a body is what made ``fetch/http-error`` vacuous for a
    release (#349).
    """
    if not isinstance(payload, dict):
        return None

    if set(payload) == {"transport_error"}:
        marker = payload["transport_error"]
        if not isinstance(marker, dict) or set(marker) != {"name", "message"}:
            raise ValueError(f"transport_error must carry exactly name and message: {marker!r}")
        name, message = marker["name"], marker["message"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"transport_error.name must be a non-empty string: {name!r}")
        if not isinstance(message, str):
            raise ValueError(f"transport_error.message must be a string: {message!r}")
        return ("transport_error", name, message)

    if "http_status" in payload and set(payload) <= {"http_status", "body"}:
        status = payload["http_status"]
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError(f"http_status must be an integer, got {status!r}")
        return ("http_status", payload.get("body"), status)

    return None
