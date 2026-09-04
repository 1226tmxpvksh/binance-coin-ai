"""
디스코드 웹훅 메시지 1회 발송 진단 스크립트.

btc_live_trading/.env 의 DISCORD_WEBHOOK_URL 로 테스트 embed 1건을 보낸다.

사용 (서버 bot2):
  cd ~/Coin
  source ~/venv/bin/activate
  python scripts/test_discord_send.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "btc_live_trading"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LIVE / ".env", override=True)

from discord_notifier import DiscordNotifier, _mask_webhook_url  # noqa: E402

KST = timezone(timedelta(hours=9))


def main() -> int:
    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    print("=== test_discord_send.py ===")
    print(f"시간: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f".env : {LIVE / '.env'}")
    print(f"NOTIFY_CHANNEL: {(os.getenv('NOTIFY_CHANNEL') or 'discord').strip() or 'discord'}")
    print(f"DISCORD_WEBHOOK_URL: {_mask_webhook_url(url)}")
    print()

    if not url:
        print("결과: 스킵 (DISCORD_WEBHOOK_URL 없음) - 예외 없이 종료")
        return 0

    notifier = DiscordNotifier(webhook_url=url, enabled=True)
    ok = notifier.send_message(
        "Coin bot Discord 테스트",
        "디스코드 웹훅 테스트 메시지입니다.\n"
        f"시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}",
    )
    print(f"=== DiscordNotifier.send_message() ===")
    print(f"  결과: {'성공' if ok else '실패'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
