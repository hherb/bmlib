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

"""medRxiv / bioRxiv MECA bulk archives: S3 requests signed with SigV4.

The archives are requester-pays S3 buckets, so the caller's own AWS key pays
for what it reads. :mod:`~bmlib.fulltext.meca.sigv4` signs requests and
:mod:`~bmlib.fulltext.meca.s3` lists a prefix and downloads one object. Both
are importable with core bmlib; only :func:`~bmlib.fulltext.meca.s3.list_objects`
and :func:`~bmlib.fulltext.meca.s3.download_object` need the client the
``bmlib[meca]`` extra installs (``httpx``). Reading a ``.meca`` package itself
is not here yet.
"""

from bmlib.fulltext.meca.s3 import (
    BIORXIV_MECA_BUCKET,
    MECA_BUCKET_REGION,
    MEDRXIV_MECA_BUCKET,
    ListPage,
    S3Error,
    S3ErrorKind,
    S3Object,
    classify_error,
    download_object,
    list_objects,
    parse_list_objects_v2,
)
from bmlib.fulltext.meca.sigv4 import S3Credentials, SignedRequest, sign_get

__all__ = [
    "BIORXIV_MECA_BUCKET",
    "MECA_BUCKET_REGION",
    "MEDRXIV_MECA_BUCKET",
    "ListPage",
    "S3Credentials",
    "S3Error",
    "S3ErrorKind",
    "S3Object",
    "SignedRequest",
    "classify_error",
    "download_object",
    "list_objects",
    "parse_list_objects_v2",
    "sign_get",
]
