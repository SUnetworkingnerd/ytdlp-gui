"""Builds the GUI's option tree from yt-dlp's own optparse parser.

No Qt imports here, so this module can be tested headless. Because the tree is
generated from ``yt_dlp.options.create_parser()``, every option the installed
yt-dlp version knows about shows up in the GUI, including ones added later.
"""
from __future__ import annotations

import hashlib
import json
import optparse
import os
import shlex
import tempfile
from dataclasses import asdict, dataclass, field

# Options the Download tab controls directly, or that the runner sets itself
# to parse progress. Showing them in the Options tab too would let the two
# places fight each other.
HANDLED_ELSEWHERE = {
    "--live-from-start", "--no-live-from-start",
    "--wait-for-video", "--no-wait-for-video",
    "--paths", "--format", "--preset-alias",
    "--batch-file", "--no-batch-file",
    "--newline", "--progress", "--no-progress", "--progress-template",
    "--console-title", "--quiet", "--no-quiet", "--color",
    "--cookies", "--no-cookies", "--cookies-from-browser", "--no-cookies-from-browser",
}

# Options that print information and skip the download. They make no sense in a
# download queue (the fetch button on the Download tab covers format listing).
NON_DOWNLOAD = {
    "--help", "--version", "--update", "--update-to",
    "--list-extractors", "--extractor-descriptions", "--ap-list-mso",
    "--list-impersonate-targets", "--list-formats", "--list-subs",
    "--list-thumbnails", "--dump-json", "--dump-single-json", "--print",
}

HIDDEN = HANDLED_ELSEWHERE | NON_DOWNLOAD


def split_args(text: str) -> list[str]:
    """Split a user-typed string into argv items (Windows-path friendly)."""
    if os.name != "nt":
        return shlex.split(text)
    parts = shlex.split(text, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p
            for p in parts]


@dataclass
class Opt:
    flag: str                      # canonical long flag, e.g. --proxy
    shorts: list = field(default_factory=list)
    kind: str = "flag"             # flag | value | choice | multi
    metavar: str = ""
    help: str = ""
    nargs: int = 1
    choices: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.flag

    def flags(self) -> list:
        return [self.flag]

    @property
    def title(self) -> str:
        text = ", ".join(self.shorts + [self.flag])
        if self.kind != "flag" and self.metavar:
            text += " " + self.metavar
        return text

    @property
    def searchtext(self) -> str:
        return f"{' '.join(self.shorts + [self.flag])} {self.help}".lower()

    def _value_args(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        if self.nargs > 1:
            return [self.flag, *split_args(text)]
        # --flag=value keeps values that start with "-" (e.g. "-5::2") intact
        return [f"{self.flag}={text}"]

    def to_args(self, value) -> list[str]:
        if self.kind == "flag":
            return [self.flag] if value else []
        if self.kind == "multi":
            out: list[str] = []
            for line in str(value).splitlines():
                out += self._value_args(line)
            return out
        return self._value_args(str(value))


@dataclass
class Pair:
    """A flag plus its --no- counterpart, shown as one three-state control."""
    pos: Opt
    neg: Opt
    kind = "pair"

    @property
    def key(self) -> str:
        return self.pos.flag

    def flags(self) -> list:
        return [self.pos.flag, self.neg.flag]

    @property
    def title(self) -> str:
        return f"{self.pos.flag}  /  {self.neg.flag}"

    @property
    def help(self) -> str:
        return f"{self.pos.help}  |  {self.neg.help}".strip(" |")

    @property
    def searchtext(self) -> str:
        return f"{self.pos.searchtext} {self.neg.searchtext}"

    def to_args(self, value) -> list[str]:
        return {1: [self.pos.flag], 2: [self.neg.flag]}.get(int(value), [])


@dataclass
class Group:
    title: str
    items: list


def _convert(o: optparse.Option):
    if o.help == optparse.SUPPRESS_HELP:          # deprecated / hidden upstream
        return None
    longs = list(o._long_opts)
    if not longs or longs[0] in HIDDEN:
        return None
    help_text = o.help or ""
    if "%default" in help_text:
        help_text = help_text.replace("%default", str(o.default))
    kind, choices = "flag", []
    if o.takes_value():
        if o.type == "choice":
            kind, choices = "choice", list(o.choices or [])
        elif o.action == "append":
            kind = "multi"
        else:
            kind = "value"
    return Opt(
        flag=longs[0],
        shorts=list(o._short_opts),
        kind=kind,
        metavar=o.metavar or (o.dest or "").upper(),
        help=" ".join(help_text.split()),
        nargs=o.nargs or 1,
        choices=choices,
    )


def _pair_up(opts: list[Opt]) -> list:
    by_flag = {o.flag: o for o in opts if o.kind == "flag"}
    consumed, items = set(), []
    for o in opts:
        if o.flag in consumed:
            continue
        if o.kind == "flag" and o.flag.startswith("--no-"):
            pos = by_flag.get("--" + o.flag[5:])
            if pos is not None:
                continue                          # emitted with its positive twin
        if o.kind == "flag":
            neg = by_flag.get("--no-" + o.flag[2:])
            if neg is not None:
                consumed.add(neg.flag)
                items.append(Pair(o, neg))
                continue
        items.append(o)
    return items


class OptionsModel:
    def __init__(self, parser: optparse.OptionParser | None = None, groups: list | None = None):
        if groups is not None:
            self.groups: list[Group] = groups
            return
        if parser is None:
            from yt_dlp.options import create_parser  # raises ImportError if missing
            parser = create_parser()
        self.groups = []
        for g in parser.option_groups:
            opts = [c for c in (_convert(o) for o in g.option_list) if c]
            items = _pair_up(opts)
            if items:
                self.groups.append(Group(g.title or "Other", items))

    # -- cache: lets the GUI start without importing yt-dlp (its ~1000 extractor
    #    modules cost a lot of RAM, and only the separate download processes need them)
    def to_dict(self) -> dict:
        def item(it):
            return ({"pair": [asdict(it.pos), asdict(it.neg)]} if isinstance(it, Pair)
                    else {"opt": asdict(it)})
        return {"groups": [{"title": g.title, "items": [item(i) for i in g.items]}
                           for g in self.groups]}

    @classmethod
    def from_dict(cls, data: dict) -> "OptionsModel":
        def item(d):
            if "pair" in d:
                return Pair(Opt(**d["pair"][0]), Opt(**d["pair"][1]))
            return Opt(**d["opt"])
        return cls(groups=[Group(g["title"], [item(i) for i in g["items"]])
                           for g in data["groups"]])

    def build_args(self, state: dict, only: set | None = None) -> list[str]:
        """state maps option key -> UI value (only non-default entries)."""
        out: list[str] = []
        for g in self.groups:
            for it in g.items:
                if it.key in state and (only is None or only & set(it.flags())):
                    out += it.to_args(state[it.key])
        return out


CACHE_SCHEMA = 1


def ytdlp_version() -> str | None:
    """Installed yt-dlp version, read from package metadata without importing yt-dlp."""
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:  # noqa: BLE001 - missing metadata just means "no cache"
        return None


def cache_file() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ytdlp-gui", "options-model.json")


def _cache_key(version: str) -> str:
    hidden = hashlib.sha1("\n".join(sorted(HIDDEN)).encode()).hexdigest()[:12]
    return f"{CACHE_SCHEMA}|{version}|{hidden}"


def load_model(path: str | None = None, version: str | None = None) -> OptionsModel:
    """OptionsModel from the on-disk cache when yt-dlp is unchanged, else built from
    yt-dlp (which is then imported) and cached. Raises ImportError if yt-dlp is
    missing and there is no usable cache."""
    path = path or cache_file()
    version = version if version is not None else ytdlp_version()
    key = _cache_key(version) if version else None
    if key:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("key") == key:
                return OptionsModel.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    model = OptionsModel()
    if key:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"key": key, **model.to_dict()}, fh)
            os.replace(tmp, path)
        except OSError:
            pass
    return model
