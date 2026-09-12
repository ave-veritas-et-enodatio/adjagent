"""Unit tests for ``liaison_tools/msg-util.py`` — the only sanctioned mutator
of the messages file.

The tool is invoked as a subprocess throughout: its interface *is* the
command line and the exit status, and its callers (both liaison definitions
and ``relay-driver.py``) act on nothing else. The cases below pin the three
properties the adversarial review found missing, each of which failed
silently — the worst shape for a tool whose output is a permanent audit
artifact:

* a failed write must not exit 0 (``LC-S1``): every failure path must reach
  the caller as a non-zero exit with the target left as it was;
* concurrent appends must not lose turns (``LC-S2``): two dispatches sharing
  a topic is the documented continuation mechanism, and an unlocked
  read-modify-write drops all but the last;
* scratch must land where the caller said (``LC-S3``): a scratch file
  carrying the whole transcript must not be routed through system temp on a
  different filesystem.

Plus the ``validate`` verb (``LC-M3``), which exists so a continuation
decision can be made mechanically rather than by inspecting whether a file is
non-empty.

Stdlib only; nothing here touches the network.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_MSG_UTIL = _THIS_DIR.parent / "msg-util.py"

# The read-only-directory cases prove nothing when the caller can write
# anywhere regardless.
_SKIP_AS_ROOT = unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory permissions")


class _MsgUtilCase(unittest.TestCase):
    """A work directory with a system-prompt and instructions file ready."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()
        self.scratch = Path(self._tmp.name) / "scratch"
        self.scratch.mkdir()
        self.sys_prompt = self.work / "sp.txt"
        self.sys_prompt.write_text("system doctrine\n", encoding="utf-8")
        self.instructions = self.work / "in.txt"
        self.instructions.write_text("first instructions\n", encoding="utf-8")
        self.messages = self.work / "messages.json"

    def run_util(self, *args, tmpdir=None, expect=None):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.scratch if tmpdir is None else tmpdir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [str(_MSG_UTIL), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def init_session(self, path=None):
        return self.run_util(
            "init",
            f"--system-prompt={self.sys_prompt}",
            f"--instructions={self.instructions}",
            str(path or self.messages),
            expect=0,
        )

    def content_file(self, name, text):
        path = self.work / name
        path.write_text(text, encoding="utf-8")
        return path

    def turns(self, path=None):
        return json.loads((path or self.messages).read_text(encoding="utf-8"))


class TestInitAndAppend(_MsgUtilCase):
    """The documented shape: a 2-turn array, then one turn per append."""

    def test_init_writes_system_and_user_turns(self):
        self.init_session()
        self.assertEqual(
            self.turns(),
            [
                {"role": "system", "content": "system doctrine\n"},
                {"role": "user", "content": "first instructions\n"},
            ],
        )

    def test_append_maps_agent_role_to_assistant(self):
        self.init_session()
        self.run_util(
            "append", "--role=agent", str(self.messages), str(self.content_file("a.txt", "reply\n")), expect=0
        )
        self.assertEqual(self.turns()[-1], {"role": "assistant", "content": "reply\n"})

    def test_append_of_missing_content_file_exits_nonzero(self):
        self.init_session()
        result = self.run_util("append", "--role=user", str(self.messages), str(self.work / "nope.txt"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content file not found", result.stderr)


class TestFailedWritesExitNonZero(_MsgUtilCase):
    """LC-S1 — a write that does not happen must not report success."""

    def test_init_into_a_nonexistent_directory_exits_nonzero(self):
        target = self.work / "no-such-dir" / "messages.json"
        result = self.run_util(
            "init",
            f"--system-prompt={self.sys_prompt}",
            f"--instructions={self.instructions}",
            str(target),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        self.assertTrue(result.stderr.strip())

    @_SKIP_AS_ROOT
    def test_append_into_a_read_only_directory_exits_nonzero(self):
        locked = self.work / "locked"
        locked.mkdir()
        messages = locked / "messages.json"
        self.init_session(messages)
        content = self.content_file("turn.txt", "a turn that must not vanish\n")
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(locked.chmod, stat.S_IRWXU)

        result = self.run_util("append", "--role=user", str(messages), str(content))

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        # The turn is genuinely absent — the exit code and the file agree.
        self.assertEqual(len(self.turns(messages)), 2)


class TestConcurrentAppends(_MsgUtilCase):
    """LC-S2 — eight appends at once, none lost."""

    def test_eight_concurrent_appends_all_land(self):
        self.init_session()
        env = dict(os.environ)
        env["TMPDIR"] = str(self.scratch)
        procs = []
        for i in range(8):
            content = self.content_file(f"c{i}.txt", f"turn-{i}\n")
            procs.append(
                subprocess.Popen(
                    [str(_MSG_UTIL), "append", "--role=user", str(self.messages), str(content)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
            )
        for proc in procs:
            _, stderr = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, stderr.decode("utf-8", errors="replace"))

        turns = self.turns()
        self.assertEqual(len(turns), 10)  # 2 from init + 8 appends
        self.assertEqual(
            sorted(t["content"] for t in turns if t["content"].startswith("turn-")),
            [f"turn-{i}\n" for i in range(8)],
        )

    def test_no_scratch_is_left_behind_and_the_lock_file_blocks_nothing(self):
        self.init_session()
        self.run_util("append", "--role=user", str(self.messages), str(self.content_file("c.txt", "x\n")), expect=0)
        self.assertEqual(list(self.scratch.iterdir()), [])
        # The lock file is created once and never unlinked — unlinking it would
        # let a waiter hold the lock on an unlinked inode while a newcomer
        # locks a fresh one. Its presence says nothing about whether the lock
        # is held, which is what the second append demonstrates.
        self.run_util("append", "--role=user", str(self.messages), str(self.content_file("d.txt", "y\n")), expect=0)
        self.assertEqual(len(self.turns()), 4)


class TestLockSurvivesAHolderDying(_MsgUtilCase):
    """A lock the kernel releases on process death cannot strand the file.

    Reaching for the primitive directly is the point: nothing the tool exposes
    can hold a lock and then die, and this is the property that removed the
    stale-lock apparatus — a bounded poll, a timeout, and an error message
    telling a human to delete something by hand.
    """

    _HOLD = (
        "import fcntl, os, sys, time;"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT);"
        "fcntl.flock(fd, fcntl.LOCK_EX);"
        "print('held', flush=True);"
        "time.sleep(300)"
    )

    def test_a_killed_holder_does_not_block_the_next_append(self):
        self.init_session()
        holder = subprocess.Popen(
            [sys.executable, "-c", self._HOLD, str(self.messages) + ".lock"],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")

        holder.kill()
        holder.wait(timeout=60)

        self.run_util("append", "--role=user", str(self.messages), str(self.content_file("c.txt", "x\n")), expect=0)
        self.assertEqual(len(self.turns()), 3)


class TestScratchPlacement(_MsgUtilCase):
    """LC-S3 — the mktemp template, asserted rather than inspected.

    A bare ``mktemp`` on macOS resolves through ``confstr
    DARWIN_USER_TEMP_DIR`` and would succeed no matter what ``TMPDIR`` says;
    an unusable ``TMPDIR`` therefore proves the explicit template is in use.
    """

    @_SKIP_AS_ROOT
    def test_append_fails_when_tmpdir_is_unwritable(self):
        self.init_session()
        unwritable = Path(self._tmp.name) / "unwritable"
        unwritable.mkdir()
        unwritable.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(unwritable.chmod, stat.S_IRWXU)

        result = self.run_util(
            "append",
            "--role=user",
            str(self.messages),
            str(self.content_file("c.txt", "x\n")),
            tmpdir=unwritable,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mktemp failed", result.stderr)
        self.assertIn(str(unwritable), result.stderr)
        self.assertEqual(len(self.turns()), 2)

    def test_unset_tmpdir_falls_back_beside_the_messages_file(self):
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        for args in (
            ["init", f"--system-prompt={self.sys_prompt}", f"--instructions={self.instructions}", str(self.messages)],
            ["append", "--role=user", str(self.messages), str(self.content_file("c.txt", "x\n"))],
        ):
            result = subprocess.run(
                [str(_MSG_UTIL), *args], capture_output=True, text=True, env=env, timeout=120, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.turns()), 3)
        # Nothing but the messages file, its lock, and the fixtures: the
        # scratch file was created beside the target and moved into place.
        self.assertEqual(
            sorted(p.name for p in self.work.iterdir()),
            ["c.txt", "in.txt", "messages.json", "messages.json.lock", "sp.txt"],
        )


class TestValidateVerb(_MsgUtilCase):
    """LC-M3 — "exists and non-empty" is not "well-formed"."""

    def test_valid_session_reports_its_turn_count(self):
        self.init_session()
        self.run_util("append", "--role=agent", str(self.messages), str(self.content_file("a.txt", "r\n")), expect=0)
        result = self.run_util("validate", str(self.messages), expect=0)
        self.assertEqual(result.stdout.strip(), "valid: 3 turns")

    def test_missing_file_is_not_valid(self):
        result = self.run_util("validate", str(self.work / "absent.json"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)

    def test_truncated_json_is_not_valid(self):
        self.init_session()
        text = self.messages.read_text(encoding="utf-8")
        self.messages.write_text(text[: len(text) // 2], encoding="utf-8")
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)

    def test_empty_file_is_not_valid(self):
        self.messages.write_text("", encoding="utf-8")
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)

    def test_empty_array_is_not_valid(self):
        self.messages.write_text("[]", encoding="utf-8")
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no turns", result.stderr)

    def test_object_at_top_level_is_not_valid(self):
        self.messages.write_text('{"role": "system", "content": "x"}', encoding="utf-8")
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a JSON array", result.stderr)

    def test_unknown_role_is_not_valid(self):
        self.messages.write_text(
            json.dumps([{"role": "system", "content": "x"}, {"role": "referee", "content": "y"}]),
            encoding="utf-8",
        )
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("turn 1", result.stderr)

    def test_non_string_content_is_not_valid(self):
        self.messages.write_text(
            json.dumps([{"role": "system", "content": "x"}, {"role": "user", "content": {"text": "y"}}]),
            encoding="utf-8",
        )
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-string content", result.stderr)

    def test_session_not_opening_with_a_system_turn_is_not_valid(self):
        self.messages.write_text(json.dumps([{"role": "user", "content": "x"}]), encoding="utf-8")
        result = self.run_util("validate", str(self.messages))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("system turn", result.stderr)


if __name__ == "__main__":
    unittest.main()
