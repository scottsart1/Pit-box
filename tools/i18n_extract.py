"""List every English string the screens translate, per catalog.

A catalog maps English text to its translation, so the English text is the
key. This tool finds the keys the same way the runtime does:

* static markup: each text node, and the placeholder, title, aria-label and
  alt attributes, with whitespace collapsed; an element marked
  ``data-i18n-html`` contributes its whole inner markup as one key instead;
* scripts: the string literal passed to ``i18n.t(...)``, and the English
  singular and plural passed to ``i18n.tp(count, one, other, ...)``;
* Settings: each setting's group, label and description, the labels of its
  choices, and the choice codes of the settings listed in
  ``TRANSLATED_CHOICE_SETTINGS``;
* ``static/i18n/server-keys.json``: fixed strings the server sends that the
  screens translate as they are shown.

Usage:
    python tools/i18n_extract.py            # summary per catalog
    python tools/i18n_extract.py --json     # every key with its sources
    python tools/i18n_extract.py --pseudo   # write qps.json pseudo catalogs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
DASHBOARD = STATIC / "driver-dashboard"

CATALOGS = {
    # catalog name -> (catalog directory, markup files, script files)
    "app": (
        STATIC / "i18n",
        (STATIC / "index.html", STATIC / "overlay.html"),
        tuple(
            path for path in sorted((STATIC / "js").glob("*"))
            if path.suffix in {".js", ".mjs"} and path.name != "i18n.js"
        ),
    ),
    "dashboard": (
        DASHBOARD / "i18n",
        (DASHBOARD / "index.html",),
        tuple(
            path for path in sorted(DASHBOARD.glob("*"))
            if path.suffix in {".js", ".mjs"} and path.name not in {"i18n.mjs", "sw.js"}
        ),
    ),
}
LANGUAGES = ("fr", "de", "es")
TRANSLATED_ATTRIBUTES = ("placeholder", "title", "aria-label", "alt")
SKIP_TAGS = {"script", "style", "textarea", "code", "pre", "noscript", "template"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "source", "track", "wbr",
}
# Settings whose choice codes are words a reader should see translated.
# Provider names, voices and other identifiers stay as they are.
TRANSLATED_CHOICE_SETTINGS = ("radio_verbosity", "field_trace_scope", "capture_mode", "raw_capture")
LETTER = re.compile(r"[A-Za-z]")
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


@dataclass
class Key:
    text: str
    plural: bool = False
    one: str | None = None
    html: bool = False
    sources: list[str] = field(default_factory=list)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _MarkupKeys(HTMLParser):
    """Collect keys from static markup the way i18n.translateDom finds them."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.found: list[tuple[str, bool, int]] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, skipped)
        self._html_depth: int | None = None
        self._html_parts: list[str] = []
        self._html_line = 0

    def _skipped(self) -> bool:
        return bool(self._stack) and self._stack[-1][1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        skipped = (
            self._skipped() or tag in SKIP_TAGS
            or values.get("translate") == "no" or "data-i18n-skip" in values
        )
        line = self.getpos()[0]
        if self._html_depth is not None:
            rendered = "".join(
                f' {name}="{(value or "").replace("&", "&amp;").replace(chr(34), "&quot;")}"'
                for name, value in attrs
            )
            self._html_parts.append(f"<{tag}{rendered}>")
            if tag in VOID_TAGS:
                self._html_parts.append(f"</{tag}>")
                return
            self._stack.append((tag, skipped))
            return
        if not skipped:
            for name in TRANSLATED_ATTRIBUTES:
                value = values.get(name)
                if value is not None and LETTER.search(normalise(value)):
                    self.found.append((normalise(value), False, line))
        if tag in VOID_TAGS:
            return
        self._stack.append((tag, skipped))
        if "data-i18n-html" in values and not skipped:
            self._html_depth = len(self._stack)
            self._html_parts = []
            self._html_line = line

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        # Pop to the matching tag, tolerating omitted end tags.
        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth][0] == tag:
                break
        else:
            return
        while len(self._stack) > depth:
            closing, _ = self._stack.pop()
            if self._html_depth is not None:
                if len(self._stack) + 1 == self._html_depth:
                    key = normalise("".join(self._html_parts))
                    if LETTER.search(key):
                        self.found.append((key, True, self._html_line))
                    self._html_depth = None
                else:
                    self._html_parts.append(f"</{closing}>")

    def handle_data(self, data: str) -> None:
        if self._html_depth is not None:
            self._html_parts.append(
                data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            return
        if self._skipped():
            return
        key = normalise(data)
        if key and LETTER.search(key):
            self.found.append((key, False, self.getpos()[0]))


def markup_keys(path: Path) -> list[tuple[str, bool, int]]:
    parser = _MarkupKeys(str(path))
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.found


_CALL = re.compile(r"\bi18n\.(tp|t)\(")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


class ScriptError(ValueError):
    pass


def _read_literal(text: str, index: int) -> tuple[str | None, int]:
    """Read a JS string literal at ``index``; None when it is not one."""
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] not in "'\"`":
        return None, index
    quote = text[index]
    index += 1
    out: list[str] = []
    while index < len(text):
        char = text[index]
        if char == quote:
            return "".join(out), index + 1
        if quote == "`" and text.startswith("${", index):
            return None, index  # interpolated template: not a fixed key
        if char == "\\":
            index += 1
            escaped = text[index]
            if escaped in _ESCAPES:
                out.append(_ESCAPES[escaped])
            elif escaped == "u":
                if text[index + 1] == "{":
                    end = text.index("}", index)
                    out.append(chr(int(text[index + 2:end], 16)))
                    index = end
                else:
                    out.append(chr(int(text[index + 1:index + 5], 16)))
                    index += 4
            elif escaped == "x":
                out.append(chr(int(text[index + 1:index + 3], 16)))
                index += 2
            elif escaped == "\n":
                pass
            else:
                out.append(escaped)
            index += 1
            continue
        out.append(char)
        index += 1
    raise ScriptError("unterminated string")


def _skip_argument(text: str, index: int) -> int:
    """Index of the comma that ends the argument starting at ``index``."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            _, index = _read_literal(text, index)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index
            depth -= 1
        elif char == "," and depth == 0:
            return index
        index += 1
    raise ScriptError("unterminated call")


def _expect_comma(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] != ",":
        raise ScriptError("expected a comma")
    return index + 1


@dataclass
class ScriptCall:
    kind: str
    line: int
    text: str | None
    one: str | None = None


def script_calls(path: Path) -> list[ScriptCall]:
    text = path.read_text(encoding="utf-8")
    calls: list[ScriptCall] = []
    for match in _CALL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        index = match.end()
        try:
            if match.group(1) == "t":
                literal, _ = _read_literal(text, index)
                calls.append(ScriptCall("t", line, literal))
            else:
                index = _expect_comma(text, _skip_argument(text, index))
                one, index = _read_literal(text, index)
                if one is None:
                    calls.append(ScriptCall("tp", line, None))
                    continue
                index = _expect_comma(text, index)
                other, _ = _read_literal(text, index)
                calls.append(ScriptCall("tp", line, other, one))
        except (ScriptError, IndexError, ValueError):
            calls.append(ScriptCall(match.group(1), line, None))
    return calls


def settings_strings() -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from pitwall.settings_service import SETTINGS_SPEC

    strings: list[str] = []
    for name, spec in SETTINGS_SPEC.items():
        strings.extend((spec["group"], spec["label"], spec["description"]))
        strings.extend(spec.get("choice_labels", {}).values())
        if name in TRANSLATED_CHOICE_SETTINGS:
            strings.extend(spec["choices"])
    return [normalise(text) for text in strings]


def server_keys() -> list[str]:
    path = STATIC / "i18n" / "server-keys.json"
    if not path.exists():
        return []
    return [normalise(text) for text in json.loads(path.read_text(encoding="utf-8"))]


def collect(catalog: str) -> tuple[dict[str, Key], list[str]]:
    """Keys for one catalog, plus calls whose key is not a fixed literal."""
    _directory, markup_files, script_files = CATALOGS[catalog]
    keys: dict[str, Key] = {}
    dynamic: list[str] = []

    def add(text: str, source: str, *, plural: bool = False, one: str | None = None, html: bool = False) -> None:
        if not text or not LETTER.search(text):
            return
        key = keys.setdefault(text, Key(text))
        key.plural = key.plural or plural
        key.one = key.one or one
        key.html = key.html or html
        key.sources.append(source)

    for path in markup_files:
        relative = path.relative_to(ROOT).as_posix()
        for text, html, line in markup_keys(path):
            add(text, f"{relative}:{line}", html=html)
    for path in (*markup_files, *script_files):
        relative = path.relative_to(ROOT).as_posix()
        for call in script_calls(path):
            if call.text is None:
                dynamic.append(f"{relative}:{call.line}")
            elif call.kind == "tp":
                add(call.text, f"{relative}:{call.line}", plural=True, one=call.one)
            else:
                add(call.text, f"{relative}:{call.line}")
    if catalog == "app":
        for text in settings_strings():
            add(text, "src/pitwall/settings_service.py")
        for text in server_keys():
            add(text, "static/i18n/server-keys.json")
    return keys, dynamic


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def pseudo(text: str) -> str:
    """Accented, lengthened text that keeps placeholders and markup intact."""
    swapped = []
    accents = str.maketrans("aeiouAEIOUcnyCNY", "àéîõüÀÉÎÕÜçñÿÇÑŸ")
    for part in re.split(r"(\{[A-Za-z0-9_]+\}|<[^>]+>|&[a-z]+;)", text):
        if part.startswith(("{", "<", "&")):
            swapped.append(part)
        else:
            swapped.append(part.translate(accents))
    body = "".join(swapped)
    return f"⟦{body}{'·' * max(1, len(text) * 2 // 5)}⟧"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print every key with its sources")
    parser.add_argument("--pseudo", action="store_true", help="write qps.json pseudo catalogs")
    args = parser.parse_args()
    report = {}
    for catalog in CATALOGS:
        keys, dynamic = collect(catalog)
        report[catalog] = {
            "keys": {
                text: {"plural": key.plural, "one": key.one, "html": key.html, "sources": key.sources}
                for text, key in sorted(keys.items())
            },
            "dynamic_calls": dynamic,
        }
        if args.pseudo:
            directory = CATALOGS[catalog][0]
            directory.mkdir(parents=True, exist_ok=True)
            entries: dict[str, object] = {}
            for text, key in keys.items():
                if key.plural:
                    entries[text] = {"one": pseudo(key.one or text), "other": pseudo(text)}
                else:
                    entries[text] = pseudo(text)
            (directory / "qps.json").write_text(
                json.dumps(entries, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        for catalog, data in report.items():
            words = sum(len(text.split()) for text in data["keys"])
            print(f"{catalog}: {len(data['keys'])} keys, about {words} words, "
                  f"{len(data['dynamic_calls'])} calls with a computed key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
