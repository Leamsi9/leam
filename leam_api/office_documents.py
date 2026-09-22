"""Bounded, inert Office Open XML inspection; never extracts or executes files."""

import io
import posixpath
import stat
import time
import zipfile
import zlib
from xml.etree import ElementTree as ET
from xml.parsers import expat

OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx", "word/document.xml", "document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        ".xlsx", "xl/workbook.xml", "workbook",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        ".pptx", "ppt/presentation.xml", "presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    ),
}
LEGACY_MIMES = {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}
MAX_ENTRIES = 2048
MAX_EXPANDED = 64 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_XML = 32 * 1024 * 1024
MAX_NODES = 200000
MAX_SECONDS = 8


def local(name):
    return name.rsplit("}", 1)[-1]


def elements(root, name):
    return (node for node in root.iter() if local(node.tag) == name)


class Package:
    def __init__(self, data, mime):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.nodes = 0
        self.xml = {}
        if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValueError("Encrypted or legacy Office file. Remove password protection and save as DOCX, XLSX or PPTX.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ENTRIES:
                    raise ValueError("Office archive exceeds the 2048-entry safety limit")
                total = xml_size = 0
                names = set()
                for info in infos:
                    self.check_time()
                    name = info.filename
                    folded = name.casefold()
                    if (not name or name.startswith("/") or "\\" in name
                            or any(part in {".", ".."} for part in name.split("/"))
                            or any(ord(c) < 32 or ord(c) == 127 for c in name)
                            or ":" in name or folded in names
                            or stat.S_ISLNK(info.external_attr >> 16)):
                        raise ValueError("Office archive contains unsafe or duplicate paths")
                    names.add(folded)
                    if info.flag_bits & 1 or folded.rsplit("/", 1)[-1] in {"encryptioninfo", "encryptedpackage"}:
                        raise ValueError("Encrypted Office files are unsupported; remove password protection first")
                    if "vbaproject" in folded or "/activex/" in folded:
                        raise ValueError("Macro-enabled or active-content Office files are unsupported; save a macro-free copy")
                    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        raise ValueError("Office archive uses unsupported compression")
                    total += info.file_size
                    if info.file_size > MAX_MEMBER or total > MAX_EXPANDED or info.file_size > max(1, info.compress_size) * 200:
                        raise ValueError("Office archive exceeds expanded-size or compression-ratio safety limits")
                    if name.endswith((".xml", ".rels")):
                        xml_size += info.file_size
                        if xml_size > MAX_XML:
                            raise ValueError("Office XML exceeds the 32 MiB safety limit")
                    # Stream every member through CRC validation without retaining binary media.
                    chunks = []
                    read = 0
                    with archive.open(info) as member:
                        while chunk := member.read(65536):
                            self.check_time()
                            read += len(chunk)
                            if read > info.file_size or read > MAX_MEMBER:
                                raise ValueError("Office archive member exceeds its declared size")
                            if name.endswith((".xml", ".rels")):
                                chunks.append(chunk)
                    if name.endswith((".xml", ".rels")):
                        self.xml[name] = self.parse_xml(b"".join(chunks))
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, OSError, EOFError, UnicodeError, zlib.error, expat.ExpatError) as error:
            raise ValueError("Invalid Office ZIP/XML document; open it in Office and save a new copy") from error
        self.main = OFFICE_TYPES[mime][1]
        root_name, content_type = OFFICE_TYPES[mime][2:]
        types = self.xml.get("[Content_Types].xml")
        root = self.xml.get(self.main)
        if types is None or local(types.tag) != "Types" or root is None or local(root.tag) != root_name:
            raise ValueError("Office package contents do not match its file extension and MIME type")
        declared = {}
        for item in types:
            value = item.get("ContentType", "")
            if any(marker in value.lower() for marker in ("macroenabled", "vbaproject", "activex", "macrosheet")):
                raise ValueError("Macro-enabled Office files are unsupported; save a macro-free DOCX, XLSX or PPTX copy")
            if local(item.tag) == "Override":
                declared[item.get("PartName", "").lstrip("/")] = value
        if declared.get(self.main) != content_type:
            raise ValueError("Office main content type does not match the selected format")
        office = [path for kind, path in self.relationships("").values() if kind == "officeDocument"]
        if office != [self.main]:
            raise ValueError("Office document is missing a valid main-part relationship")
        # Validate referenced sheets/slides even when the text budget is already full.
        if root_name in {"workbook", "presentation"}:
            self.ordered_parts("sheet" if root_name == "workbook" else "sldId",
                               "worksheet" if root_name == "workbook" else "slide")

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise ValueError("Office document processing exceeded its safety time limit")

    def parse_xml(self, data):
        builder = ET.TreeBuilder()
        parser = expat.ParserCreate(namespace_separator="}")
        depth = 0

        def reject(*_args):
            raise ValueError("Office XML must not contain DTDs or entity declarations")

        def start(name, attrs):
            nonlocal depth
            depth += 1
            self.nodes += 1
            self.check_time()
            if depth > 64 or self.nodes > MAX_NODES or len(attrs) > 64:
                raise ValueError("Office XML exceeds nesting, element or attribute safety limits")
            builder.start(name, attrs)

        def end(name):
            nonlocal depth
            builder.end(name)
            depth -= 1

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = builder.data
        parser.StartDoctypeDeclHandler = reject
        parser.EntityDeclHandler = reject
        parser.ExternalEntityRefHandler = reject
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        for offset in range(0, len(data), 65536):
            self.check_time()
            parser.Parse(data[offset:offset + 65536], False)
        parser.Parse(b"", True)
        return builder.close()

    def relationships(self, source):
        directory, basename = posixpath.split(source)
        name = posixpath.join(directory, "_rels", basename + ".rels") if source else "_rels/.rels"
        root = self.xml.get(name)
        if root is None:
            return {}
        if local(root.tag) != "Relationships":
            raise ValueError("Invalid Office relationships part")
        result = {}
        for item in root:
            if local(item.tag) != "Relationship":
                continue
            key = item.get("Id", "")
            if not key or key in result:
                raise ValueError("Office relationships contain missing or duplicate IDs")
            # Hyperlinks and linked media are inert; never resolve an external target.
            if item.get("TargetMode") == "External":
                continue
            target = item.get("Target", "")
            path = posixpath.normpath(posixpath.join(directory, target)) if not target.startswith("/") else target.lstrip("/")
            if not target or path.startswith("../") or ":" in path or "\\" in path:
                raise ValueError("Office relationship target is invalid")
            result[key] = (item.get("Type", "").rsplit("/", 1)[-1], path)
        return result

    def ordered_parts(self, element, kind):
        links = self.relationships(self.main)
        result = []
        for node in elements(self.xml[self.main], element):
            key = next((value for name, value in node.attrib.items() if "}" in name and local(name) == "id"), "")
            actual, path = links.get(key, (None, None))
            if actual != kind or path not in self.xml:
                raise ValueError("Office document has a missing or invalid sheet/slide relationship")
            result.append((node, path))
        return result


class Text:
    def __init__(self, limit):
        # Retain one extra character so the caller can report truncation honestly.
        self.remaining = max(0, limit) + 1
        self.chunks = []

    def add(self, value):
        if self.remaining:
            chunk = value[:self.remaining]
            self.chunks.append(chunk)
            self.remaining -= len(chunk)

    def paragraphs(self, root):
        for paragraph in elements(root, "p"):
            if not self.remaining:
                return
            for node in paragraph.iter():
                name = local(node.tag)
                if name == "t":
                    self.add(node.text or "")
                elif name in {"tab", "br", "cr"}:
                    self.add("\t" if name == "tab" else "\n")
            self.add("\n")


def inspect_office(data, mime, limit=None):
    """Validate the whole package, optionally return bounded plain text + scope."""
    package = Package(data, mime)
    if limit is None:
        return None
    text = Text(limit)
    extension = OFFICE_TYPES[mime][0]
    if extension == ".docx":
        text.paragraphs(package.xml[package.main])
        for path in sorted(package.xml):
            if path.startswith("word/") and path.endswith(".xml") and posixpath.basename(path).startswith(("header", "footer", "footnotes", "endnotes")):
                text.add("\n[" + posixpath.basename(path) + "]\n")
                text.paragraphs(package.xml[path])
        scope = "Word paragraphs/tables, headers, footers and notes; no images, OCR or layout"
    elif extension == ".xlsx":
        links = package.relationships(package.main)
        shared_path = next((path for kind, path in links.values() if kind == "sharedStrings"), "xl/sharedStrings.xml")
        shared = package.xml.get(shared_path)
        strings = ["".join(n.text or "" for n in elements(item, "t")) for item in elements(shared, "si")] if shared is not None else []
        for sheet, path in package.ordered_parts("sheet", "worksheet"):
            if not text.remaining:
                break
            text.add("[Sheet: " + sheet.get("name", "Unnamed") + "]\n")
            for row in elements(package.xml[path], "row"):
                if not text.remaining:
                    break
                for cell in row:
                    if local(cell.tag) != "c":
                        continue
                    value = next((n.text or "" for n in cell if local(n.tag) == "v"), "")
                    kind = cell.get("t")
                    if kind == "s":
                        if not value.isdecimal() or len(value) > 8 or int(value) >= len(strings):
                            raise ValueError("Excel cell refers to an invalid shared string")
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(n.text or "" for n in elements(cell, "t"))
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    formula = next((n.text or "" for n in cell if local(n.tag) == "f"), None)
                    if formula is not None:
                        value = (value + " [cached; formula: =" + formula + "]") if value else "[formula: =" + formula + "; no cached value]"
                    text.add(cell.get("r", "?") + "=" + value + "\t")
                text.add("\n")
        scope = "Excel sheets and cell values; formulas shown with saved cached results, never recalculated; dates may be serial numbers; no charts/images"
    else:
        for index, (_slide, path) in enumerate(package.ordered_parts("sldId", "slide"), 1):
            if not text.remaining:
                break
            text.add(f"[Slide {index}]\n")
            text.paragraphs(package.xml[path])
            for kind, target in package.relationships(path).values():
                if kind == "notesSlide" and target in package.xml:
                    text.add("[Speaker notes]\n")
                    text.paragraphs(package.xml[target])
        scope = "PowerPoint slide text/tables and speaker notes in presentation order; no images, OCR or layout"
    package.check_time()
    extracted = "".join(text.chunks)
    if extension == ".docx" and len(extracted) <= limit and not extracted.strip():
        raise ValueError("Word text extraction found no text. Image-only documents need OCR; attach page images instead.")
    return extracted, scope
