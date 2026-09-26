import difflib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from agents.basic_agent import BasicAgent

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_XML_BYTES = 60 * 1024 * 1024
MAX_TEXT = 400
TEXT_TYPES = (".txt", ".md", ".csv")


def _clip(text):
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def _docx_paragraphs(path):
    with zipfile.ZipFile(path) as archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError:
            raise ValueError(f"{path.name} isn't a Word document (no word/document.xml).")
        if info.file_size > MAX_XML_BYTES:
            raise ValueError(f"{path.name} is too large to compare.")
        root = ElementTree.fromstring(archive.read(info))
    paragraphs = []
    for paragraph in root.iter(W + "p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (W + "tab", W + "br", W + "cr"):
                parts.append(" ")
        paragraphs.append("".join(parts))
    return paragraphs


def _read(value, label):
    if not value:
        raise ValueError(f"{label} is required: the path of a .docx, .txt, .md or .csv file.")
    path = Path(str(value)).expanduser()
    if not path.is_file():
        raise ValueError(f"{label}: no file at {path}.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{label}: {path.name} is over 25 MB.")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        lines = _docx_paragraphs(path)
    elif suffix in TEXT_TYPES:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        raise ValueError(f"{label}: {path.name} isn't a .docx, .txt, .md or .csv file.")
    lines = [" ".join(line.split()) for line in lines]
    return path.name, [line for line in lines if line]


def _pair(old_block, new_block):
    """Match each old paragraph to its most similar later new paragraph, in order; the rest are added or removed."""
    pairs, start = {}, 0
    if len(old_block) * len(new_block) > 10000:
        return pairs
    for i, before in enumerate(old_block):
        best, best_j = 0.0, None
        for j in range(start, len(new_block)):
            matcher = difflib.SequenceMatcher(None, before, new_block[j], autojunk=False)
            if matcher.quick_ratio() > best and matcher.ratio() > best:
                best, best_j = matcher.ratio(), j
        if best_j is not None and best >= 0.5:
            pairs[best_j] = i
            start = best_j + 1
    return pairs


def _word_changes(before, after):
    old, new = before.split(), after.split()
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            changes.append({"from": _clip(" ".join(old[i1:i2])), "to": _clip(" ".join(new[j1:j2]))})
    return changes


class CompareDocumentsAgent(BasicAgent):
    def __init__(self):
        self.name = "CompareDocuments"
        self.metadata = {
            "name": self.name,
            "description": ("Compare two versions of a document (Word .docx, or .txt, .md, .csv) and list every "
                            "difference: paragraphs added, removed and changed, with the exact words that changed. "
                            "It reads both files in full, so small edits such as a changed number or date are not "
                            "skipped. Pass the file paths of the older and newer versions."),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Path of the older version."},
                    "new_path": {"type": "string", "description": "Path of the newer version."},
                    "max_changes": {"type": "integer",
                                    "description": "Most changes to list, 1 to 500. Defaults to 200."}
                },
                "required": ["old_path", "new_path"]
            }
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        try:
            limit = int(kwargs.get("max_changes") or 200)
        except (TypeError, ValueError):
            return "max_changes must be a whole number."
        limit = max(1, min(limit, 500))
        try:
            old_name, old = _read(kwargs.get("old_path"), "old_path")
            new_name, new = _read(kwargs.get("new_path"), "new_path")
        except (ValueError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
            return f"Couldn't read the documents: {error}"

        changes, counts = [], {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == "equal":
                counts["unchanged"] += i2 - i1
                continue
            pairs = _pair(old[i1:i2], new[j1:j2]) if tag == "replace" else {}
            matched = set(pairs.values())
            next_old = i1

            def removed_until(stop):
                nonlocal next_old
                for k in range(next_old, stop):
                    if k - i1 not in matched:
                        counts["removed"] += 1
                        changes.append({"type": "removed", "where": f"paragraph {k + 1} of the old version",
                                        "before": _clip(old[k])})
                next_old = max(next_old, stop)

            for k in range(j1, j2):
                if k - j1 in pairs:
                    source = i1 + pairs[k - j1]
                    removed_until(source)
                    next_old = source + 1
                    counts["changed"] += 1
                    changes.append({"type": "changed", "where": f"paragraph {k + 1} of the new version",
                                    "before": _clip(old[source]), "after": _clip(new[k]),
                                    "words": _word_changes(old[source], new[k])})
                else:
                    counts["added"] += 1
                    changes.append({"type": "added", "where": f"paragraph {k + 1} of the new version",
                                    "after": _clip(new[k])})
            removed_until(i2)

        return json.dumps({
            "old": old_name, "new": new_name,
            "paragraphs": {"old": len(old), "new": len(new)},
            "counts": counts,
            "identical": not changes,
            "changes": changes[:limit],
            "more_changes_not_listed": max(0, len(changes) - limit),
        }, ensure_ascii=False)
