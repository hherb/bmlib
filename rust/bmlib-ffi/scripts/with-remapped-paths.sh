#!/usr/bin/env bash
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

# Run a command (a cargo build) with every builder-specific path that rustc
# would compile into the library remapped to a fixed one (#490): the bmlib
# checkout, CARGO_HOME (crate sources) and the toolchain's sysroot (std's
# sources, when rust-src is installed). Without it every slice carries the
# builder's home directory in its panic locations, so two checkouts never
# build the same bytes and a CI rebuild cannot be compared with a committed
# artifact.
#
#   rust/bmlib-ffi/scripts/with-remapped-paths.sh cargo build -p bmlib-ffi ...
#
# CARGO_ENCODED_RUSTFLAGS (0x1f-separated) survives paths with spaces. Flags
# the caller already set there are kept after these (build-xcframework.sh
# passes its install name that way); RUSTFLAGS in the environment is
# dropped, since cargo would ignore it beside CARGO_ENCODED_RUSTFLAGS anyway.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: with-remapped-paths.sh <command> [args...]" >&2; exit 2; }

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)    # the bmlib checkout
SYSROOT=$(rustc --print sysroot)
SEP=$'\x1f'
export CARGO_ENCODED_RUSTFLAGS="--remap-path-prefix=$ROOT=/bmlib$SEP--remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo$SEP--remap-path-prefix=$SYSROOT=/rustc-sysroot${CARGO_ENCODED_RUSTFLAGS:+$SEP$CARGO_ENCODED_RUSTFLAGS}"
unset RUSTFLAGS
exec "$@"
