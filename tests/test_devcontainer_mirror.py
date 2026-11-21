"""The devcontainer here is MiOS's one dev image, byte for byte.

Every MiOS repository carries the same ``.devcontainer/Containerfile``: the MiOS
image plus account and container wiring, edited in MiOS only and mirrored into
each satellite (mios-bootstrap, -dev-loop, this repo). A mirror that drifts
silently forks the image, so this compares the local copy against the first
MiOS copy it can reach: ``MIOS_CONTAINERFILE`` (a path), a sibling checkout
(``../MiOS``), or MiOS main on GitHub. It skips, saying why, only when none is
reachable; a missing local copy is a failure.

devcontainer.json is not byte-identical across satellites (name, workspace), but
the values MiOS projects from its SSOT (ports, the AI endpoint, host requirements,
the build identity) must match MiOS's.

Controls: ``test_mirror_matches_mios`` is the positive side; ``test_one_changed_byte_is_named``
plants one flipped byte in a scratch copy and requires the comparison to name the file,
the reference and the byte. Run: ``python3 -m pytest tests/test_devcontainer_mirror.py``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIRROR = Path(".devcontainer") / "Containerfile"
LOCAL = ROOT / MIRROR
REMOTE = "https://raw.githubusercontent.com/mios-dev/MiOS/main/.devcontainer/Containerfile"
REMOTE_JSON = "https://raw.githubusercontent.com/mios-dev/MiOS/main/.devcontainer/devcontainer.json"
# What every genuine copy contains; an HTML error page answering the fetch does not.
SIGNATURE = b"FROM ${MIOS_IMAGE_REF}"
PROJECTED = ("forwardPorts", "remoteUser", "remoteEnv", "hostRequirements")


def _candidates() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    env = os.environ.get("MIOS_CONTAINERFILE")
    if env:
        out.append(("MIOS_CONTAINERFILE", Path(env)))
    out.append(("sibling checkout ../MiOS", ROOT.parent / "MiOS" / MIRROR))
    return out


def _fetch(url: str) -> bytes | str:
    """The body, or the reason it could not be fetched."""
    try:
        p = subprocess.run(
            ["curl", "-fsSL", "--compressed", "--retry", "3", "--retry-delay", "2", "--max-time", "60", url],
            capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{url}: curl did not run: {exc}"
    if p.returncode != 0:
        return f"{url}: curl exit {p.returncode}: {p.stderr.decode(errors='replace').strip()}"
    return p.stdout


def reference() -> tuple[bytes, str] | list[str]:
    """MiOS's bytes and where they came from, or the reasons nothing was reachable."""
    why: list[str] = []
    for label, path in _candidates():
        if not path.is_file():
            why.append(f"{label}: {path} is not a file")
            continue
        data = path.read_bytes()
        if SIGNATURE not in data:
            why.append(f"{label}: {path} is not the MiOS Containerfile (no '{SIGNATURE.decode()}')")
            continue
        return data, f"{label} ({path})"
    body = _fetch(REMOTE)
    if isinstance(body, str):
        why.append(body)
        return why
    if SIGNATURE not in body:
        why.append(f"{REMOTE}: {len(body)} bytes that are not the MiOS Containerfile")
        return why
    return body, REMOTE


def compare(local: bytes, ref: bytes, ref_label: str) -> str | None:
    """None when identical; otherwise a message naming the mirror and the first difference."""
    local = local.replace(b"\r\n", b"\n")
    ref = ref.replace(b"\r\n", b"\n")
    if local == ref:
        return None
    n = min(len(local), len(ref))
    off = next((i for i in range(n) if local[i] != ref[i]), n)
    line = local[:off].count(b"\n") + 1
    return (
        f"{MIRROR} differs from MiOS ({ref_label}): first difference at byte {off}, "
        f"line {line} (local {len(local)} bytes, MiOS {len(ref)} bytes). "
        f"Edit the file in MiOS and mirror it; never edit the mirror."
    )


def _jsonc(text: str) -> dict:
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


class DevcontainerMirror(unittest.TestCase):
    def test_local_copy_exists_and_is_the_one_image(self) -> None:
        self.assertTrue(LOCAL.is_file(), f"missing: {MIRROR} (the MiOS dev image mirror)")
        data = LOCAL.read_bytes().replace(b"\r\n", b"\n")
        self.assertIn(SIGNATURE, data, f"{MIRROR} is not the MiOS dev Containerfile")
        self.assertIn(b"edit this source only.", data, f"{MIRROR} lacks the mirror header; it is not MiOS's Containerfile")

    def test_mirror_matches_mios(self) -> None:
        self.assertTrue(LOCAL.is_file(), f"missing: {MIRROR}")
        ref = reference()
        if isinstance(ref, list):
            raise unittest.SkipTest("no MiOS Containerfile reachable, so the mirror was NOT checked:\n  " + "\n  ".join(ref))
        data, label = ref
        print(f"[devcontainer-mirror] reference: {label}", file=sys.stderr)
        report = compare(LOCAL.read_bytes(), data, label)
        self.assertIsNone(report, report)

    def test_one_changed_byte_is_named(self) -> None:
        self.assertTrue(LOCAL.is_file(), f"missing: {MIRROR}")
        good = LOCAL.read_bytes().replace(b"\r\n", b"\n")
        i = len(good) // 2  # any one byte, so this does not depend on the file's text
        bad = good[:i] + bytes([good[i] ^ 0x01]) + good[i + 1 :]
        self.assertIsNone(compare(good, good, "self"))
        report = compare(bad, good, "scratch")
        self.assertIsNotNone(report)
        assert report is not None
        self.assertIn(str(MIRROR), report)
        self.assertIn(f"byte {i}", report)
        self.assertIsNotNone(compare(good + b"\n", good, "scratch"))


class DevcontainerProjection(unittest.TestCase):
    """The values MiOS projects from mios.toml into its devcontainer.json must be the same here."""

    def _mios(self) -> dict:
        for _, path in _candidates():
            dc = path.parent / "devcontainer.json"
            if dc.is_file():
                return _jsonc(dc.read_text(encoding="utf-8"))
        body = _fetch(REMOTE_JSON)
        if isinstance(body, str):
            raise unittest.SkipTest("no MiOS devcontainer.json reachable, so the projection was NOT checked: " + body)
        return _jsonc(body.decode("utf-8"))

    def test_local_definition_builds_the_mirror(self) -> None:
        mine = _jsonc((ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))
        self.assertNotIn("image", mine, "devcontainer.json must build the MiOS Containerfile, not pull a third-party image")
        self.assertEqual("Containerfile", (mine.get("build") or {}).get("dockerfile"))

    def test_projected_values_match_mios(self) -> None:
        mine = _jsonc((ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))
        mios = self._mios()
        for key in PROJECTED:
            self.assertEqual(mine.get(key), mios.get(key), f"devcontainer.json {key} differs from MiOS's")
        self.assertEqual(
            ((mine.get("build") or {}).get("args") or {}).get("MIOS_IDENTITY_USERNAME"),
            ((mios.get("build") or {}).get("args") or {}).get("MIOS_IDENTITY_USERNAME"),
            "devcontainer.json build.args.MIOS_IDENTITY_USERNAME differs from MiOS's",
        )
        self.assertEqual(
            (mine.get("containerEnv") or {}).get("MIOS_AI_ENDPOINT"),
            (mios.get("containerEnv") or {}).get("MIOS_AI_ENDPOINT"),
            "devcontainer.json containerEnv.MIOS_AI_ENDPOINT differs from MiOS's",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
