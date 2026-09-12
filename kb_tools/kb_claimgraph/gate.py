"""Stage G — refresh, then verify, through the consuming project's runner targets.

**This is the loop that works: it exits on a subprocess return code.** There is
no fix loop here and no seat to run one. Every check these targets make compares
one mechanical product against another — a rebuild diffed against disk, a link
against the file it names, an id against the register that mints it — so a red
gate is a defect in this stage's input or in this stage, and neither is repaired
by asking.

The targets are reached by name, never by invoking the modules behind them: the
runner include line is what a consuming project gains the targets through, and
running the modules directly would be the ad-hoc invocation project policy
forbids. Which runner is in front of them is read off the root rather than
assumed.
"""

import shlex
import subprocess
from pathlib import Path

from .. import kb_util
from .report import FAIL, PASS, Finding


def _run(command: str, *, repo_root: Path) -> tuple[int, str]:
    completed = subprocess.run(
        shlex.split(command),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def run(repo_root: Path) -> list[Finding]:
    """``kb-refresh`` then ``kb-verify``. Green or stop, on the return code alone."""
    findings: list[Finding] = []
    runner = kb_util.detected_runner(repo_root)
    if runner is None:
        # The hint functions fall back to a raw module invocation where no
        # runner file stands, and running the modules behind the targets is the
        # ad-hoc invocation project policy forbids. Nothing upstream can reach
        # this — the include line cannot be installed without a runner file —
        # so it is a boundary check rather than a branch with a caller.
        return [Finding(FAIL, "runner", f"no runner file at {repo_root}, so the KB targets do not exist")]
    for command in (kb_util.refresh_cmd(repo_root), kb_util.verify_cmd(repo_root)):
        code, output = _run(command, repo_root=repo_root)
        if code != 0:
            tail = "\n".join(output.splitlines()[-20:])
            findings.append(Finding(FAIL, command, f"exited {code}\n{tail}"))
            return findings
        findings.append(Finding(PASS, command, "exited 0"))
    return findings
