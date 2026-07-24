"""PTY output normalization: ANSI stripping, tqdm \\r handling, ConsoleStream."""
import re

_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_PROGRESS_RE = re.compile(
    r"(\d+%\s*\d+/\d+|\d+it\s*\[|\?it/s|it/s\]|/s\]|/it\]|█|▏|▎|▍|▌|▋|▊|▉)"
)

strip_ansi = lambda t: _ANSI.sub("", t)


class ConsoleStream:
    """Normalises PTY output (ANSI, tqdm carriage-returns) for the web terminal."""

    def __init__(self):
        self._lines: list[str] = []
        self._buf = ""

    @staticmethod
    def _clean(line: str) -> str:
        return strip_ansi(line).rstrip()

    @staticmethod
    def _is_progress(line: str) -> bool:
        s = line.strip()
        return len(s) >= 3 and bool(_PROGRESS_RE.search(s))

    def _append_line(self, line: str, events: list[dict], *, with_newline: bool = True):
        line = self._clean(line)
        if not line.strip():
            return
        suffix = "\n" if with_newline else ""
        self._lines.append(line)
        events.append({"op": "append", "text": line + suffix})

    def _replace_last_progress(self, line: str, events: list[dict], *, with_newline: bool):
        line = self._clean(line)
        if not line.strip():
            return
        suffix = "\n" if with_newline else ""
        if self._lines and self._is_progress(self._lines[-1]):
            self._lines[-1] = line
            events.append({"op": "replace", "text": line + suffix})
        else:
            self._lines.append(line)
            events.append({"op": "append", "text": line + suffix})

    def _on_carriage_return(self, events: list[dict]):
        raw = self._buf
        self._buf = ""
        line = self._clean(raw) if raw else ""
        if not line.strip():
            return
        if self._is_progress(line):
            self._replace_last_progress(line, events, with_newline=False)
        else:
            self._append_line(line, events, with_newline=True)

    def _on_newline(self, events: list[dict]):
        line = self._clean(self._buf) if self._buf else ""
        self._buf = ""
        if not line.strip():
            return
        if self._is_progress(line):
            self._replace_last_progress(line, events, with_newline=True)
        else:
            self._append_line(line, events, with_newline=True)

    def feed(self, text: str) -> list[dict]:
        if not text:
            return []
        events: list[dict] = []
        for ch in text:
            if ch == "\r":
                self._on_carriage_return(events)
            elif ch == "\b":
                if self._buf:
                    self._buf = self._buf[:-1]
            elif ch == "\n":
                self._on_newline(events)
            else:
                self._buf += ch
        if self._buf:
            line = self._clean(self._buf)
            if line.strip():
                if self._is_progress(line):
                    self._replace_last_progress(line, events, with_newline=False)
                else:
                    self._append_line(line, events, with_newline=False)
            # Clear buf after committing so flush()/transcript() don't double-add
            self._buf = ""
        return events

    def flush(self) -> list[dict]:
        events: list[dict] = []
        if self._buf.strip() or self._clean(self._buf).strip():
            self._on_newline(events)
        return events

    def transcript(self) -> str:
        self.flush()
        return "\n".join(self._lines)

    def display_lines(self) -> list[str]:
        return [ln + "\n" for ln in self._lines]


def normalize_transcript(raw: str) -> str:
    if not raw:
        return ""
    stream = ConsoleStream()
    stream.feed(strip_ansi(raw))
    stream.flush()
    return stream.transcript()
