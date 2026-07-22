"""SQLite 이벤트/주문/신호 저장."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class TradeDatabase:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    entry_time TEXT,
                    entry_price REAL,
                    size REAL,
                    stop_price REAL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT,
                    order_type TEXT,
                    price REAL,
                    quantity REAL,
                    status TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_time TEXT,
                    exit_time TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    pnl_percent REAL,
                    holding_hours REAL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    reason TEXT,
                    price REAL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (created_at, event_type, payload) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), event_type, json.dumps(payload, ensure_ascii=False, default=str)),
            )

    def record_signal(self, signal: str, reason: str, price: float, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signals (timestamp, signal, reason, price, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    signal,
                    reason,
                    price,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )

    def record_order(self, symbol: str, order: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orders (created_at, symbol, side, order_type, price, quantity, status, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    symbol,
                    order.get("side"),
                    order.get("type") or order.get("orderType"),
                    _safe_float(
                        order.get("price")
                        or order.get("avgPrice")
                        or order.get("triggerPrice")
                        or order.get("stopPrice")
                    ),
                    _safe_float(order.get("executedQty") or order.get("origQty") or order.get("quantity")),
                    order.get("status") or order.get("algoStatus"),
                    json.dumps(order, ensure_ascii=False, default=str),
                ),
            )

    def record_position(self, symbol: str, status: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (symbol, entry_time, entry_price, size, stop_price, status, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    payload.get("entry_time"),
                    payload.get("entry_price"),
                    payload.get("quantity"),
                    payload.get("stop_price"),
                    status,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )

    def record_trade(self, payload: dict[str, Any]) -> None:
        entry_time = payload.get("entry_time")
        exit_time = payload.get("exit_time")
        holding_hours = None
        try:
            if entry_time and exit_time:
                holding_hours = (
                    datetime.fromisoformat(exit_time) - datetime.fromisoformat(entry_time)
                ).total_seconds() / 3600
        except ValueError:
            holding_hours = None
        pnl = _safe_float(payload.get("pnl")) or 0.0
        entry_price = _safe_float(payload.get("entry_price")) or 0.0
        quantity = _safe_float(payload.get("quantity")) or 0.0
        pnl_percent = pnl / (entry_price * quantity) if entry_price > 0 and quantity > 0 else 0.0
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (entry_time, exit_time, entry_price, exit_price, pnl, pnl_percent, holding_hours, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_time,
                    exit_time,
                    entry_price,
                    payload.get("exit_price"),
                    pnl,
                    pnl_percent,
                    holding_hours,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )

    def record_system_log(self, level: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO system_logs (created_at, level, message, payload) VALUES (?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    level,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                ),
            )


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

