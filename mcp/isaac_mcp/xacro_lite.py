"""A restricted xacro expander. Standard library only; it never runs the ``xacro`` program or any code from a file.

xacro is XML plus ``${expression}``, ``$(command)``, macros, includes and conditions. The real program evaluates the
expressions with Python's ``eval``, which can run anything, so it is never used here. This module reads the same
language with its own parser: an expression is parsed by ``ast`` and only a short list of node kinds, names and
functions is evaluated (arithmetic, comparisons, conditionals, ``math``, a few built-ins, indexing, string methods
that cannot reach attributes, and ``xacro.load_yaml`` on a file inside the project). Everything else is refused, with the
expression named. Files are found inside one project folder only (an approved root, a pack or a snapshot of one); a path
that leaves it, an absolute path, a URL or a backslash is refused. Work is bounded: expression size, macro depth, include
depth, the number of expansions and the number of output elements.

``expand(entry, project, args)`` returns URDF text. Unsupported xacro features are refused by name, never skipped.
"""

from __future__ import annotations

import ast
import fnmatch
import math
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from .project_assets import AssetError, refuse
from .urdf_refs import MAX_XML, SCHEME, Project, _parent, normal_path

MAX_EXPR = 4096
MAX_MACRO_DEPTH = 64
MAX_INCLUDE_DEPTH = 16
MAX_STEPS = 2_000_000  # expression nodes plus elements, one expansion
MAX_OUTPUT = 200_000  # elements in the result
MAX_STRING = 1_000_000
MAX_OUTPUT_BYTES = 64 * 1024**2  # names and values in the result: a URDF the importer can still read
MAX_TEXT_BYTES = 256 * 1024**2  # text substituted, one expansion
MAX_PARSED_BYTES = 256 * 1024**2  # XML files parsed, one expansion
MAX_YAML = 1024**2
_MATH = {name: getattr(math, name) for name in (
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "exp", "log", "log10", "radians", "degrees", "pow",
    "floor", "ceil", "fabs", "copysign", "hypot", "sinh", "cosh", "tanh")}
_CONSTANTS = {"pi": math.pi, "inf": math.inf, "nan": math.nan, "e": math.e, "True": True, "False": False, "None": None}
_BUILTINS = {"abs": abs, "min": min, "max": max, "round": round, "int": int, "float": float, "str": str, "len": len, "bool": bool}
_STRING_METHODS = {"replace", "lower", "upper", "startswith", "endswith", "split", "strip", "lstrip", "rstrip", "join", "index", "count", "find"}
_BINARY = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
_COMPARE = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
            ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
            ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}
_FILE_ROOT = "package://"
_WIDE_FORMAT = re.compile(r"%[-+ #0]*(\*|\d{4,})|%[^%a-zA-Z]*\.(\*|\d{4,})")


class XacroError(AssetError):
    pass


def _fail(message, code="XACRO"):
    raise XacroError(code, message)


def is_xacro_ns(tag) -> bool:
    return isinstance(tag, str) and tag.startswith("{") and tag[1:].split("}")[0].rstrip("/").endswith("xacro")


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


# ---------------------------------------------------------------- a small YAML reader (xacro.load_yaml)

def _scalar(text: str):
    text = text.strip()
    tagged = re.fullmatch(r"!(degrees|radians)\s+(\S+)", text)  # the two unit tags xacro's own YAML reader knows
    if tagged:
        try:
            number = float(tagged.group(2))
        except ValueError:
            _fail(f"YAML {tagged.group(1)} tag on a non-number: {tagged.group(2)[:40]!r}.")
        return math.radians(number) if tagged.group(1) == "degrees" else number
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text[0] in "\"'" and text[-1] == text[0] and len(text) >= 2:
        return text[1:-1]
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in _split_flow(inner)] if inner else []
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def _split_flow(text: str):
    parts, depth, current = [], 0, ""
    for char in text:
        depth += char in "[{"
        depth -= char in "]}"
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p for p in parts if p.strip()]


def load_yaml_text(text: str):
    """The nested maps, lists and scalars that robot parameter files use; anything cleverer is refused."""
    try:
        return _load_yaml_text(text)
    except RecursionError:
        _fail("A YAML file nests too deeply.", "LIMIT")


def _load_yaml_text(text: str):
    if len(text) > MAX_YAML:
        _fail("A YAML file is too large.", "LIMIT")
    lines = []
    for raw in text.splitlines():
        stripped = re.sub(r"\s+#.*$", "", raw.rstrip())
        if stripped.strip() == "" or stripped.lstrip().startswith("#") or stripped.strip() in ("---", "..."):
            continue
        if "\t" in stripped[: len(stripped) - len(stripped.lstrip())]:
            _fail("YAML indented with a tab is not supported.")
        if any(token in stripped for token in ("!!", "&", "*", "|", ">")) and not re.search(r"[\"']", stripped) and re.search(r"(^|\s)(!!|&|\*|[|>]\s*$)", stripped):
            _fail("YAML tags, anchors, aliases and block scalars are not supported.")
        lines.append((len(stripped) - len(stripped.lstrip()), stripped.strip()))

    def block(index, indent):
        if index >= len(lines):
            return None, index
        if lines[index][1].startswith("- "):
            result = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                item = lines[index][1][2:].strip()
                index += 1
                if item == "":
                    value, index = block(index, lines[index][0]) if index < len(lines) and lines[index][0] > indent else (None, index)
                else:
                    value = _scalar(item)
                result.append(value)
            return result, index
        result = {}
        while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
            key, _, rest = lines[index][1].partition(":")
            if _ == "":
                _fail(f"YAML line not understood: {lines[index][1][:60]}")
            key = key.strip().strip("\"'")
            index += 1
            if rest.strip() == "":
                if index < len(lines) and lines[index][0] > indent:
                    result[key], index = block(index, lines[index][0])
                elif index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                    result[key], index = block(index, indent)
                else:
                    result[key] = None
            else:
                result[key] = _scalar(rest)
        return result, index

    value, end = block(0, lines[0][0]) if lines else ({}, 0)
    if end != len(lines):
        _fail("YAML structure not understood (check the indentation).")
    return value


# ---------------------------------------------------------------- values and expressions

def convert_value(value):
    """xacro turns a property written as text into a number or a boolean when it reads as one."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    if text == "true":
        return True
    if text == "false":
        return False
    return value


def render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value) if not value.is_integer() or abs(value) >= 1e16 else repr(value)
    if isinstance(value, (int, str)):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return str(value)
    return str(value)


class Lazy:
    """A property written as text, evaluated on first use in the scope it was written in."""

    def __init__(self, text, scope):
        self.text, self.scope, self.done, self.value = text, scope, False, None


class Scope:
    def __init__(self, parent=None):
        self.parent, self.names = parent, {}

    def find(self, name):
        scope = self
        while scope is not None:
            if name in scope.names:
                return scope
            scope = scope.parent
        return None

    def root(self):
        scope = self
        while scope.parent is not None:
            scope = scope.parent
        return scope


def _string_call_too_long(function, args) -> bool:
    """Would this ``str.join`` or ``str.replace`` build more than MAX_STRING? Asked before it runs: the memory is spent by then."""
    owner, name = getattr(function, "__self__", None), getattr(function, "__name__", "")
    if not isinstance(owner, str):
        return False
    if name == "join" and args and isinstance(args[0], (list, tuple, str)):
        return sum(len(item) if isinstance(item, str) else 0 for item in args[0]) + len(owner) * len(args[0]) > MAX_STRING
    if name == "replace" and len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
        return len(owner) + owner.count(args[0]) * len(args[1]) > MAX_STRING
    return False


class Expander:
    def __init__(self, project: Project, args=None):
        self.project = project
        self.args = {str(k): ("true" if v is True else "false" if v is False else str(v)) for k, v in (args or {}).items()}
        self.declared_args = {}
        self.macros = {}
        self.steps = 0
        self.output = 0
        self.output_bytes = 0
        self.text_bytes = 0
        self.parsed_bytes = 0
        self.parsed = {}
        self.problems = []
        self.warnings = []
        self.read_files = set()

    # -- budget
    def tick(self, count=1):
        self.steps += count
        if self.steps > MAX_STEPS:
            _fail("The xacro file needs too much work to expand (more than two million steps).", "LIMIT")

    # -- files
    def _resolve_file(self, text: str, current: str) -> str:
        text = text.strip()
        if text == "" or SCHEME.match(text) and not text.startswith(_FILE_ROOT):
            _fail(f"A xacro file name must be a path inside the approved folder: {text[:80]!r}.", "UNSUPPORTED_URI")
        if "\\" in text or any(ord(c) < 32 for c in text):
            _fail(f"A backslash or control character in a xacro file name: {text[:80]!r}.", "INVALID_PATH")
        if text.startswith(_FILE_ROOT):
            package, _, rest = text[len(_FILE_ROOT):].partition("/")
            folder = self._package_folder(package)
            return f"{folder}/{rest}" if folder and rest else folder or rest
        if text.startswith("/"):
            _fail(f"An absolute path is not supported in xacro: {text[:80]!r}. Use $(find package) or a relative path.", "INVALID_PATH")
        joined = normal_path(_parent(current), text)
        if joined is None:
            _fail(f"{text[:80]!r} climbs out of the approved folder.", "OUTSIDE_ROOT")
        return joined

    def _package_folder(self, package: str) -> str:
        named = self.project.named_packages().get(package)
        folders = named or self.project.folders_named(package)
        if len(folders) == 1:
            return folders[0]
        if not folders:
            _fail(f"Package {package!r} (from $(find {package})) is not in the approved folder, so it was not looked for anywhere else. "
                  "Approve the folder that holds it, or the package it belongs to.", "PACKAGE_MISSING")
        _fail(f"Package {package!r} is found in several folders in the approved folder: {', '.join(folders[:4])}.", "PACKAGE_AMBIGUOUS")

    def _read(self, rel: str) -> bytes:
        if rel not in self.project.files:
            _fail(f"{rel} is not in the approved folder.", "NOT_FOUND")
        if self.project._unsafe(rel):
            _fail(f"{rel} is behind a symlink, which is refused.", "UNSAFE_ENTRY")
        data = self.project.read(rel)
        if len(data) > MAX_XML:
            _fail(f"{rel} exceeds 64 MiB.", "LIMIT")
        self.read_files.add(rel)
        return data

    def _parse(self, rel: str) -> ET.Element:
        if rel in self.parsed:  # a file included many times is parsed once; nothing changes a parsed tree
            return self.parsed[rel]
        data = self._read(rel)
        self.parsed_bytes += len(data)
        if self.parsed_bytes > MAX_PARSED_BYTES:
            _fail("The xacro files are too large to read (more than 256 MiB in all).", "LIMIT")
        if b"\x00" in data:
            _fail(f"{rel}: only UTF-8 text is supported.", "XACRO_XML")
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            _fail(f"{rel}: DTDs and entities are not supported.", "XACRO_XML")
        try:
            self.parsed[rel] = ET.fromstring(data)
        except ET.ParseError as exc:
            _fail(f"{rel} is not valid XML: {exc}", "XACRO_XML")
        return self.parsed[rel]

    # -- text substitution
    def text(self, text: str, scope: Scope, current: str):
        """Substitute ``${}`` and ``$()`` in *text*. A text that is exactly one ``${expr}`` keeps the value's type."""
        if "$" not in text:
            return text
        self.tick(1 + len(text) // 1024)
        pieces, index, single = [], 0, None
        while index < len(text):
            if text.startswith("$$", index):
                pieces.append("$")
                index += 2
                continue
            if text.startswith("${", index) or text.startswith("$(", index):
                close = "}" if text[index + 1] == "{" else ")"
                end = self._match(text, index + 2, text[index + 1], close)
                body = text[index + 2:end]
                if text[index + 1] == "{":
                    value = self.evaluate(body, scope, current)
                else:
                    value = self.command(body, scope, current)
                if index == 0 and end + 1 == len(text):
                    single = value
                pieces.append(render(value))
                index = end + 1
                continue
            pieces.append(text[index])
            index += 1
        joined = "".join(pieces)
        if len(joined) > MAX_STRING:
            _fail("A xacro string became too long.", "LIMIT")
        self.text_bytes += len(joined)
        if self.text_bytes > MAX_TEXT_BYTES:
            _fail("The xacro file substitutes too much text (more than 256 MiB in all).", "LIMIT")
        return single if single is not None and text.startswith(("${", "$(")) and not isinstance(single, str) else joined

    @staticmethod
    def _match(text, start, opener, closer):
        depth, quote = 1, None
        for index in range(start, len(text)):
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "\"'" and opener == "{":
                quote = char
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return index
        _fail(f"Unbalanced {opener}{closer} in a xacro expression: {text[max(0, start - 2):start + 60]!r}.")

    def command(self, body: str, scope: Scope, current: str):
        words = body.split()
        if not words:
            _fail("An empty $() in a xacro file.")
        name, rest = words[0], body[len(words[0]):].strip()
        if name == "arg":
            return self.arg(rest.split()[0] if rest else "", scope, current)
        if name == "find":
            return _FILE_ROOT + self._package_name(rest.split()[0] if rest else "")
        if name == "optenv":
            parts = rest.split(None, 1)  # never reads the real environment: only the default is returned
            return parts[1] if len(parts) > 1 else ""
        if name == "dirname":
            return _parent(current) or "."
        if name == "eval":
            return self.evaluate(rest, scope, current)
        if name in ("env", "anon", "cwd"):
            _fail(f"$({name} ...) is not supported: it would read the machine, not the approved folder. Pass the value as an argument instead.", "UNSUPPORTED_XACRO")
        _fail(f"$({name} ...) is not supported in xacro here.", "UNSUPPORTED_XACRO")

    def _package_name(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name or ""):
            _fail(f"$(find {name[:40]}) is not a package name.", "INVALID_PATH")
        self._package_folder(name)  # fail now, with the helpful message, when it is not there
        return name

    def arg(self, name: str, scope: Scope, current: str):
        if name in self.args:
            return self.args[name]
        declared = self.declared_args.get(name)
        if declared is not None:
            return self.text(declared[0], declared[1], declared[2]) if isinstance(declared[0], str) else declared[0]
        _fail(f"$(arg {name}) has no value. Give it in xacro_args, for example xacro_args={{\"{name}\": \"...\"}}.", "XACRO_ARG")

    # -- expressions
    def evaluate(self, source: str, scope: Scope, current: str):
        if len(source) > MAX_EXPR:
            _fail("A xacro expression is too long.", "LIMIT")
        source = source.strip()
        if "$(" in source:  # $(arg x) inside an expression is replaced by its text first, as xacro does; once, so a value naming $( cannot loop
            pieces, index = [], 0
            while (start := source.find("$(", index)) >= 0:
                self.tick()
                end = self._match(source, start + 2, "(", ")")
                pieces += [source[index:start], render(self.command(source[start + 2:end], scope, current))]
                index = end + 1
            source = "".join(pieces) + source[index:]
            if len(source) > MAX_EXPR:
                _fail("A xacro expression is too long.", "LIMIT")
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            _fail(f"Not a valid xacro expression: ${{{source[:80]}}} ({exc.msg}).")
        return self._eval(tree.body, scope, current, source, 0)

    def _lookup(self, name, scope, current, source):
        if name in _CONSTANTS:
            return _CONSTANTS[name]
        owner = scope.find(name)
        if owner is not None:
            value = owner.names[name]
            if isinstance(value, Lazy):
                if not value.done:
                    if getattr(value, "busy", False):
                        _fail(f"The property {name!r} refers to itself.", "XACRO_LOOP")
                    value.busy = True
                    try:
                        raw = self.text(value.text, value.scope, current) if isinstance(value.text, str) else value.text
                    finally:
                        value.busy = False
                    value.value, value.done = convert_value(raw), True
                return value.value
            return value
        if name in _BUILTINS:
            return _BUILTINS[name]
        if name == "math":
            return _MATH_NS
        if name == "xacro":
            return _XacroNs(self, current)
        if name in ("load_yaml",):
            return _XacroNs(self, current).load_yaml
        if name in _MATH:
            return _MATH[name]
        _fail(f"The name {name!r} is not defined (in ${{{source[:80]}}}).", "XACRO_NAME")

    def _eval(self, node, scope, current, source, depth):
        self.tick()
        if depth > 64:
            _fail("A xacro expression is nested too deeply.", "LIMIT")
        go = lambda child: self._eval(child, scope, current, source, depth + 1)  # noqa: E731
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                return node.value
            _fail(f"A value of that kind is not supported in ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")
        if isinstance(node, ast.Name):
            return self._lookup(node.id, scope, current, source)
        if isinstance(node, ast.BinOp):
            left, right = go(node.left), go(node.right)
            if isinstance(node.op, ast.Pow):
                if not (isinstance(left, (int, float)) and isinstance(right, (int, float))) or abs(right) > 1000 or abs(left) > 1e12:
                    _fail(f"That power is not allowed in ${{{source[:80]}}}.", "LIMIT")
                return left ** right
            op = _BINARY.get(type(node.op))
            if op is None:
                _fail(f"The operator in ${{{source[:80]}}} is not supported.", "UNSUPPORTED_XACRO")
            if isinstance(node.op, ast.Mult) and isinstance(left, (str, list)) and isinstance(right, int) and len(left) * max(right, 0) > MAX_STRING:
                _fail("A repeated string or list would be too long.", "LIMIT")
            if isinstance(node.op, ast.Mult) and isinstance(right, (str, list)) and isinstance(left, int) and len(right) * max(left, 0) > MAX_STRING:
                _fail("A repeated string or list would be too long.", "LIMIT")
            if isinstance(node.op, ast.Mod) and isinstance(left, str) and _WIDE_FORMAT.search(left):
                _fail("A format width above 999 is not allowed.", "LIMIT")
            try:
                result = op(left, right)
            except (TypeError, ZeroDivisionError, ValueError) as exc:
                _fail(f"${{{source[:80]}}} cannot be computed ({exc}).", "XACRO_EVAL")
            if isinstance(result, (str, list)) and len(result) > MAX_STRING:
                _fail("A xacro string or list became too long.", "LIMIT")
            return result
        if isinstance(node, ast.UnaryOp):
            value = go(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.Not):
                return not value
            _fail(f"The operator in ${{{source[:80]}}} is not supported.", "UNSUPPORTED_XACRO")
        if isinstance(node, ast.BoolOp):
            result = go(node.values[0])
            for child in node.values[1:]:
                if isinstance(node.op, ast.And):
                    result = result and go(child)
                else:
                    result = result or go(child)
            return result
        if isinstance(node, ast.Compare):
            left = go(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = go(comparator)
                fn = _COMPARE.get(type(op))
                if fn is None:
                    _fail(f"The comparison in ${{{source[:80]}}} is not supported.", "UNSUPPORTED_XACRO")
                if not fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return go(node.body) if go(node.test) else go(node.orelse)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [go(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            return {go(k): go(v) for k, v in zip(node.keys, node.values) if k is not None}
        if isinstance(node, ast.Subscript):
            value, index = go(node.value), go(node.slice)
            try:
                return value[index]
            except (TypeError, KeyError, IndexError) as exc:
                _fail(f"${{{source[:80]}}}: cannot index ({exc}).", "XACRO_EVAL")
        if isinstance(node, ast.Attribute):
            owner = go(node.value)
            if node.attr.startswith("_"):
                _fail(f"Attributes that start with an underscore are refused: ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")
            if isinstance(owner, (_MathNs, _XacroNs)):
                return getattr(owner, node.attr) if hasattr(owner, node.attr) and not node.attr.startswith("_") else _fail(
                    f"{node.attr!r} is not available on {type(owner).__name__[1:-2].lower()} here.", "UNSUPPORTED_XACRO")
            if isinstance(owner, str) and node.attr in _STRING_METHODS:
                return getattr(owner, node.attr)
            if isinstance(owner, dict) and node.attr in owner:  # a YAML mapping reads as an object, like xacro's own wrapper
                return owner[node.attr]
            if isinstance(owner, dict) and node.attr in ("get", "keys", "values", "items"):
                return getattr(owner, node.attr)
            _fail(f"The attribute {node.attr!r} is not supported in ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")
        if isinstance(node, ast.Call):
            if node.keywords:
                _fail(f"Keyword arguments are not supported in ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")
            function = go(node.func)
            allowed = (_BUILTINS.values(), _MATH.values())
            ok = any(function in group for group in allowed) or getattr(function, "__self__", None) is not None and (
                isinstance(function.__self__, (str, dict)) or isinstance(function.__self__, _XacroNs))
            if not ok:
                _fail(f"That function is not supported in ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")
            args = [go(a) for a in node.args]
            if _string_call_too_long(function, args):
                _fail("A xacro string would become too long.", "LIMIT")
            try:
                result = function(*args)
            except XacroError:
                raise
            except Exception as exc:  # a bad argument to an allowed function
                _fail(f"${{{source[:80]}}} cannot be computed ({type(exc).__name__}: {exc}).", "XACRO_EVAL")
            if isinstance(result, str) and len(result) > MAX_STRING:
                _fail("A xacro string became too long.", "LIMIT")
            return result
        _fail(f"This kind of expression is not supported: ${{{source[:80]}}}.", "UNSUPPORTED_XACRO")

    # -- the document
    def expand(self, entry: str) -> ET.Element:
        root = self._parse(entry)
        if _local(root.tag) != "robot":
            _fail(f"{entry}: the root element must be robot, not {str(root.tag)[:64]}.", "XACRO_XML")
        scope = Scope()
        out = ET.Element("robot")
        for key, value in root.attrib.items():
            if key.startswith("{") or key.startswith("xmlns"):
                continue
            out.set(key, render(self.text(value, scope, entry)))
        self.children(root, out, scope, entry, 0, 0)
        return out

    def children(self, element, out, scope, current, depth, include_depth):
        for child in list(element):
            self.node(child, out, scope, current, depth, include_depth)

    def _emit(self, parent, element):
        self.output += 1
        if self.output > MAX_OUTPUT:
            _fail("The expanded URDF would have more than 200,000 elements.", "LIMIT")
        parent.append(element)

    def node(self, element, out, scope, current, depth, include_depth):
        self.tick()
        if not isinstance(element.tag, str):
            return  # a comment
        if not is_xacro_ns(element.tag):
            made = ET.Element(element.tag)
            for key, value in element.attrib.items():
                made.set(key, render(self.text(value, scope, current)))
            if element.text and element.text.strip():
                made.text = render(self.text(element.text, scope, current)).strip()
            self.output_bytes += sum(len(v) for v in made.attrib.values()) + len(made.text or "")
            if self.output_bytes > MAX_OUTPUT_BYTES:
                _fail("The expanded URDF would be larger than 64 MiB.", "LIMIT")
            self._emit(out, made)
            for child in list(element):
                self.node(child, made, scope, current, depth, include_depth)
            if element.tail and element.tail.strip():
                made.tail = render(self.text(element.tail, scope, current)).strip()
            return
        name = _local(element.tag)
        handler = getattr(self, f"x_{name}", None)
        if handler is not None:
            handler(element, out, scope, current, depth, include_depth)
        elif name in self.macros:
            self.call(name, element, out, scope, current, depth, include_depth)
        else:
            _fail(f"xacro:{name} is not defined or not supported (in {current}).", "UNSUPPORTED_XACRO")

    # -- directives
    def x_property(self, element, out, scope, current, depth, include_depth):
        name = self.text(element.get("name", ""), scope, current)
        if not name or "value" not in element.attrib and not len(element):
            _fail(f"A xacro:property needs a name and a value (in {current}).")
        target = scope
        where = element.get("scope")
        if where == "global":
            target = scope.root()
        elif where == "parent" and scope.parent is not None:
            target = scope.parent
        if "value" in element.attrib:
            written = element.get("value")
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(name))}(?![A-Za-z0-9_])", written):
                # A redefinition that reads its own old value (value="${x + 1}") is evaluated now, before it replaces the old one.
                done = Lazy(written, scope)
                done.value, done.done = convert_value(self.text(written, scope, current)), True
                target.names[str(name)] = done
            else:
                target.names[str(name)] = Lazy(written, scope)
        else:  # a block property: kept as an element list, inserted by xacro:insert_block
            target.names[str(name)] = ("block", element)

    def x_arg(self, element, out, scope, current, depth, include_depth):
        name = element.get("name")
        if not name:
            _fail(f"A xacro:arg needs a name (in {current}).")
        if name not in self.declared_args:
            self.declared_args[name] = (element.get("default", ""), scope, current)

    def x_macro(self, element, out, scope, current, depth, include_depth):
        name = element.get("name")
        if not name:
            _fail(f"A xacro:macro needs a name (in {current}).")
        params = []
        # A parameter may hold spaces inside quotes (xyz:='0 0 0'), which a plain split would cut in two.
        for word in re.findall(r"""(?:[^\s'"]|'[^']*'|"[^"]*")+""", element.get("params") or ""):
            if word.startswith("**"):
                params.append((word[2:], "rest", None))  # takes all the remaining child elements as one block
            elif word.startswith("*"):
                params.append((word[1:], "block", None))
            elif ":=" in word:
                key, _, default = word.partition(":=")
                if len(default) >= 2 and default[0] == default[-1] and default[0] in "'\"":
                    default = default[1:-1]
                params.append((key, "default", default))
            else:
                params.append((word, "required", None))
        self.macros[name.split(":")[-1]] = (element, params, current)

    def x_include(self, element, out, scope, current, depth, include_depth):
        if include_depth >= MAX_INCLUDE_DEPTH:
            _fail("xacro files include each other too deeply.", "LIMIT")
        filename = element.get("filename")
        if filename is None:
            _fail(f"A xacro:include needs a filename (in {current}).")
        text = self.text(filename, scope, current)
        text = render(text)
        if text.startswith("package://"):
            pass
        pattern = self._resolve_file(text, current)
        if "*" in pattern or "?" in pattern:
            matches = sorted(f for f in self.project.files if fnmatch.fnmatchcase(f, pattern) and PurePosixPath(f).suffix.lower() in (".xacro", ".xml", ".urdf"))
            targets = matches
        else:
            targets = [pattern]
        for target in targets:
            root = self._parse(target)
            self.children(root, out, scope, target, depth, include_depth + 1)

    def x_if(self, element, out, scope, current, depth, include_depth):
        if self._truth(element, scope, current):
            self.children(element, out, scope, current, depth, include_depth)

    def x_unless(self, element, out, scope, current, depth, include_depth):
        if not self._truth(element, scope, current):
            self.children(element, out, scope, current, depth, include_depth)

    def _truth(self, element, scope, current):
        if "value" not in element.attrib:
            _fail(f"xacro:{_local(element.tag)} needs a value (in {current}).")
        value = self.text(element.get("value"), scope, current)
        if isinstance(value, str):
            value = convert_value(value.strip())
        if isinstance(value, str):
            _fail(f"A xacro condition must be true, false or a number, not {value[:40]!r} (in {current}).", "XACRO_EVAL")
        return bool(value)

    def x_insert_block(self, element, out, scope, current, depth, include_depth):
        name = self.text(element.get("name", ""), scope, current)
        owner = scope.find(str(name))
        block = owner.names[str(name)] if owner is not None else None
        if not (isinstance(block, tuple) and block and block[0] == "block"):
            _fail(f"xacro:insert_block {name!r}: no such block parameter (in {current}).", "XACRO_NAME")
        holder = block[1]
        if isinstance(holder, ET.Element) and _local(holder.tag) == "property":
            for child in list(holder):
                self.node(child, out, scope, current, depth, include_depth)
        else:
            self.node(holder, out, scope, current, depth, include_depth)

    def call(self, name, element, out, scope, current, depth, include_depth):
        if depth >= MAX_MACRO_DEPTH:
            _fail(f"xacro:{name} calls nest more than {MAX_MACRO_DEPTH} deep.", "LIMIT")
        macro, params, defined_in = self.macros[name]
        local = Scope(scope)
        blocks = [child for child in list(element) if isinstance(child.tag, str)]
        used = set()
        for key, kind, default in params:
            if kind == "rest":
                holder = ET.Element("property")  # inserted by xacro:insert_block as its children
                holder.extend(blocks)
                blocks = []
                local.names[key] = ("block", holder)
                continue
            if kind == "block":
                if not blocks:
                    _fail(f"xacro:{name} needs a child element for its block parameter *{key} (in {current}).")
                local.names[key] = ("block", blocks.pop(0))
                continue
            if key in element.attrib:
                local.names[key] = Lazy(element.get(key), scope)
                used.add(key)
            elif kind == "default":
                if default.startswith("^"):
                    rest = default[1:]
                    owner = scope.find(key)
                    if owner is not None:
                        local.names[key] = owner.names[key]
                    elif rest.startswith("|"):
                        local.names[key] = Lazy(rest[1:], scope)
                    else:
                        _fail(f"xacro:{name}: the parameter {key!r} is required from the surrounding scope (in {current}).")
                else:
                    local.names[key] = Lazy(default, local)
            else:
                _fail(f"xacro:{name} needs the parameter {key!r} (in {current}).")
        unknown = [k for k in element.attrib if k not in {p[0] for p in params}]
        if unknown:
            _fail(f"xacro:{name} has no parameter {unknown[0]!r} (in {current}).")
        self.children(macro, out, local, defined_in, depth + 1, include_depth)

    def x_element(self, *_):
        _fail("xacro:element is not supported here.", "UNSUPPORTED_XACRO")

    def x_attribute(self, *_):
        _fail("xacro:attribute is not supported here.", "UNSUPPORTED_XACRO")


class _MathNs:
    pi, e, inf, nan = math.pi, math.e, math.inf, math.nan


for _name, _fn in _MATH.items():
    setattr(_MathNs, _name, staticmethod(_fn))
_MATH_NS = _MathNs()


class _XacroNs:
    def __init__(self, expander, current):
        self._expander, self._current = expander, current

    def load_yaml(self, filename):
        expander = self._expander
        rel = expander._resolve_file(str(filename), self._current)
        text = expander._read(rel).decode("utf-8", "replace")
        return load_yaml_text(text)


def _missing_args(entry: str, project: Project, args) -> list:
    """Every ``$(arg)`` the file needs that was not given, found by expanding with empty placeholders (bounded)."""
    given, missing = dict(args or {}), []
    for _ in range(25):
        try:
            Expander(project, given).expand(entry)
            return missing
        except XacroError as exc:
            if exc.code != "XACRO_ARG":
                return missing
            name = re.search(r"\$\(arg ([^)]*)\)", str(exc))
            if not name or name.group(1) in given:
                return missing
            missing.append(name.group(1))
            given[name.group(1)] = ""
    return missing


def expand(entry: str, project: Project, args=None) -> dict:
    """Expand the xacro file *entry* (a path in *project*): ``{"urdf": text, "files": [...], "args": {...}}``. Any failure is a XacroError."""
    try:
        return _expand(entry, project, args)
    except XacroError:
        raise
    except RecursionError:
        _fail(f"{entry} nests too deeply.", "LIMIT")
    except (TypeError, ValueError, OverflowError, AttributeError, KeyError, IndexError) as exc:
        _fail(f"{entry} cannot be expanded ({type(exc).__name__}: {str(exc)[:200]}).", "XACRO_EVAL")


def _expand(entry: str, project: Project, args=None) -> dict:
    expander = Expander(project, args)
    try:
        root = expander.expand(entry)
    except XacroError as exc:
        if exc.code != "XACRO_ARG":
            raise
        needed = _missing_args(entry, project, args) or [re.search(r"\$\(arg ([^)]*)\)", str(exc)).group(1)]
        shown = ", ".join(f'"{name}": "..."' for name in needed)
        raise XacroError("XACRO_ARG", f"This xacro file needs values for its $(arg ...): {', '.join(needed)}. Give them in xacro_args, "
                                      f"for example xacro_args={{{shown}}}.", needs_args=needed) from None
    ET.indent(root)
    text = ET.tostring(root, encoding="unicode")
    text = "<?xml version=\"1.0\"?>\n" + text + "\n"
    return dict(urdf=text, args=dict(expander.args), declared_args=sorted(expander.declared_args), macros=sorted(expander.macros),
                files=sorted(expander.read_files))
