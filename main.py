"""make.fun entry: The 4AM Commit — funniest agent bug-fix message.

Running this module writes one fixed commit message to
``$MAKEFUN_OUTPUT_DIR/entry.txt`` (``/output`` if the variable is
unset or blank). The message is constant, so every run publishes
exactly the same bytes.

The scorer's gate is modest — UTF-8 text, 1 to 200 characters, not
only whitespace — but this writer holds the line to a stricter
standard before it will publish anything:

* exactly one line, no leading or trailing whitespace;
* no control characters at all (that includes tab, newline, and the
  whole C1 range U+0080–U+009F);
* no byte-order mark, no invisible format characters, no
  U+2028/U+2029 separators, and no lone surrogates.

Publication is crash-safe. The payload is staged in a temporary file
created inside the output directory itself — never in ``/tmp``, which
may sit on a different filesystem from a bind-mounted output
directory, so a cross-device rename would fail there — then moved
into place with ``os.replace``. The staged file is fsynced and read
back before the rename, the published file is fsynced, read back
again, and checked for mode ``0644``, and the directory itself is
fsynced after the rename. Temp files abandoned by a run that died
mid-publish are swept up front, and the sweep only ever unlinks
stale files carrying our own temp prefix inside the output
directory; the output directory itself is never removed.
"""

import os
import stat
import tempfile
import time
import unicodedata

COMMIT = (
    "fix: the bug was in the patch I wrote to fix the bug in the "
    "patch I wrote to fix the bug I wrote at 2am"
)

DEFAULT_OUTPUT_DIR = "/output"
TEMP_PREFIX = ".entry-"
TEMP_SUFFIX = ".tmp"
# A temp file older than this can only belong to a dead run: a live
# run holds its temp file for milliseconds. The age guard also stops
# one run from ever sweeping another live run's fresh temp file.
STALE_TEMP_AGE_SECONDS = 60.0

# Unicode general categories rejected outright: Cc covers controls
# (C1 included, plus tab and newline), Cf covers format characters
# (the BOM U+FEFF and zero-width marks among them), Cs covers
# surrogates (unencodable as UTF-8), and Zl/Zp cover the U+2028
# line separator and U+2029 paragraph separator.
FORBIDDEN_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")


def _resolve_output_dir():
    """Pick the output directory from the environment, or the default.

    A missing variable, an empty value, or a whitespace-only value
    all fall back to ``DEFAULT_OUTPUT_DIR``.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    cleaned = raw.strip()
    return cleaned or DEFAULT_OUTPUT_DIR


def _check_shape(text):
    """Shape rules: length 1..200, not blank, no edge whitespace."""
    if not 1 <= len(text) <= 200:
        return False
    if not text.strip():
        return False
    if text != text.strip():
        return False
    return True


def _check_characters(text):
    """Character rules: nothing in a forbidden category, no surrogates."""
    for ch in text:
        if unicodedata.category(ch) in FORBIDDEN_CATEGORIES:
            return False
        if 0xD800 <= ord(ch) <= 0xDFFF:
            return False
    return True


def _line_is_valid(text):
    """Full gate: a string whose shape and characters both pass."""
    if not isinstance(text, str):
        return False
    return _check_shape(text) and _check_characters(text)


def _sweep_abandoned_temps(out_dir):
    """Remove temp files left behind by a crashed earlier run.

    Only files wearing our own prefix and suffix, and only ones
    older than STALE_TEMP_AGE_SECONDS. Best effort throughout: a
    failure here must never stop the run from publishing.
    """
    now = time.time()
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not (name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)):
            continue
        path = os.path.join(out_dir, name)
        try:
            if now - os.path.getmtime(path) < STALE_TEMP_AGE_SECONDS:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            continue
    return removed


def _read_back_and_check(path, expected):
    """Read a file back and insist on exact bytes and a valid line."""
    with open(path, "rb") as f:
        back = f.read()
    if back != expected:
        raise SystemExit("read-back mismatch in %s" % path)
    try:
        text = back.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit("read-back is not valid UTF-8 in %s" % path)
    if not _line_is_valid(text):
        raise SystemExit("read-back fails the gate check in %s" % path)
    return text


def _flush_dir_entry(out_dir):
    """Fsync the directory so the rename survives a crash."""
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _write_staged(fd, tmp_path, payload):
    """Fill the staged temp file, fsync it, and re-check its bytes.

    The descriptor handed in from mkstemp is closed here; the caller
    keeps the path so it can publish the file or tidy it away.
    A short write aborts the run instead of publishing a partial
    line, and the staged bytes are verified before the file is ever
    allowed to become the published entry.
    """
    os.chmod(tmp_path, 0o644)
    with os.fdopen(fd, "wb") as f:
        written = f.write(payload)
        if written != len(payload):
            raise SystemExit(
                "short write: %d of %d bytes" % (written, len(payload))
            )
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp_path, 0o644)

    _read_back_and_check(tmp_path, payload)


def _publish_atomically(out_dir, payload):
    """Stage the payload inside out_dir, then rename it to entry.txt."""
    _sweep_abandoned_temps(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=out_dir
    )
    try:
        _write_staged(fd, tmp_path, payload)

        target = os.path.join(out_dir, "entry.txt")
        os.replace(tmp_path, target)
        os.chmod(target, 0o644)
        _flush_dir_entry(out_dir)

        # Check the published target itself, and its mode.
        _read_back_and_check(target, payload)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        if mode != 0o644:
            raise SystemExit("output mode is not 0644: %o" % mode)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    out_dir = _resolve_output_dir()
    os.makedirs(out_dir, exist_ok=True)

    if not _line_is_valid(COMMIT):
        raise SystemExit("entry line fails the gate check")

    _publish_atomically(out_dir, COMMIT.encode("utf-8"))


if __name__ == "__main__":
    main()
