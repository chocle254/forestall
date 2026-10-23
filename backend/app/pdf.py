"""Minimal text-only PDF writer (no dependencies) for dispute evidence documents."""

import textwrap

PAGE_W, PAGE_H, MARGIN, LEADING = 612, 792, 54, 14  # US Letter, points
MAX_CHARS = 92


ASCII = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                       "\u2014": "-", "\u2011": "-", "\u2022": "-", "\u00a0": " ", "\u202f": " ", "\u2026": "..."})


def _esc(s: str) -> str:
    s = s.translate(ASCII).encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render(title: str, lines: list[str]) -> bytes:
    """lines: plain text; a line starting with '# ' is a bold heading."""
    wrapped: list[tuple[str, bool]] = [(title, True), ("", False)]
    for line in lines:
        if line.startswith("# "):
            wrapped += [("", False), (line[2:], True)]
            continue
        for part in textwrap.wrap(line, MAX_CHARS) or [""]:
            wrapped.append((part, False))

    per_page = (PAGE_H - 2 * MARGIN) // LEADING
    pages = [wrapped[i:i + per_page] for i in range(0, len(wrapped), per_page)] or [[]]

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved after page/content objects
    page_ids = []
    for n, page in enumerate(pages, 1):
        ops = ["BT", f"{MARGIN} {PAGE_H - MARGIN} Td", f"{LEADING} TL"]
        for text, is_bold in page:
            ops.append(f"/{'F2' if is_bold else 'F1'} {11 if not is_bold else 12} Tf")
            ops.append(f"({_esc(text)}) '")
        ops += ["ET", f"BT /F1 8 Tf {MARGIN} {MARGIN - 24} Td (Forestall evidence report - page {n} of {len(pages)}) Tj ET"]
        stream = "\n".join(ops).encode("latin-1", "replace")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>".encode()))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
