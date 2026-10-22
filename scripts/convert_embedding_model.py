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
"""Reproducible GGUF conversion of arctic-embed-l-v2.0, with verification.

    python scripts/convert_embedding_model.py --llama-cpp <checkout> \
        --base-dir <HF cache>/snapshots/<BASE_REVISION> --out <dir> \
        --verify-reference tests/fixtures/embedding_reference.json

Refuses any llama.cpp checkout other than the pinned commit, and a base
snapshot directory not named for the pinned revision (the revision is stamped
into the manifest and model card). Writes ``manifest.json`` and ``README.md``
only once both files have passed verification. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

LLAMA_CPP_COMMIT = "26394b4e6749a41c3633db040e0987500a5f7013"
BASE_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"
BASE_REVISION = "ac6544c8a46e00af67e330e85a9028c66b8cfd9a"
STEM = "snowflake-arctic-embed-l-v2.0"
DIM = 256
# llama-embedding splits -p on this (default "\n"); it must not occur in any text.
SEPARATOR = "<|bmlib-sep|>"
BLOCK = 1024 * 1024
# Minimum cosine against the fp32 reference, by file suffix.
TOLERANCE = {"F16": 0.999, "Q8_0": 0.998}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(BLOCK):
            h.update(block)
    return h.hexdigest()


def manifest(files: list[Path], llama_cpp_commit: str, base_revision: str) -> dict:
    return {
        "base_model": BASE_MODEL,
        "base_revision": base_revision,
        "llama_cpp_commit": llama_cpp_commit,
        "files": [
            {"file": p.name, "size": p.stat().st_size, "sha256": sha256_file(p)} for p in files
        ],
    }


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def parse_embedding(output: str, dim: int = DIM) -> list[float]:
    """First ``dim`` values of llama-embedding's JSON, L2-renormalised; ValueError if odd."""
    try:
        data = json.loads(output)["data"]
        vec = [float(x) for x in data[0]["embedding"]] if len(data) == 1 else []
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ValueError(f"unrecognised llama-embedding output: {output[:200]!r}") from e
    if len(data) != 1:
        raise ValueError(f"expected 1 embedding, got {len(data)}")
    if len(vec) < dim:
        raise ValueError(f"embedding has {len(vec)} dims, need at least {dim}")
    vec = vec[:dim]
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        raise ValueError("zero-norm embedding")
    return [x / norm for x in vec]


def verify(llama_cpp: Path, gguf: Path, reference: dict) -> float:
    """Minimum cosine over all reference items; SystemExit if under the file's tolerance."""
    floor = TOLERANCE[gguf.stem.rsplit("-", 1)[1]]
    lowest = 1.0
    for item in reference["items"]:
        text = ("query: " if item["kind"] == "query" else "") + item["text"]
        if SEPARATOR in text:
            raise ValueError(f"reference text contains the separator: {item['name']}")
        cmd = [
            str(llama_cpp / "build/bin/llama-embedding"), "-m", str(gguf),
            "--pooling", "cls", "--embd-normalize", "-1",
            "--embd-output-format", "json", "--embd-separator", SEPARATOR, "-p", text,
        ]  # fmt: skip
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
        lowest = min(lowest, cosine(parse_embedding(out), item["vector"]))
    if lowest < floor:
        sys.exit(f"{gguf.name}: min cosine {lowest:.5f} < {floor}")
    return lowest


def model_card(m: dict) -> str:
    rows = "\n".join(f"| {f['file']} | {f['size']} | `{f['sha256']}` |" for f in m["files"])
    results = "\n".join(f"- {f['file']}: {f['min_cosine']:.5f}" for f in m["files"])
    c = m["llama_cpp_commit"]
    return f"""---
license: apache-2.0
base_model: {m["base_model"]}
base_model_relation: quantized
library_name: llama.cpp
tags: [gguf, sentence-similarity, feature-extraction]
pipeline_tag: sentence-similarity
---

# {STEM} (GGUF)

GGUF conversions of [{m["base_model"]}](https://huggingface.co/{m["base_model"]}),
the multilingual embedding model by Snowflake, at revision `{m["base_revision"]}`.
All credit for the model belongs to Snowflake; only the file format differs.

## Files

| File | Size (bytes) | SHA-256 |
|---|---|---|
{rows}

## How it was made

llama.cpp commit `{c}`:

```
python convert_hf_to_gguf.py <base-dir> --outtype f16 --outfile {STEM}-F16.gguf
build/bin/llama-quantize {STEM}-F16.gguf {STEM}-Q8_0.gguf Q8_0
```

Produced by `scripts/convert_embedding_model.py` in bmlib.

## Usage

- CLS pooling.
- Prefix queries with `"query: "`; documents take no prefix.
- Keep the first 256 dimensions and L2-renormalise (Matryoshka truncation).
- Context: 8192 tokens.

## Verification

Minimum cosine similarity against the fp32 reference embeddings of the original model
(256 dimensions, renormalised):

{results}

## Licence

Apache-2.0, unchanged from the base model.
"""


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--llama-cpp", type=Path, required=True)
    ap.add_argument("--base-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--verify-reference", type=Path, required=True)
    args = ap.parse_args(argv)

    head = subprocess.run(
        ["git", "-C", str(args.llama_cpp), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    if head != LLAMA_CPP_COMMIT:
        sys.exit(f"llama.cpp is at {head}, need {LLAMA_CPP_COMMIT}")
    # An HF cache snapshot directory is named for its commit.
    if args.base_dir.resolve().name != BASE_REVISION:
        sys.exit(f"--base-dir must be the snapshot of {BASE_MODEL} at {BASE_REVISION}")

    args.out.mkdir(parents=True, exist_ok=True)
    for stale in ("manifest.json", "README.md"):  # never left beside unverified files
        (args.out / stale).unlink(missing_ok=True)
    f16 = args.out / f"{STEM}-F16.gguf"
    q8 = args.out / f"{STEM}-Q8_0.gguf"
    _run([sys.executable, str(args.llama_cpp / "convert_hf_to_gguf.py"), str(args.base_dir),
          "--outtype", "f16", "--outfile", str(f16)])  # fmt: skip
    _run([str(args.llama_cpp / "build/bin/llama-quantize"), str(f16), str(q8), "Q8_0"])

    m = manifest([f16, q8], head, BASE_REVISION)
    reference = json.loads(args.verify_reference.read_text())
    for entry, path in zip(m["files"], (f16, q8), strict=True):
        entry["min_cosine"] = verify(args.llama_cpp, path, reference)
    (args.out / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    (args.out / "README.md").write_text(model_card(m))


if __name__ == "__main__":
    main()
