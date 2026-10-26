"""notify_service_stop: 웹훅이 없으면 네트워크 없이 종료코드 0."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import notify_service_stop as notify  # noqa: E402


class NotifyServiceStopTest(unittest.TestCase):
    def test_missing_webhook_exits_quietly(self) -> None:
        missing = Path("/tmp/coin-no-such-env")
        empty = self._empty_env()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = notify.notify(["notify_service_stop.py", "coinbot"], {"SERVICE_RESULT": "oom-kill"}, env_path=missing)
            code_empty = notify.notify(["notify_service_stop.py", "coinbot"], {"SERVICE_RESULT": "success"}, env_path=empty)
        self.assertEqual(code, 0)
        self.assertEqual(code_empty, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_classify_and_colors(self) -> None:
        now = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
        normal = notify.build_payload("coinbot", "success", "exited", "0", now=now, host="vultr")
        crash = notify.build_payload("coinbot", "exit-code", "exited", "1", now=now, host="vultr")
        oom = notify.build_payload("coinbot", "oom-kill", "killed", "9", now=now, host="vultr")
        self.assertFalse(notify.classify("success")[0])
        self.assertIn("정상 종료", normal["embeds"][0]["title"])
        self.assertEqual(normal["embeds"][0]["color"], notify.COLOR_NORMAL)
        self.assertIn("비정상 종료", crash["embeds"][0]["title"])
        self.assertIn("exit-code", crash["embeds"][0]["title"])
        self.assertEqual(crash["embeds"][0]["color"], notify.COLOR_ABNORMAL)
        self.assertIn("oom-kill", oom["embeds"][0]["title"])
        self.assertEqual(oom["embeds"][0]["color"], notify.COLOR_ABNORMAL)

    def test_send_failure_still_exits_zero_without_url(self) -> None:
        env = self._empty_env()
        env.write_text("DISCORD_WEBHOOK_URL=https://discord.example/api/webhooks/1/secret\n", encoding="utf-8")
        seen: list[str] = []

        def boom(url: str, **_kwargs: object) -> None:
            seen.append(url)
            raise RuntimeError(f"post failed {url}")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = notify.notify(
                ["notify_service_stop.py", "coinbot"],
                {"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "9"},
                env_path=env,
                post=boom,
                host="vultr",
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("discord.example", out.getvalue() + err.getvalue())

    def _empty_env(self) -> Path:
        import tempfile

        path = Path(tempfile.mkdtemp()) / "env"
        path.write_text("OTHER=1\n", encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
