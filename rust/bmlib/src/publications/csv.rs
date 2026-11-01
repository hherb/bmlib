// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! A CSV reader that reports the physical line each record ended on.
//!
//! Python's `csv.DictReader.line_num` is "the number of physical lines read
//! from the source iterator", and the Retraction Watch importer depends on it:
//! a quoted field containing a newline makes one CSV *record* span two physical
//! lines, and a caller reporting "row 5 is unusable" must mean what an editor
//! shows. `enumerate()` over the records would under-report every row after the
//! first multi-line one.
//!
//! The parsing is the [`csv`] crate's — a general, solved problem, and exactly
//! what the dependency policy reaches for. What is ours is the **line count**:
//! `ByteRecord::position()` reports a raw byte offset into the input, and
//! counting `\n` bytes before that offset is the physical line count Python
//! reports — over the raw bytes, so a line ending inside a quoted field is
//! counted, which is the whole point.
//!
//! Counting bytes rather than using `String::lines()` also keeps this correct
//! for a file the encoding scan has not yet decoded: RFC 4180 quoting is ASCII,
//! so delimiters, quotes and line endings are unambiguous in raw bytes and a
//! non-UTF-8 byte inside a field never has to be interpreted.

/// One CSV record, with the physical line it ended on.
pub type Record = (Vec<(String, String)>, usize);

/// Why a CSV document could not be read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CsvError {
    /// The document had no header row.
    NoHeader,
    /// The document itself is malformed.
    Malformed(String),
}

impl std::fmt::Display for CsvError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CsvError::NoHeader => write!(f, "the CSV has no header row"),
            CsvError::Malformed(why) => write!(f, "{why}"),
        }
    }
}

impl std::error::Error for CsvError {}

/// Count the `\n` bytes in `raw[from..to]`, clamped to `raw`.
fn newlines_between(raw: &[u8], from: usize, to: usize) -> usize {
    let to = to.min(raw.len());
    raw[from.min(to)..to]
        .iter()
        .filter(|b| **b == b'\n')
        .count()
}

/// A CSV document with a header, yielding one map per record.
pub struct Reader {
    inner: csv::Reader<std::io::Cursor<Vec<u8>>>,
    header: Vec<String>,
    raw: Vec<u8>,
    /// `\n` bytes in `raw[..counted_to]`: records end in file order, so each
    /// count starts where the last stopped (from the start, 73k records of a
    /// 68 MB export took over ten minutes).
    newlines: usize,
    counted_to: usize,
}

impl std::fmt::Debug for Reader {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Reader")
            .field("header", &self.header)
            .finish_non_exhaustive()
    }
}

impl Reader {
    /// Parse a CSV document.
    ///
    /// # Errors
    ///
    /// [`CsvError`] if the document has no header or the crate rejects it.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, CsvError> {
        let raw = bytes.to_vec();
        let mut inner = csv::ReaderBuilder::new()
            .has_headers(true)
            // Python's `csv` is not strict: a field with an unescaped quote in
            // the middle of an unquoted cell is data. The crate's default would
            // reject the row instead, losing notices a `DictReader` accepts.
            .flexible(true)
            .from_reader(std::io::Cursor::new(raw.clone()));
        let header: Vec<String> = inner
            .headers()
            .map_err(|e| CsvError::Malformed(e.to_string()))?
            .iter()
            .map(str::to_string)
            .collect();
        if header.is_empty() {
            return Err(CsvError::NoHeader);
        }
        Ok(Reader {
            inner,
            header,
            raw,
            newlines: 0,
            counted_to: 0,
        })
    }

    /// The header column names, in order.
    #[must_use]
    pub fn header(&self) -> &[String] {
        &self.header
    }

    /// The next record, as `(fields, physical line it ended on)`.
    ///
    /// # Errors
    ///
    /// [`CsvError::Malformed`] when the record is shorter than the header;
    /// [`CsvError::Malformed`] when the crate rejects the input.
    pub fn next_record(&mut self) -> Option<Result<Record, CsvError>> {
        let mut record = csv::ByteRecord::new();
        match self.inner.read_byte_record(&mut record) {
            Ok(false) => None,
            Err(e) => Some(Err(CsvError::Malformed(e.to_string()))),
            Ok(true) => {
                // The reader's position is now just past this record's
                // terminator, so the `\n`s before it are the lines read whole
                // — Python's `line_num` — plus the record's own last line when
                // the file ends without a newline. (The record's field bytes
                // carry no delimiters, quotes or terminator, so counting to
                // `start + len` missed a newline late in the record.)
                let end = usize::try_from(self.inner.position().byte()).unwrap_or(usize::MAX);
                self.newlines += newlines_between(&self.raw, self.counted_to, end);
                self.counted_to = self.counted_to.max(end.min(self.raw.len()));
                let unterminated = self.counted_to > 0 && self.raw[self.counted_to - 1] != b'\n';
                let line = self.newlines + usize::from(unterminated);

                // A record **shorter** than its header is: it is what a
                // download cut part-way through a row leaves, and read with
                // its missing fields as `""` its cut DOI and missing nature
                // replaced a good notice with one no lookup finds. 0 of the
                // 69,318 rows of the 2026-03 export and 0 of the 73,329 of
                // 2026-10-21 are short. Python's reader refuses it alike.
                if record.len() < self.header.len() {
                    return Some(Err(CsvError::Malformed(format!(
                        "the CSV row ending on line {line} has fewer fields than the header; \
                         the download is probably truncated -- fetch it again."
                    ))));
                }

                // A record **wider** than its header is not an error: Python's
                // `DictReader` keeps the extras under a `None` key, which the
                // column resolution cannot find, so such a row reaches the row
                // rules with only its named columns — and is then skipped for
                // having no usable identifier, which is the same answer. A
                // first cut raised here and diverged on a two-column fixture
                // whose rows carried three fields; the oracle caught it.
                let fields: Vec<(String, String)> = self
                    .header
                    .iter()
                    .enumerate()
                    .map(|(index, name)| {
                        let value = record
                            .get(index)
                            .map(|b| String::from_utf8_lossy(b).into_owned())
                            .unwrap_or_default();
                        (name.clone(), value)
                    })
                    .collect();
                Some(Ok((fields, line)))
            }
        }
    }
}

impl Iterator for Reader {
    type Item = Result<Record, CsvError>;

    fn next(&mut self) -> Option<Self::Item> {
        self.next_record()
    }
}
