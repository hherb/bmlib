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

//! The real PDF backend, over MuPDF.
//!
//! Python extracts with PyMuPDF, which binds MuPDF; this binds the same engine
//! through the `mupdf` crate, so a PDF gives the same lines in both languages
//! (#590). The engine is a C library compiled from source and linked
//! statically, behind the `pdf` feature.
//!
//! # Reading a line as `pdf_converter.py` does
//!
//! `PyMuPDFConverter.extract_blocks` reads `page.get_text("dict")`, whose
//! **spans** are runs of characters sharing one style, and
//! [`line_to_block`](crate::fulltext::pdf_text::line_to_block)'s rule takes the
//! font from the **dominant** span (most non-whitespace characters, ties to the
//! first). The crate exposes characters, not spans, so [`line_spans`] rebuilds
//! them with PyMuPDF's own grouping (`JM_make_spanlist`): a span ends where the
//! size, the font flags, the character flags less `SYNTHETIC`, the colour or the
//! font name changes. The pieces it takes from PyMuPDF:
//!
//! * **The flags**: [`DICT_FLAGS`] is PyMuPDF's `TEXTFLAGS_DICT` and
//!   [`TEXT_FLAGS`] its `TEXTFLAGS_TEXT` (199 and 195). Both clip to the clip
//!   path within the mediabox, which is what drops a bioRxiv margin line set
//!   outside the page.
//! * **The whole page, into one device**: `fz_run_page` (contents, then
//!   annotations and widgets) into a text page bounded by the page, as
//!   `_get_textpage` builds it. The crate's `Page::to_text_page` runs the
//!   contents alone, and a figure label can be a `FreeText` annotation (three,
//!   in one corpus PDF); a second device over the same page would close it
//!   twice, and every close reverses a right-to-left line's characters again.
//! * **Characters off the page are dropped**: one whose box does not overlap
//!   the page, as `JM_make_spanlist` and `JM_print_stext_page_as_text` drop it
//!   (which drops a glyph with an empty box too), and a line's box is the union
//!   of the characters kept.
//! * **Bold and italic** are MuPDF's `fz_font_is_bold`/`fz_font_is_italic`, the
//!   span's `flags` bits 4 and 1, not a guess from the font's name.
//! * **The font name** is MuPDF's bytes read as Latin-1, which is what PyMuPDF's
//!   `raw-unicode-escape` decoding gives for a name with no backslash: a name in
//!   another encoding (EUC-KR, in one corpus PDF) arrives as Latin-1 characters
//!   rather than as an error. It loses a six-letter subset tag
//!   (`ABCDEF+Helvetica`), as `JM_font_name` does.
//! * **The superscript bit** (`detect_super_script`) splits a span as any other
//!   flag does: on a line written left to right, a character whose baseline sits
//!   above the line's first character's by more than a tenth of its size.
//! * **Page text** ([`MupdfExtractor::page_texts`]) is written as
//!   `JM_print_stext_page_as_text` writes `get_text()`: each line's kept
//!   characters, then a newline unless the line ended in one — not MuPDF's own
//!   text writer, which adds a blank line after every block and can emit bytes
//!   that are not UTF-8 (a glyph with no Unicode is stored as its CID, and CID 0
//!   is written `C0 80`).
//!
//! What is not replicated, the crate exposing neither the field nor a way to
//! reach it: the bidirectional level, one of `JM_make_spanlist`'s split keys
//! (so a right-to-left run inside a line can share a span PyMuPDF splits); the
//! line's `dir`, for which the first character's quad stands in (left to right
//! where its top edge is level); and `JM_char_quad`'s recomputation of a
//! character's box from the font's ascender and descender, which reads `dir`
//! too. That last is PyMuPDF's own and moves between its releases: over #590's
//! corpus, PyMuPDF 1.28.5's line boxes agree with MuPDF's raw quads in 132 of
//! 151 PDFs, and 1.27.2's differ on about 50,000 lines. PyMuPDF also sets a
//! rotated page's rotation to 0 before extracting; this reads the page as MuPDF
//! lays it out.

use crate::fulltext::models::TextBlock;
use crate::fulltext::pdf_text::{normalize_line, PdfBlocks, PdfError, PdfLimit, PdfTextExtractor};
use mupdf::text_page::{TextCharFlags, TextLine, TextPage, TextPageFlags};
use mupdf::{Cookie, Device, DisplayList, Document, Matrix, Page, Quad, Rect};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};

/// How often the watcher looks at a [`PdfLimit`] while a page runs. A cancel or
/// a deadline is overrun by up to this, plus whatever MuPDF does between two
/// looks at the cookie (loading a page and building its text page do not look).
pub const LIMIT_WATCH_INTERVAL: std::time::Duration = std::time::Duration::from_millis(10);

/// PyMuPDF's `TEXTFLAGS_DICT` (199), the flags `get_text("dict")` reads with.
pub const DICT_FLAGS: TextPageFlags = TextPageFlags::PRESERVE_LIGATURES
    .union(TextPageFlags::PRESERVE_WHITESPACE)
    .union(TextPageFlags::PRESERVE_IMAGES)
    .union(TextPageFlags::CLIP)
    .union(TextPageFlags::USE_CID_FOR_UNKNOWN_UNICODE);

/// PyMuPDF's `TEXTFLAGS_TEXT` (195), the flags `get_text()` reads with.
pub const TEXT_FLAGS: TextPageFlags = TextPageFlags::PRESERVE_LIGATURES
    .union(TextPageFlags::PRESERVE_WHITESPACE)
    .union(TextPageFlags::CLIP)
    .union(TextPageFlags::USE_CID_FOR_UNKNOWN_UNICODE);

/// PyMuPDF's `TEXT_FONT_SUPERSCRIPT` span-flag bit.
const SUPERSCRIPT_FLAG: i32 = 1;

/// One span of a line: characters sharing one style, as PyMuPDF groups them.
#[derive(Debug, Clone, PartialEq)]
pub struct Span {
    /// The span's text.
    pub text: String,
    /// The font size.
    pub size: f64,
    /// The font name, less a subset tag.
    pub font: String,
    /// PyMuPDF's span `flags`: superscript 1, italic 2, serif 4, monospaced 8,
    /// bold 16.
    pub flags: i32,
}

/// The style key `JM_make_spanlist` splits a span on.
#[derive(Debug, Clone, PartialEq)]
struct Style {
    size: f32,
    flags: i32,
    char_flags: u16,
    argb: u32,
    font: String,
}

/// A font name as PyMuPDF reports it, from MuPDF's bytes: a six-letter subset
/// tag and its `+` removed (`ABCDEF+Helvetica`), and every byte read as one
/// Latin-1 character (see the module docs). The tag is found in the bytes, as
/// `JM_font_name` finds it.
#[must_use]
pub fn pymupdf_font_name(bytes: &[u8]) -> String {
    let name = match bytes.iter().position(|&byte| byte == b'+') {
        Some(6) => &bytes[7..],
        _ => bytes,
    };
    name.iter().map(|&byte| char::from(byte)).collect()
}

/// A font's name as [`pymupdf_font_name`] reports it.
///
/// Read from `fz_font.name` directly, because the crate's `Font::name` unwraps a
/// UTF-8 decode and so panics on a name in any other encoding.
fn font_name(font: &mupdf::Font) -> String {
    use mupdf_sys::fz_font;
    // `Font` is one `*mut fz_font` (`mupdf` 0.8.0, pinned exactly with
    // `mupdf-sys`); a crate that added a field would fail this assertion.
    const _: () =
        assert!(std::mem::size_of::<mupdf::Font>() == std::mem::size_of::<*mut fz_font>());
    // SAFETY: the copy is the pointer `font` holds, valid while `font` is
    // borrowed; it is only read, and the copy is not dropped (`*mut` has no
    // destructor), so the font's reference count is untouched. `name` is a
    // NUL-terminated array MuPDF writes with `fz_strlcpy`.
    let name = unsafe {
        let raw: *mut fz_font = std::mem::transmute_copy(font);
        &(*raw).name
    };
    let bytes: Vec<u8> = name
        .iter()
        .take_while(|&&byte| byte != 0)
        .map(|&byte| byte as u8)
        .collect();
    pymupdf_font_name(&bytes)
}

/// The smallest rectangle holding a quad.
fn quad_rect(quad: &Quad) -> Rect {
    let xs = [quad.ul.x, quad.ur.x, quad.ll.x, quad.lr.x];
    let ys = [quad.ul.y, quad.ur.y, quad.ll.y, quad.lr.y];
    let min = |v: [f32; 4]| v.into_iter().fold(f32::INFINITY, f32::min);
    let max = |v: [f32; 4]| v.into_iter().fold(f32::NEG_INFINITY, f32::max);
    Rect::new(min(xs), min(ys), max(xs), max(ys))
}

/// Whether two rectangles share area, as `JM_rects_overlap` decides: an empty
/// rectangle overlaps nothing.
fn overlaps(a: &Rect, b: &Rect) -> bool {
    !(a.x0 >= b.x1 || a.y0 >= b.y1 || a.x1 <= b.x0 || a.y1 <= b.y0)
}

/// A line's spans, grouped as PyMuPDF's `get_text("dict")` groups them, and the
/// rectangle round the characters kept.
///
/// A character whose box does not overlap `page` is dropped, as
/// `JM_make_spanlist` drops it — which is also what drops a glyph with an empty
/// box. The line's rectangle is the union of the kept characters' boxes, which
/// is how PyMuPDF builds a line's `bbox`; `None` when none was kept.
#[must_use]
pub fn line_spans(line: &TextLine<'_>, page: &Rect) -> (Vec<Span>, Option<Rect>) {
    let mut spans: Vec<Span> = Vec::new();
    let mut bounds: Option<Rect> = None;
    let mut current: Option<Style> = None;
    let mut first: Option<(f32, bool)> = None;
    for ch in line.chars() {
        let quad = ch.quad();
        // The line's direction and baseline, taken from its first character
        // whether or not it is kept, as `detect_super_script` reads
        // `line.first_char`: left to right where its top edge is level.
        let (first_y, horizontal) = *first.get_or_insert((
            ch.origin().y,
            quad.ul.y == quad.ur.y && quad.ur.x > quad.ul.x,
        ));
        let rect = quad_rect(&quad);
        if !overlaps(page, &rect) {
            continue;
        }
        bounds = Some(match bounds {
            Some(b) => Rect::new(
                b.x0.min(rect.x0),
                b.y0.min(rect.y0),
                b.x1.max(rect.x1),
                b.y1.max(rect.y1),
            ),
            None => rect,
        });
        let size = ch.size();
        let superscript = horizontal && ch.origin().y < first_y - size * 0.1;
        let (font, font_flags) = match ch.font() {
            Some(font) => {
                let flags = i32::from(font.is_italic()) * 2
                    + i32::from(font.is_serif()) * 4
                    + i32::from(font.is_monospaced()) * 8
                    + i32::from(font.is_bold()) * 16;
                (font_name(&font), flags)
            }
            None => (String::new(), 0),
        };
        let style = Style {
            size,
            flags: font_flags + if superscript { SUPERSCRIPT_FLAG } else { 0 },
            char_flags: (ch.flags() - TextCharFlags::SYNTHETIC).bits(),
            argb: ch.argb(),
            font,
        };
        if current.as_ref() != Some(&style) {
            spans.push(Span {
                text: String::new(),
                size: f64::from(style.size),
                font: style.font.clone(),
                flags: style.flags,
            });
            current = Some(style);
        }
        if let Some(span) = spans.last_mut() {
            span.text
                .push(ch.char().unwrap_or(char::REPLACEMENT_CHARACTER));
        }
    }
    (spans, bounds)
}

/// Collapse one line into a [`TextBlock`], by `line_to_block`'s rules.
///
/// Returns `None` for a line with no non-whitespace text.
#[must_use]
pub fn line_block(line: &TextLine<'_>, page: &Rect, page_num: i64) -> Option<TextBlock> {
    let (spans, bounds) = line_spans(line, page);
    let bounds = bounds?;
    let joined: String = spans.iter().map(|s| s.text.as_str()).collect();
    let text = normalize_line(&joined);
    if text.is_empty() {
        return None;
    }
    let weight = |span: &Span| span.text.chars().filter(|c| !c.is_whitespace()).count();
    // Strictly greater, so a tie keeps the earlier span.
    let dominant = spans.iter().fold(None::<&Span>, |best, span| match best {
        Some(previous) if weight(span) <= weight(previous) => Some(previous),
        _ => Some(span),
    })?;
    Some(TextBlock {
        text,
        page_num,
        font_size: dominant.size,
        font_name: dominant.font.clone(),
        is_bold: dominant.flags & 16 != 0,
        is_italic: dominant.flags & 2 != 0,
        x: f64::from(bounds.x0),
        y: f64::from(bounds.y0),
        width: f64::from(bounds.x1 - bounds.x0),
        height: f64::from(bounds.y1 - bounds.y0),
    })
}

/// A PDF backend over MuPDF.
///
/// It holds nothing: MuPDF's context is the crate's, one per thread, and each
/// call opens the document afresh.
#[derive(Debug, Default, Clone, Copy)]
pub struct MupdfExtractor;

impl MupdfExtractor {
    /// The backend.
    #[must_use]
    pub fn new() -> Self {
        MupdfExtractor
    }
}

/// A MuPDF failure as a [`PdfError`], `context` naming the step.
///
/// The file's side is MuPDF's `FORMAT`, `SYNTAX` and `UNSUPPORTED` — what a
/// damaged, truncated or non-PDF file throws (a garbage or empty file is
/// `FORMAT`, a truncated one `SYNTAX`, measured). Everything else is the
/// engine's: `SYSTEM` (out of memory, a syscall — a missing file too),
/// `LIBRARY`, `ARGUMENT`, `LIMIT`, `GENERIC` and the crate's own errors. MuPDF
/// names the path in an open failure, and it is removed.
fn classify(error: &mupdf::Error, context: &str, path: &std::path::Path) -> PdfError {
    use mupdf_sys::{FZ_ERROR_FORMAT, FZ_ERROR_SYNTAX, FZ_ERROR_UNSUPPORTED};
    let (detail, the_file) = match error {
        mupdf::Error::MuPdf(e) => (
            e.message.clone(),
            [FZ_ERROR_FORMAT, FZ_ERROR_SYNTAX, FZ_ERROR_UNSUPPORTED]
                .iter()
                .any(|&code| e.code == code as i32),
        ),
        other => (other.to_string(), false),
    };
    // shortcut: MuPDF cuts a message at 256 bytes, so a path long enough to be
    // cut survives the replace; check the path's length first if one ever does.
    let detail = format!("{context}: {detail}").replace(&path.display().to_string(), "the file");
    if the_file {
        PdfError::unreadable(&detail)
    } else {
        PdfError::engine(&detail)
    }
}

/// Whether a MuPDF message is about the PDF read, not the engine's own cache.
///
/// Every message is, but the store's report of a race between two threads
/// (`found duplicate fz_icc_link in the store`, from `fz_store_item`): it
/// keeps one of the two items and reads on, so no text is lost. Python's
/// `is_about_the_file` is the same rule, and `tests/fixtures/pdf_warnings.json`
/// pins both.
#[must_use]
pub fn is_about_the_file(message: &str) -> bool {
    let race = message
        .strip_prefix("found duplicate ")
        .and_then(|rest| rest.strip_suffix(" in the store"));
    // The item's type is a C identifier (`fz_icc_link`), matched as ASCII.
    !race.is_some_and(|kind| {
        !kind.is_empty()
            && kind
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    })
}

/// What MuPDF reports without failing, captured while one PDF is read (#599).
///
/// `mupdf-sys` silences MuPDF's warning and error callbacks, and MuPDF reads
/// past a broken xref (repairing it) or a page's content stream (stopping it
/// after too many syntax errors) with only a message. While this is alive the
/// calling thread's MuPDF context sends both kinds of message here, as
/// PyMuPDF's store collects both; dropping it puts back the callbacks it
/// found. MuPDF's context is the crate's, one per thread, and every MuPDF
/// call this module makes runs on the thread that made the capture (the limit
/// watcher makes none), so no other thread's messages arrive.
struct EngineMessages {
    context: *mut mupdf_sys::fz_context,
    warning: (mupdf_sys::fz_warning_cb, *mut std::ffi::c_void),
    error: (mupdf_sys::fz_error_cb, *mut std::ffi::c_void),
    /// Boxed so the address MuPDF holds stays put when `self` moves.
    messages: Box<std::cell::RefCell<Vec<String>>>,
}

/// MuPDF's callback for both kinds of message: keep it.
///
/// # Safety
///
/// `user` is the `messages` of a live [`EngineMessages`], and `message` a
/// NUL-terminated string, which is what MuPDF passes back.
unsafe extern "C" fn keep_message(user: *mut std::ffi::c_void, message: *const std::ffi::c_char) {
    // SAFETY: as the function's contract states; nothing else borrows the
    // cell while MuPDF runs, since only `finish` reads it, after its flush.
    let (messages, message) = unsafe {
        (
            &*user.cast::<std::cell::RefCell<Vec<String>>>(),
            std::ffi::CStr::from_ptr(message),
        )
    };
    messages
        .borrow_mut()
        .push(message.to_string_lossy().into_owned());
}

impl EngineMessages {
    /// Start capturing on this thread's MuPDF context.
    fn capture() -> Self {
        use mupdf_sys::{
            fz_context, fz_error_callback, fz_flush_warnings, fz_set_error_callback,
            fz_set_warning_callback, fz_warning_callback,
        };
        // `Context` is one `*mut fz_context` (`mupdf` 0.8.0, pinned exactly); a
        // crate that added a field would fail this assertion.
        const _: () = assert!(
            std::mem::size_of::<mupdf::Context>() == std::mem::size_of::<*mut fz_context>()
        );
        let messages = Box::new(std::cell::RefCell::new(Vec::new()));
        let user = std::ptr::from_ref(&*messages).cast_mut().cast();
        // SAFETY: the copy is the thread's context pointer, which the crate
        // keeps alive for the thread's life (`Context` has no destructor). The
        // flush clears a warning an earlier read left counted, which would
        // otherwise swallow this read's first identical one; it reports to the
        // callbacks found, before they are replaced.
        unsafe {
            let context: *mut fz_context = std::mem::transmute_copy(&mupdf::Context::get());
            fz_flush_warnings(context);
            let mut warning_user = std::ptr::null_mut();
            let warning = fz_warning_callback(context, &mut warning_user);
            let mut error_user = std::ptr::null_mut();
            let error = fz_error_callback(context, &mut error_user);
            fz_set_warning_callback(context, Some(keep_message), user);
            fz_set_error_callback(context, Some(keep_message), user);
            EngineMessages {
                context,
                warning: (warning, warning_user),
                error: (error, error_user),
                messages,
            }
        }
    }

    /// The messages so far that are [`is_about_the_file`] and not empty (as
    /// Python's split drops an empty one), in order, `path` replaced by "the file".
    fn finish(self, path: &std::path::Path) -> Vec<String> {
        // SAFETY: the context is this thread's, as in `capture`. A warning
        // repeated up to now is counted, and reported only on a flush.
        unsafe { mupdf_sys::fz_flush_warnings(self.context) };
        let path = path.display().to_string();
        self.messages
            .take()
            .into_iter()
            .filter(|message| !message.is_empty() && is_about_the_file(message))
            .map(|message| message.replace(&path, "the file"))
            .collect()
    }
}

impl Drop for EngineMessages {
    fn drop(&mut self) {
        // SAFETY: the context is this thread's, and the callbacks are the
        // ones `capture` found on it.
        unsafe {
            mupdf_sys::fz_set_warning_callback(self.context, self.warning.0, self.warning.1);
            mupdf_sys::fz_set_error_callback(self.context, self.error.0, self.error.1);
        }
    }
}

/// Open a PDF, refusing one that needs a password, as `extract_blocks` does.
fn open(path: &std::path::Path) -> Result<Document, PdfError> {
    let document = Document::open(path).map_err(|e| classify(&e, "could not open it", path))?;
    if document
        .needs_password()
        .map_err(|e| classify(&e, "could not open it", path))?
    {
        return Err(PdfError::encrypted());
    }
    Ok(document)
}

/// A cookie's `abort` field, for the watcher thread to set.
///
/// MuPDF's cookie is its documented way for one thread to stop another's run,
/// "unsynchronized without locking": the PDF interpreter reads `abort` once per
/// content-stream token and ends the page early, silently, when it is set.
/// The crate's [`Cookie`] cannot cross threads (`abort` takes `&mut`, and it is
/// `!Send`), so the field is reached through its pointer and written
/// atomically.
fn abort_flag(cookie: &Cookie) -> &AtomicI32 {
    use mupdf_sys::fz_cookie;
    // `Cookie` is one `*mut fz_cookie` (`mupdf` 0.8.0, pinned exactly); a crate
    // that added a field would fail this assertion.
    const _: () = assert!(std::mem::size_of::<Cookie>() == std::mem::size_of::<*mut fz_cookie>());
    // SAFETY: the copy is the pointer `cookie` holds, valid while `cookie` is
    // borrowed, which the returned reference is tied to; the copy is not
    // dropped (`*mut` has no destructor), so the cookie is freed once, by
    // `cookie`. `abort` is an aligned C `int`, which is what `AtomicI32` needs.
    // One precondition of `from_ptr` is knowingly not met: MuPDF reads `abort`
    // plainly while the watcher stores to it, a mixed access the Rust memory
    // model calls a race. It is accepted because that read is MuPDF's own
    // documented protocol, behind FFI where Rust cannot reorder it, and the
    // store is one aligned `int` write that cannot tear.
    unsafe {
        let raw: *mut fz_cookie = std::mem::transmute_copy(cookie);
        AtomicI32::from_ptr(std::ptr::addr_of_mut!((*raw).abort))
    }
}

/// Visit each page's bounds and text, in order, read with `flags`, stopping at
/// `limit`.
///
/// The whole page is run (`fz_run_page`: contents, then annotations and
/// widgets) into one device over an empty text page bounded by the page, which
/// is how PyMuPDF's `_get_textpage` builds it (see the module docs). The empty
/// text page comes from an empty display list, the crate's one way to make a
/// text page from a rectangle. A page is loaded only when it is reached, so a
/// long document is not held whole.
///
/// Every page runs with one cookie, which changes nothing else MuPDF does but
/// count. When `limit` is set, a scoped watcher thread checks it every
/// [`LIMIT_WATCH_INTERVAL`] and sets the cookie's `abort`, which stops the page
/// running; since that stop is silent, the flag is read back after each page,
/// so a page cut short is an error and never a short page (#600).
fn each_page(
    document: &Document,
    flags: TextPageFlags,
    path: &std::path::Path,
    limit: &PdfLimit,
    mut visit: impl FnMut(Rect, TextPage),
) -> Result<(), PdfError> {
    let count = document
        .page_count()
        .map_err(|e| classify(&e, "could not count the pages", path))?;
    let cookie = Cookie::new().map_err(|e| classify(&e, "could not start", path))?;
    let abort = abort_flag(&cookie);
    let done = AtomicBool::new(false);
    std::thread::scope(|scope| {
        let watcher = limit.is_set().then(|| {
            scope.spawn(|| {
                while !done.load(Ordering::Acquire) {
                    if limit.reached().is_some() {
                        abort.store(1, Ordering::Release);
                        return;
                    }
                    std::thread::park_timeout(LIMIT_WATCH_INTERVAL);
                }
            })
        });
        let mut walk = || -> Result<(), PdfError> {
            for index in 0..count {
                if let Some(stop) = limit.reached() {
                    return Err(stop);
                }
                let read = |page: Page| -> Result<(Rect, TextPage), mupdf::Error> {
                    let bounds = page.bounds()?;
                    let text_page = DisplayList::new(bounds)?.to_text_page(flags)?;
                    let device = Device::from_text_page(&text_page, flags)?;
                    page.run_with_cookie(&device, &Matrix::IDENTITY, &cookie)?;
                    // Dropping the device closes it, which flushes its last line.
                    drop(device);
                    Ok((bounds, text_page))
                };
                let page = document.load_page(index).and_then(read);
                if abort.load(Ordering::Acquire) != 0 {
                    return Err(limit.reached().unwrap_or_else(PdfError::cancelled));
                }
                let (bounds, text_page) = page.map_err(|e| {
                    classify(
                        &e,
                        &format!("could not read the text of page {index}"),
                        path,
                    )
                })?;
                visit(bounds, text_page);
            }
            Ok(())
        };
        let _done = WalkDone {
            done: &done,
            watcher: watcher.as_ref().map(|w| w.thread().clone()),
        };
        walk()
    })
}

/// Ends the watcher however the walk ends, a panic included: the scope joins the
/// watcher before a panic leaves it, and a watcher whose limit is never reached
/// (a cancel flag nobody sets) would otherwise keep it waiting for ever.
struct WalkDone<'a> {
    done: &'a AtomicBool,
    watcher: Option<std::thread::Thread>,
}

impl Drop for WalkDone<'_> {
    fn drop(&mut self) {
        self.done.store(true, Ordering::Release);
        if let Some(watcher) = &self.watcher {
            watcher.unpark();
        }
    }
}

/// A page's text as PyMuPDF's `get_text()` writes it (see the module docs).
#[must_use]
pub fn page_text(text_page: &TextPage, page: &Rect) -> String {
    let mut out = String::new();
    for block in text_page.blocks() {
        for line in block.lines() {
            let mut last = None;
            for ch in line.chars() {
                if overlaps(page, &quad_rect(&ch.quad())) {
                    let c = ch.char().unwrap_or(char::REPLACEMENT_CHARACTER);
                    out.push(c);
                    last = Some(c);
                }
            }
            if last.is_some_and(|c| c != '\n' && c != '\0') {
                out.push('\n');
            }
        }
    }
    out
}

impl PdfTextExtractor for MupdfExtractor {
    fn name(&self) -> &'static str {
        "mupdf"
    }

    fn extract_blocks_within(
        &self,
        path: &std::path::Path,
        limit: &PdfLimit,
    ) -> Result<Vec<TextBlock>, PdfError> {
        self.extract_blocks_with_warnings(path, limit)
            .map(|read| read.blocks)
    }

    /// MuPDF's messages are those reported from opening the file to its last
    /// page. On a failure they are dropped, the error saying what failed.
    fn extract_blocks_with_warnings(
        &self,
        path: &std::path::Path,
        limit: &PdfLimit,
    ) -> Result<PdfBlocks, PdfError> {
        let messages = EngineMessages::capture();
        let document = open(path)?;
        let mut blocks = Vec::new();
        let mut page_index = 0;
        each_page(&document, DICT_FLAGS, path, limit, |bounds, text_page| {
            // `lines()` yields nothing for an image block, which is the
            // `type != 0` skip `extract_blocks` makes.
            for block in text_page.blocks() {
                for line in block.lines() {
                    blocks.extend(line_block(&line, &bounds, page_index));
                }
            }
            page_index += 1;
        })?;
        drop(document);
        Ok(PdfBlocks {
            blocks,
            warnings: messages.finish(path),
        })
    }

    fn page_texts_within(
        &self,
        path: &std::path::Path,
        limit: &PdfLimit,
    ) -> Result<Vec<String>, PdfError> {
        self.page_texts_with_warnings(path, limit)
            .map(|(pages, _)| pages)
    }
}

impl MupdfExtractor {
    /// [`PdfTextExtractor::page_texts_within`], with MuPDF's messages as
    /// [`PdfTextExtractor::extract_blocks_with_warnings`] reports them.
    ///
    /// # Errors
    ///
    /// As [`PdfTextExtractor::page_texts_within`].
    pub fn page_texts_with_warnings(
        &self,
        path: &std::path::Path,
        limit: &PdfLimit,
    ) -> Result<(Vec<String>, Vec<String>), PdfError> {
        let messages = EngineMessages::capture();
        let document = open(path)?;
        let mut pages = Vec::new();
        each_page(&document, TEXT_FLAGS, path, limit, |bounds, text_page| {
            pages.push(page_text(&text_page, &bounds));
        })?;
        drop(document);
        Ok((pages, messages.finish(path)))
    }
}

// ---------------------------------------------------------------------------
// The adapter the service uses
// ---------------------------------------------------------------------------

/// The MuPDF backend as [`FullTextService`](crate::fulltext::service::FullTextService)
/// consumes one.
///
/// The service declares its own `PdfExtractor` trait so the conversion is
/// injectable, which is what makes its tests run without a PDF library. This is the
/// production implementation of that trait, and it is a separate type from
/// [`MupdfExtractor`] because the two answer different questions: the extractor
/// reports **blocks** for the segmenter, the service wants **HTML for the whole
/// document**.
#[derive(Debug, Default, Clone, Copy)]
pub struct MupdfPdfExtractor {
    extractor: MupdfExtractor,
}

impl MupdfPdfExtractor {
    /// The adapter.
    #[must_use]
    pub fn new() -> Self {
        MupdfPdfExtractor::default()
    }
}

impl crate::fulltext::service::PdfExtractor for MupdfPdfExtractor {
    fn extract(
        &self,
        pdf_path: &std::path::Path,
    ) -> Result<crate::fulltext::service::PdfText, crate::fulltext::service::PdfExtractError> {
        use crate::fulltext::pdf_text::render_html;
        use crate::fulltext::service::{PdfExtractError, PdfText};

        let (pages, warnings) = self
            .extractor
            .page_texts_with_warnings(pdf_path, &PdfLimit::default())
            .map_err(|e| PdfExtractError::Conversion(e.to_string()))?;
        let joined = pages.join("\n");
        let html = render_html(true, &joined, &pages);
        let char_count = joined.chars().count();
        let page_count = pages.len();

        // **Every page counts as converted**: a page's text is read or the call
        // fails, so there is no per-page partial outcome to report. What can be
        // empty is the document — a scanned PDF has pages and no text, which
        // `char_count` says and `is_complete` reads.
        Ok(PdfText {
            html,
            success: true,
            error_message: None,
            page_count,
            converted_pages: page_count,
            char_count,
            warnings,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fulltext::pdf_text::PdfErrorKind;
    use std::sync::Arc;
    use std::time::Duration;

    /// One empty page; MuPDF's repair supplies the offsets.
    const ONE_PAGE: &[u8] = b"%PDF-1.4\n1 0 obj <</Type/Catalog/Pages 2 0 R>> endobj\n\
        2 0 obj <</Type/Pages/Kids[3 0 R]/Count 1>> endobj\n\
        3 0 obj <</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>> endobj\n\
        trailer <</Root 1 0 R>>\n%%EOF\n";

    /// Every MuPDF code is placed: `FORMAT`, `SYNTAX` and `UNSUPPORTED` the
    /// file's and every other the engine's, so a new code cannot be blamed on
    /// the file by default.
    #[test]
    fn every_mupdf_code_is_placed() {
        use mupdf_sys::*;
        let codes = [
            (FZ_ERROR_NONE, PdfErrorKind::Engine),
            (FZ_ERROR_GENERIC, PdfErrorKind::Engine),
            (FZ_ERROR_SYSTEM, PdfErrorKind::Engine),
            (FZ_ERROR_LIBRARY, PdfErrorKind::Engine),
            (FZ_ERROR_ARGUMENT, PdfErrorKind::Engine),
            (FZ_ERROR_LIMIT, PdfErrorKind::Engine),
            (FZ_ERROR_UNSUPPORTED, PdfErrorKind::Unreadable),
            (FZ_ERROR_FORMAT, PdfErrorKind::Unreadable),
            (FZ_ERROR_SYNTAX, PdfErrorKind::Unreadable),
            (FZ_ERROR_TRYLATER, PdfErrorKind::Engine),
            (FZ_ERROR_ABORT, PdfErrorKind::Engine),
            (FZ_ERROR_REPAIRED, PdfErrorKind::Engine),
        ];
        for (code, kind) in codes {
            let error = mupdf::Error::MuPdf(mupdf::error::MuPdfError {
                code: code as i32,
                message: "cannot read /tmp/probe.pdf".to_string(),
            });
            let classified = classify(&error, "probing", std::path::Path::new("/tmp/probe.pdf"));
            assert_eq!(classified.kind, kind, "code {code}");
            assert!(!classified.message.contains("probe.pdf"), "{classified}");
        }
    }

    /// This thread's MuPDF context.
    fn context() -> *mut mupdf_sys::fz_context {
        // SAFETY: as in `EngineMessages::capture`.
        unsafe { std::mem::transmute_copy(&mupdf::Context::get()) }
    }

    /// MuPDF's warning callback on this thread's context.
    fn warning_callback() -> mupdf_sys::fz_warning_cb {
        // SAFETY: the context is this thread's; a null `user` is allowed.
        unsafe { mupdf_sys::fz_warning_callback(context(), std::ptr::null_mut()) }
    }

    /// Warn as MuPDF does, through the variadic `fz_warn`.
    fn warn(message: &std::ffi::CStr) {
        // SAFETY: the context is this thread's; `%s` takes the one string.
        unsafe { mupdf_sys::fz_warn(context(), c"%s".as_ptr(), message.as_ptr()) };
    }

    /// A warning counted before the capture, as a failed read leaves one, does
    /// not swallow the capture's first identical one; a warning repeated in
    /// it is reported once and then as MuPDF's count.
    #[test]
    fn a_warning_left_counted_does_not_swallow_the_next() {
        std::thread::spawn(|| {
            warn(c"left over");
            let messages = EngineMessages::capture();
            for _ in 0..3 {
                warn(c"left over");
            }
            assert_eq!(
                messages.finish(std::path::Path::new("/nowhere.pdf")),
                ["left over", "... repeated 3 times..."]
            );
        })
        .join()
        .expect("the thread ran");
    }

    /// The callback a capture found, none on a fresh thread (`mupdf-sys`
    /// silences MuPDF), is back once it ends, finished or not, and the path
    /// read never reaches a message.
    #[test]
    fn a_capture_puts_back_what_it_found_and_drops_the_path() {
        std::thread::spawn(|| {
            assert!(warning_callback().is_none());
            let messages = EngineMessages::capture();
            assert!(warning_callback().is_some());
            warn(c"cannot read /tmp/probe.pdf");
            // Set aside on the way in, not only by the predicate's own test.
            warn(c"found duplicate fz_icc_link in the store");
            warn(c"");
            assert_eq!(
                messages.finish(std::path::Path::new("/tmp/probe.pdf")),
                ["cannot read the file"]
            );
            assert!(warning_callback().is_none());
            drop(EngineMessages::capture());
            assert!(warning_callback().is_none());
        })
        .join()
        .expect("the thread ran");
    }

    /// A panic while a page is visited reaches the caller, rather than the
    /// scope waiting for ever on a watcher whose cancel flag nobody sets.
    #[test]
    fn a_panic_while_a_page_is_visited_is_not_a_hang() {
        let (sender, receiver) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let document = Document::from_bytes(ONE_PAGE, "application/pdf").expect("parses");
            let limit = PdfLimit {
                cancel: Some(Arc::new(AtomicBool::new(false))),
                ..PdfLimit::default()
            };
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                each_page(
                    &document,
                    TEXT_FLAGS,
                    std::path::Path::new("probe.pdf"),
                    &limit,
                    |_, _| panic!("visit"),
                )
            }));
            sender.send(outcome.is_err()).ok();
        });
        assert_eq!(receiver.recv_timeout(Duration::from_secs(10)), Ok(true));
    }
}
