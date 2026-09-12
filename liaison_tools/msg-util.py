#!/usr/bin/env python3
"""msg-util.py — manage the OpenAI-compatible messages JSON array used by the
guest liaison (mad-guest-liaison and guest-liaison). Provides deterministic
initialization and append operations so the liaison never has to improvise JSON
manipulation inline.

Modes:
  init   --system-prompt=<system-prompt-file> --instructions=<instructions-file> <messages.json>
    Creates <messages.json> as a 2-element array: one system turn holding the
    system-prompt file's content and one user turn holding the instructions
    file's content. Overwrites if the file exists.

  append --role=<user|agent> <messages.json> <content-file>
    Appends one turn whose content is the full contents of <content-file>.
    Role 'agent' maps to API role 'assistant'; 'user' passes through.

  validate <messages.json>
    Exits 0 if the file is a well-formed session (a JSON array of turns, each
    with a known role and string content, opening with the system turn) and
    prints its turn count; otherwise names the defect and exits non-zero.
    "Exists and non-empty" is not "well-formed" — this is the mechanical check
    a continuation decision can be made on.

The exit status is binary: 0 on success, 1 on every failure. Diagnostics go to
stderr; stdout carries nothing but `validate`'s turn count.

INVARIANT: stdlib only. No third-party dependencies. Ever.

Design notes:
- Message bodies are read from files, never from argv: they are large and carry
  shell-significant characters, both of which corrupt or truncate silently.
- All text is read and written as UTF-8 explicitly, so the three modes cannot
  disagree about whether the same file is well-formed text.
- A mutation is serialized by an exclusive `flock` and lands by writing a
  scratch file and replacing the target with it, so a reader holding no lock
  observes the pre- or post-mutation file in full, never a partial write.
"""

import errno
import fcntl
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

THIS_SCRIPT = Path(__file__).name

# Lock wait bound. Long enough to cover a slow interpreter start under
# contention, short enough that a wedged holder is reported rather than waited
# out. The kernel releases the lock when its holder dies, so reaching this
# timeout means a live process is holding it — there is nothing to clean up by
# hand.
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.1

# The roles legal *inside* the file. 'agent' is a CLI-only spelling and never
# appears here; MAP_ROLE is the only place the two vocabularies meet.
FILE_ROLES = ("system", "user", "assistant")
MAP_ROLE = {"user": "user", "agent": "assistant"}


class UsageError(Exception):
    """A malformed command line. Reported with the usage block."""


class MsgUtilError(Exception):
    """A failure that is not the command line's shape. Reported alone."""


def usage_text() -> str:
    return (
        "usage:\n"
        f"  {THIS_SCRIPT} init     --system-prompt=<system-prompt-file>"
        " --instructions=<instructions-file> <messages.json>\n"
        f"  {THIS_SCRIPT} append   --role=<user|agent> <messages.json> <content-file>\n"
        f"  {THIS_SCRIPT} validate <messages.json>\n"
    )


def scratch_dir(fallback: Path) -> Path:
    """TMPDIR when the caller set one, otherwise the messages file's own
    directory — which is always on the target's filesystem, and so keeps the
    closing replace atomic. A scratch file here carries the ENTIRE messages
    array, which is why it never goes to system-wide temp.
    """
    tmpdir = os.environ.get("TMPDIR")
    return Path(tmpdir) if tmpdir else fallback


@contextmanager
def exclusive_lock(target: Path) -> Iterator[None]:
    """Serialize the read-modify-write on `target` for the block's duration.

    `flock` on a sibling lock file, held for as long as the descriptor is open.
    The kernel releases it when this process exits by any route, crash
    included, so a dead mutator strands nothing and there is no stale lock for
    an operator to remove.

    The lock file itself is created once and never unlinked: unlinking it would
    let a waiter acquire the lock on an unlinked inode while a newcomer creates
    and locks a fresh one, putting two mutators inside the same critical
    section. It is empty, and its presence means nothing about whether the lock
    is held.
    """
    lock_path = target.with_name(target.name + ".lock")
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        # Not contention: a missing or unwritable parent directory, reported
        # now rather than ten seconds from now with the wrong cause.
        raise MsgUtilError(
            f"cannot create lock {lock_path} — is its directory present and writable? ({e.strerror})"
        ) from e
    try:
        _acquire(fd, lock_path)
        yield
    finally:
        os.close(fd)


def _acquire(fd: int, lock_path: Path) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                # A filesystem that cannot lock is not a filesystem to wait on.
                raise MsgUtilError(f"cannot lock {lock_path}: {e.strerror}") from e
            if time.monotonic() >= deadline:
                raise MsgUtilError(
                    f"timed out after {LOCK_TIMEOUT_SECONDS:g}s waiting for {lock_path}; "
                    f"another {THIS_SCRIPT} holds it"
                ) from e
            time.sleep(LOCK_POLL_SECONDS)


def read_text(path: Path, *, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise MsgUtilError(f"{what} {path} is not valid UTF-8 text ({e})") from e
    except OSError as e:
        raise MsgUtilError(f"{what} {path} could not be read ({e.strerror})") from e


def write_messages(target: Path, turns: list) -> None:
    """Serialize `turns` to a scratch file and replace `target` with it.

    Caller holds the lock. The replace is atomic within a filesystem, so a
    concurrent reader sees one whole version or the other; a scratch directory
    on a *different* filesystem is refused rather than silently degraded to a
    copy-plus-unlink that can leave a full transcript behind when interrupted.
    """
    directory = scratch_dir(target.parent)
    try:
        fd, name = tempfile.mkstemp(prefix="msg-util.", dir=str(directory))
    except OSError as e:
        raise MsgUtilError(f"mktemp failed in {directory} ({e.strerror})") from e
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(turns, f, indent=2)
        os.replace(tmp, target)
    except OSError as e:
        raise MsgUtilError(f"could not write {target} ({e.strerror})") from e
    finally:
        # Fires on the error paths and on an unhandled exception alike; a no-op
        # once the replace above has consumed the scratch file.
        tmp.unlink(missing_ok=True)


def load_turns(path: Path) -> list:
    """Read an existing messages file for mutation."""
    try:
        turns = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise MsgUtilError(f"{path}: not valid JSON ({e})") from e
    except (OSError, ValueError) as e:
        raise MsgUtilError(f"{path}: could not be read ({e})") from e
    if not isinstance(turns, list):
        raise MsgUtilError(f"{path}: top level is {type(turns).__name__}, not a JSON array of turns")
    return turns


def mode_init(args: list[str]) -> None:
    sys_prompt = instructions = msgs_file = ""
    for arg in args:
        if arg.startswith("--system-prompt="):
            sys_prompt = arg.partition("=")[2]
        elif arg.startswith("--instructions="):
            instructions = arg.partition("=")[2]
        elif arg in ("--system-prompt", "--instructions"):
            raise UsageError(f"{arg} must use '=<value>' form")
        elif arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        elif not msgs_file:
            msgs_file = arg
        else:
            raise UsageError(f"unexpected positional: {arg}")

    if not msgs_file:
        raise UsageError("init requires <messages.json>")
    if not sys_prompt:
        raise UsageError("init requires --system-prompt=<system-prompt-file>")
    if not instructions:
        raise UsageError("init requires --instructions=<instructions-file>")
    if not Path(sys_prompt).is_file():
        raise UsageError(f"sys-prompt file {sys_prompt} does not exist.")
    if not Path(instructions).is_file():
        raise UsageError(f"instructions file {instructions} does not exist.")

    target = Path(msgs_file)
    with exclusive_lock(target):
        write_messages(
            target,
            [
                {"role": "system", "content": read_text(Path(sys_prompt), what="sys-prompt file")},
                {"role": "user", "content": read_text(Path(instructions), what="instructions file")},
            ],
        )


def mode_append(args: list[str]) -> None:
    role_in = msgs_file = content_file = ""
    for arg in args:
        if arg.startswith("--role="):
            role_in = arg.partition("=")[2]
        elif arg == "--role":
            raise UsageError("--role must use '=<value>' form")
        elif arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        elif not msgs_file:
            msgs_file = arg
        elif not content_file:
            content_file = arg
        else:
            raise UsageError(f"unexpected positional: {arg}")

    if not role_in:
        raise UsageError("append requires --role=<user|agent>")
    if not msgs_file:
        raise UsageError("append requires <messages.json>")
    if not content_file:
        raise UsageError("append requires <content-file>")
    if not Path(msgs_file).is_file():
        raise MsgUtilError(f"messages file not found: {msgs_file}")
    if not Path(content_file).is_file():
        raise MsgUtilError(f"content file not found: {content_file}")
    if role_in not in MAP_ROLE:
        raise MsgUtilError(f"role must be 'user' or 'agent' (got '{role_in}')")

    target = Path(msgs_file)
    # The lock spans the whole read-modify-write: the load below, the replace
    # at the end. Unlocked, two appends racing each read the same array and the
    # later write silently discards the earlier turn — on a file documented as
    # a permanent audit artifact.
    with exclusive_lock(target):
        turns = load_turns(target)
        turns.append({"role": MAP_ROLE[role_in], "content": read_text(Path(content_file), what="content file")})
        write_messages(target, turns)


def mode_validate(args: list[str]) -> None:
    msgs_file = ""
    for arg in args:
        if arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        elif not msgs_file:
            msgs_file = arg
        else:
            raise UsageError(f"unexpected positional: {arg}")

    if not msgs_file:
        raise UsageError("validate requires <messages.json>")
    if not Path(msgs_file).is_file():
        raise MsgUtilError(f"messages file not found: {msgs_file}")

    # No lock: the replace a mutation ends with is atomic within a filesystem,
    # so a concurrent append is observed either not at all or completely.
    print(f"valid: {len(validated_turns(msgs_file))} turns")


def validated_turns(msgs_file: str) -> list:
    """The turns of a well-formed session, or a MsgUtilError naming the defect."""

    def bad(what: str):
        return MsgUtilError(f"{msgs_file}: {what}")

    try:
        turns = json.loads(Path(msgs_file).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise bad(f"not valid JSON ({e})") from e
    except (OSError, ValueError) as e:
        raise bad(f"could not be read ({e})") from e

    if not isinstance(turns, list):
        raise bad(f"top level is {type(turns).__name__}, not a JSON array of turns")
    if not turns:
        raise bad("no turns — an initialized session holds at least a system turn")
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise bad(f"turn {i} is {type(turn).__name__}, not an object")
        if turn.get("role") not in FILE_ROLES:
            raise bad(f"turn {i} has role {turn.get('role')!r}; expected one of {list(FILE_ROLES)}")
        if not isinstance(turn.get("content"), str):
            raise bad(f"turn {i} has non-string content ({type(turn.get('content')).__name__})")
    if turns[0]["role"] != "system":
        raise bad(f"turn 0 has role {turns[0]['role']!r}; an initialized session opens with the system turn")
    return turns


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            raise UsageError("missing mode")
        mode, rest = args[0], args[1:]
        if mode == "init":
            mode_init(rest)
        elif mode == "append":
            mode_append(rest)
        elif mode == "validate":
            mode_validate(rest)
        elif mode in ("-h", "--help", "help"):
            raise UsageError("")
        else:
            raise UsageError(f"unknown mode: {mode}")
    except UsageError as e:
        if str(e):
            sys.stderr.write(f"error: {e}\n")
        sys.stderr.write(usage_text())
        return 1
    except MsgUtilError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
