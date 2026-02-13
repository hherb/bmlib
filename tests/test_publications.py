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

"""Tests for bmlib.publications — models, schema, and storage."""

from __future__ import annotations

from datetime import datetime

from bmlib.publications.models import (
    DownloadDay,
    FetchResult,
    FullTextSource,
    Publication,
    SyncProgress,
    SyncReport,
)

# ---------------------------------------------------------------------------
# Task 1: Data model tests
# ---------------------------------------------------------------------------


class TestPublication:
    def test_roundtrip(self):
        pub = Publication(
            title="Test Publication",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/test",
            pmid="12345678",
            abstract="An abstract.",
            authors=["Author A", "Author B"],
            journal="Test Journal",
            publication_date="2024-01-15",
            publication_types=["journal-article"],
            keywords=["test", "example"],
            is_open_access=True,
            license="CC-BY-4.0",
        )
        d = pub.to_dict()
        pub2 = Publication.from_dict(d)

        assert pub2.title == "Test Publication"
        assert pub2.doi == "10.1234/test"
        assert pub2.pmid == "12345678"
        assert pub2.abstract == "An abstract."
        assert pub2.authors == ["Author A", "Author B"]
        assert pub2.journal == "Test Journal"
        assert pub2.publication_date == "2024-01-15"
        assert pub2.publication_types == ["journal-article"]
        assert pub2.keywords == ["test", "example"]
        assert pub2.is_open_access is True
        assert pub2.license == "CC-BY-4.0"
        assert pub2.sources == ["pubmed"]
        assert pub2.first_seen_source == "pubmed"
        assert isinstance(pub2.created_at, datetime)
        assert isinstance(pub2.updated_at, datetime)

    def test_defaults(self):
        pub = Publication(
            title="Minimal",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
        )
        assert pub.doi is None
        assert pub.pmid is None
        assert pub.abstract is None
        assert pub.authors == []
        assert pub.journal is None
        assert pub.publication_date is None
        assert pub.publication_types == []
        assert pub.keywords == []
        assert pub.is_open_access is False
        assert pub.license is None
        assert pub.id is None
        assert isinstance(pub.created_at, datetime)
        assert isinstance(pub.updated_at, datetime)


class TestFullTextSource:
    def test_roundtrip(self):
        fts = FullTextSource(
            publication_id=1,
            source="pmc",
            url="https://pmc.example.com/article/123",
            format="xml",
            version="1.0",
        )
        d = fts.to_dict()
        fts2 = FullTextSource.from_dict(d)

        assert fts2.publication_id == 1
        assert fts2.source == "pmc"
        assert fts2.url == "https://pmc.example.com/article/123"
        assert fts2.format == "xml"
        assert fts2.version == "1.0"
        assert isinstance(fts2.created_at, datetime)


class TestDownloadDay:
    def test_roundtrip(self):
        dd = DownloadDay(
            source="pubmed",
            date="2024-06-15",
            status="completed",
            record_count=150,
        )
        d = dd.to_dict()
        dd2 = DownloadDay.from_dict(d)

        assert dd2.source == "pubmed"
        assert dd2.date == "2024-06-15"
        assert dd2.status == "completed"
        assert dd2.record_count == 150
        assert isinstance(dd2.downloaded_at, datetime)


class TestFetchResult:
    def test_basic(self):
        fr = FetchResult(
            source="pubmed",
            date="2024-06-15",
            record_count=100,
            status="ok",
        )
        assert fr.source == "pubmed"
        assert fr.date == "2024-06-15"
        assert fr.record_count == 100
        assert fr.status == "ok"
        assert fr.error is None

    def test_with_error(self):
        fr = FetchResult(
            source="biorxiv",
            date="2024-06-15",
            record_count=0,
            status="error",
            error="Connection timeout",
        )
        assert fr.error == "Connection timeout"


class TestSyncProgress:
    def test_basic(self):
        sp = SyncProgress(
            source="pubmed",
            date="2024-06-15",
            records_processed=50,
            records_total=200,
            status="in_progress",
        )
        assert sp.source == "pubmed"
        assert sp.records_processed == 50
        assert sp.records_total == 200
        assert sp.status == "in_progress"
        assert sp.message is None

    def test_with_message(self):
        sp = SyncProgress(
            source="pubmed",
            date="2024-06-15",
            records_processed=200,
            records_total=200,
            status="completed",
            message="All records synced successfully",
        )
        assert sp.message == "All records synced successfully"


class TestSyncReport:
    def test_basic(self):
        sr = SyncReport(
            sources_synced=["pubmed", "biorxiv"],
            days_processed=30,
            records_added=500,
            records_merged=50,
            records_failed=2,
        )
        assert sr.sources_synced == ["pubmed", "biorxiv"]
        assert sr.days_processed == 30
        assert sr.records_added == 500
        assert sr.records_merged == 50
        assert sr.records_failed == 2
        assert sr.errors == []

    def test_with_errors(self):
        sr = SyncReport(
            sources_synced=["pubmed"],
            days_processed=5,
            records_added=100,
            records_merged=10,
            records_failed=3,
            errors=["Failed to parse record X", "Duplicate DOI Y"],
        )
        assert len(sr.errors) == 2
