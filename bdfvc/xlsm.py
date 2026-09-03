"""Save an Amazon template without losing its cascading dropdowns.

openpyxl round-trips almost everything in these workbooks correctly - the row-1
metadata, the 1500-odd defined names, the ~320 standard data validations - but it
does not understand the x14 extension block, so the conditional dropdowns
(product_subcategory, recommended_browse_nodes, unit_count type, EPR packaging
materials) are dropped on save.

Amazon's ingestion reads cell values and ignores validation rules, so a file
without them still uploads. They matter for the human doing the final review, so
we splice the original block back in.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

# Prefixes the extension block may use. openpyxl writes a bare <worksheet> root,
# so any prefix the block references has to be re-declared or the file will not
# parse ("unbound prefix").
KNOWN_NS = {
    "x14": "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main",
    "xm": "http://schemas.microsoft.com/office/excel/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "xr": "http://schemas.microsoft.com/office/spreadsheetml/2014/revision",
    "x14ac": "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _declared_prefixes(root_tag: str) -> set:
    return set(re.findall(r'xmlns:([A-Za-z0-9]+)=', root_tag))


def _used_prefixes(fragment: str) -> set:
    used = set(re.findall(r"<([A-Za-z0-9]+):", fragment))
    used |= set(re.findall(r"\s([A-Za-z0-9]+):[A-Za-z]+=", fragment))
    return used


def _ensure_namespaces(sheet_xml: str, fragment: str, original_root: str) -> str:
    """Add any xmlns declaration the fragment relies on to the worksheet root."""
    m = re.search(r"<worksheet\b[^>]*>", sheet_xml)
    if not m:
        return sheet_xml
    root = m.group(0)
    have = _declared_prefixes(root) | _declared_prefixes(fragment)
    need = _used_prefixes(fragment) - have

    original_decls = dict(re.findall(r'xmlns:([A-Za-z0-9]+)="([^"]+)"', original_root))
    additions = []
    for prefix in sorted(need):
        uri = original_decls.get(prefix) or KNOWN_NS.get(prefix)
        if uri:
            additions.append(f'xmlns:{prefix}="{uri}"')
    if not additions:
        return sheet_xml
    new_root = root[:-1].rstrip() + " " + " ".join(additions) + ">"
    return sheet_xml.replace(root, new_root, 1)


def _extract_extlst(xml: str):
    i = xml.find("<extLst")
    if i < 0:
        return None
    j = xml.find("</extLst>", i)
    if j < 0:
        return None
    return xml[i : j + len("</extLst>")]


def save_preserving_extensions(wb, source_path, out_path, sheet_xml_path):
    """Save `wb`, then copy the source sheet's <extLst> into the result."""
    out_path = Path(out_path)
    wb.save(out_path)

    if not sheet_xml_path:
        return out_path

    with zipfile.ZipFile(source_path) as z:
        try:
            original = z.read(sheet_xml_path).decode("utf-8", errors="ignore")
        except KeyError:
            return out_path
    ext = _extract_extlst(original)
    if not ext:
        return out_path

    with zipfile.ZipFile(out_path) as z:
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    sheet_xml = blobs.get(sheet_xml_path)
    if sheet_xml is None:
        return out_path
    text = sheet_xml.decode("utf-8", errors="ignore")
    if "<extLst" in text:
        return out_path

    original_root_m = re.search(r"<worksheet\b[^>]*>", original)
    original_root = original_root_m.group(0) if original_root_m else ""
    text = _ensure_namespaces(text, ext, original_root)

    # extLst must be the last child of <worksheet>.
    text = re.sub(r"</worksheet>\s*$", ext + "</worksheet>", text)
    blobs[sheet_xml_path] = text.encode("utf-8")

    tmp = Path(tempfile.mkstemp(suffix=".xlsm")[1])
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, blobs[n])
    shutil.move(str(tmp), str(out_path))
    return out_path
