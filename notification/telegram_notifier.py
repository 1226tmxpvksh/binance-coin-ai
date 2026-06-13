"""Telegram Bot API 알림 모듈."""

from __future__ import annotations

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled

    def send_message(self, title: str, description: str) -> bool:
        if not self.enabled:
            return False
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot token 또는 chat id가 없어 알림을 건너뜁니다.")
            return False
        text = f"{title}\n{description}"
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                data={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            if response.status_code == 200:
                return True
            logger.error("Telegram 알림 실패: %s %s", response.status_code, response.text)
            return False
        except requests.RequestException as exc:
            logger.error("Telegram 알림 네트워크 오류: %s", exc)
            return False

    def notify_start(self, dry_run: bool):
        mode = "PAPER" if dry_run else "LIVE"
        return self.send_message("[START]", f"Mode: {mode}\nTime: {datetime.now():%Y-%m-%d %H:%M:%S}")

    def notify_signal(self, target: float, stop: float, quantity: float, reason: str):
        return self.send_message(
            "[ENTRY SIGNAL]",
            f"BTCUSDT\nPrice: {target:,.2f}\nSize: {quantity:.6f} BTC\nStop: {stop:,.2f}\nReason: {reason}",
        )

    def notify_entry(self, entry_price: float, stop_price: float, quantity: float):
        return self.send_message(
            "[ENTRY]",
            f"BTCUSDT\nPrice: {entry_price:,.2f}\nSize: {quantity:.6f} BTC\nStop: {stop_price:,.2f}",
        )

    def notify_stop_update(self, old_stop: float, new_stop: float):
        return self.send_message("[STOP UPDATE]", f"Old: {old_stop:,.2f}\nNew: {new_stop:,.2f}")

    def notify_close(self, exit_price: float, pnl: float, reason: str):
        return self.send_message("[EXIT]", f"BTCUSDT\nExit: {exit_price:,.2f}\nPnL: {pnl:+,.2f}\nReason: {reason}")

    def notify_no_entry(self, reason: str):
        return self.send_message("[HOLD]", reason)

    def notify_error(self, message: str):
        return self.send_message("[ERROR]", message)

