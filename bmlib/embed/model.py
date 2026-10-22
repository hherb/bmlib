# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The embedding model files and a verified download.

Stdlib only. A file is never visible at its final path unless its size and
sha256 matched.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bmlib import __version__

BLOCK_SIZE = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ModelFile:
    """A model file pinned to a repository revision, size and checksum."""

    repo: str
    revision: str
    file: str
    size: int
    sha256: str

    def url(self) -> str:
        """Return the revision-pinned download URL."""
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"


_REPO = "hherb/snowflake-arctic-embed-l-v2.0-gguf"
_REVISION = "c0a1ca8894e853259b9e2aeb1a873f6d50ae5701"

ARCTIC_EMBED_L_V2_F16 = ModelFile(
    _REPO,
    _REVISION,
    "snowflake-arctic-embed-l-v2.0-F16.gguf",
    1157672160,
    "19227acecdaead5b4272216bda90c9025fa25c243f2abffe755cf89c7fc1b719",
)
ARCTIC_EMBED_L_V2_Q8_0 = ModelFile(
    _REPO,
    _REVISION,
    "snowflake-arctic-embed-l-v2.0-Q8_0.gguf",
    634554720,
    "363d89f6c2476bd5dcb010780884ab19ba3ba19b17aed0953a53877524323b88",
)


class ModelVerificationError(Exception):
    """A model file's size or sha256 is not the pinned one."""

    def __init__(self, path: Path, what: str, expected: int | str, actual: int | str) -> None:
        super().__init__(f"{path}: {what} is {actual}, expected {expected}")
        self.path = path
        self.what = what
        self.expected = expected
        self.actual = actual


class DownloadCancelled(Exception):  # noqa: N818 - a cancel, not an error
    """The caller's ``progress`` hook returned ``False``; nothing was kept."""


def _check(path: Path, model: ModelFile, size: int, sha256: str) -> None:
    if size != model.size:
        raise ModelVerificationError(path, "size", model.size, size)
    if sha256 != model.sha256:
        raise ModelVerificationError(path, "sha256", model.sha256, sha256)


def verify_model(path: Path, model: ModelFile) -> None:
    """Check a file's size, then its streamed sha256.

    Raises:
        ModelVerificationError: On a mismatch.
        OSError: If the file cannot be read.
    """
    size = path.stat().st_size
    if size != model.size:
        raise ModelVerificationError(path, "size", model.size, size)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(BLOCK_SIZE):
            digest.update(block)
    _check(path, model, size, digest.hexdigest())


def download_model(
    model: ModelFile,
    directory: Path,
    progress: Callable[[int, int], bool | None] | None = None,
) -> Path:
    """Return the verified model file in ``directory``, downloading it if needed.

    An already-verified file costs no network, but it is re-hashed on every
    call (about 1 s per GB), so call this once per launch. A mismatched one is
    deleted and fetched again. The download goes through ``<file>.part``, which
    is synced to disk before the rename and removed on any failure or cancel.
    Run one call per ``directory`` at a time: concurrent calls share the
    ``.part`` file.

    Args:
        model: The pinned file.
        directory: Where the file lives (created if missing).
        progress: Called as ``progress(done_bytes, model.size)`` per block.
            Returning exactly ``False`` cancels the download; ``None`` or
            ``True`` carries on.

    Returns:
        The final path.

    Raises:
        ModelVerificationError: If the downloaded bytes are not the pinned ones
            (a body shorter or longer than the pinned size included).
        OSError: On a network or file-system failure (``URLError`` included).
        DownloadCancelled: If ``progress`` returned ``False``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / model.file
    if final.exists():
        try:
            verify_model(final, model)
            return final
        except ModelVerificationError:
            final.unlink()
    part = directory / (model.file + ".part")
    request = urllib.request.Request(model.url(), headers={"User-Agent": f"bmlib/{__version__}"})
    try:
        digest = hashlib.sha256()
        done = 0
        with (
            urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response,
            part.open("wb") as out,
        ):
            while block := response.read(BLOCK_SIZE):
                out.write(block)
                digest.update(block)
                done += len(block)
                if done > model.size:  # stop at the first excess block
                    raise ModelVerificationError(
                        part, "size", model.size, f"more than {model.size}"
                    )
                if progress is not None and progress(done, model.size) is False:
                    raise DownloadCancelled(f"download of {model.file} cancelled")
            out.flush()
            os.fsync(out.fileno())
        _check(part, model, done, digest.hexdigest())
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return final
