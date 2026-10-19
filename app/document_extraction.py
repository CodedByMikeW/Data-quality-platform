"""
Turns an uploaded file into plain text, based on its extension. This is
deliberately the only job of this module - chunking, embeddings, and search
are separate, later steps that all depend on having reliable extracted text
to work with first.
"""
import io
import zipfile

from docx import Document as DocxFile
from pypdf import PdfReader

from app.config import settings

_CHARS_PER_CHUNK_BUDGET = 2_000  # ~2.5x what a chunk of ordinary prose holds, so only a doc far over the chunk limit is stopped early
_MAX_BYTES_PER_CHAR = 4  # UTF-8: no character takes more, so n bytes are at least n / 4 characters
_UNPACK_FACTOR = 2  # everything in a Word file, pictures included, may unpack to this many times the upload limit
_MAX_XML_UNPACKED_BYTES = 10 * 1024 * 1024  # ...but the text parts of it to no more than this (or the upload limit, if lower)


class UnsupportedFileType(Exception):
    pass


class FileTooLarge(Exception):
    """A file within the upload limit that is still too much to read, e.g. a small file that unpacks huge."""


class TextTooLong(FileTooLarge):
    """More text than the search index takes. Carries no message: the caller words it (see limits.document_too_long)."""


def _mb_text(n_bytes: int) -> str:
    """
    Megabytes for a message, rounded UP to one decimal and without a trailing
    ".0": a file a byte over a limit then reads "10.1 MB, more than the 10 MB"
    instead of "10 MB, more than the 10 MB".
    """
    tenths = -(-n_bytes * 10 // (1024 * 1024))
    whole, tenth = divmod(tenths, 10)
    return f"{whole:,}" if tenth == 0 else f"{whole:,}.{tenth}"


def _refuse_if_unpacks_huge(contents: bytes) -> None:
    """
    A .docx is a zip, and a zip that is a few KB on disk can unpack to gigabytes
    (a "zip bomb"). The sizes it declares are checked before anything is
    unpacked - zipfile never reads past a declared size, so they can't be faked
    to hide more.

    Two limits, because the two kinds of content cost very different amounts to
    open. Pictures and other binary parts are held as they are, so they cost
    about their size. The XML parts (the text, styles, numbering) are built
    into a tree, and that costs about ten times their size - measured: a Word
    file whose text unpacks to 118 MB took 1.1 GB to open. So the XML gets a
    much smaller allowance than the whole file does.
    """
    total_limit = settings.max_upload_bytes * _UNPACK_FACTOR
    xml_limit = min(_MAX_XML_UNPACKED_BYTES, settings.max_upload_bytes)
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            parts = archive.infolist()
    except zipfile.BadZipFile:
        return  # not a zip at all - python-docx reports that in its own words
    total = sum(info.file_size for info in parts)
    xml = sum(info.file_size for info in parts if info.filename.lower().endswith((".xml", ".rels")))
    if total > total_limit:
        raise FileTooLarge(
            f"This Word file unpacks to {_mb_text(total)} MB, "
            f"more than the {_mb_text(total_limit)} MB the server will open."
        )
    if xml > xml_limit:
        raise FileTooLarge(
            f"The text inside this Word file unpacks to {_mb_text(xml)} MB, "
            f"more than the {_mb_text(xml_limit)} MB the server will open. "
            "Split it into parts or save a plainer copy."
        )


def _refuse_if_too_long_to_index(contents: bytes, index: bool) -> None:
    """
    Plain text with more bytes than the search index could ever take characters is
    turned away before it is decoded. Decoding and word-counting a 25 MB file costs
    several times its size in memory, only to say "too long" at the end; this says
    it first. (Nothing the index would have taken is refused: n bytes are at least
    n / 4 characters.)
    """
    if index and len(contents) > settings.max_document_chunks * _CHARS_PER_CHUNK_BUDGET * _MAX_BYTES_PER_CHAR:
        raise TextTooLong()


def extract_text(filename: str, contents: bytes, index: bool = True) -> tuple[str, str]:
    """
    Returns (content_type, extracted_text) for a supported file type.

    `index` says the text is going to be searched, so it is held to the length the
    search index takes. A check's data file is kept as rows to look at, not indexed
    (see store_document's embed=False), and passes index=False.
    """
    lower = filename.lower()
    # Stop reading a PDF or Word file once it has more text than the index
    # could take anyway (see max_document_chunks) - without this a 3,000-page
    # PDF is read to the end just to be turned away.
    max_chars = settings.max_document_chunks * _CHARS_PER_CHUNK_BUDGET

    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(contents))
        pages, total = [], 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            total += len(page_text)
            if total > max_chars:
                raise TextTooLong()
            pages.append(page_text)
        return "pdf", "\n\n".join(pages).strip()

    if lower.endswith(".docx"):
        _refuse_if_unpacks_huge(contents)
        doc = DocxFile(io.BytesIO(contents))
        paragraphs, total = [], 0
        for paragraph in doc.paragraphs:
            total += len(paragraph.text)
            if total > max_chars:
                raise TextTooLong()
            paragraphs.append(paragraph.text)
        return "docx", "\n".join(paragraphs).strip()

    if lower.endswith(".csv"):
        _refuse_if_too_long_to_index(contents, index)
        return "csv", contents.decode("utf-8", errors="replace").strip()

    if lower.endswith(".txt"):
        _refuse_if_too_long_to_index(contents, index)
        return "text", contents.decode("utf-8", errors="replace").strip()

    raise UnsupportedFileType(
        f"Unsupported file type for '{filename}' - upload a PDF, DOCX, CSV, or TXT file."
    )
