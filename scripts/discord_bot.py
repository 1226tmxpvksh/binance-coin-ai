#!/usr/bin/env python3
"""디스코드 슬래시 명령어 봇 (조회 전용, 매매 루프와 분리).

명령어:
  /status  — 원장 기반 매매 상태 embed
  /health  — last_cycle_at_kst 기준 지연 의

환경변수 (btc_live_trading/.env):
  DISCORD_BOT_TOKEN   필수
  DISCORD_GUILD_ID    권장 (길드 즉시 동기화)

실행:
  cd ~/Coin && source ~/venv/bin/activate
  python scripts/discord_bot.py

※ 매매/주문/청산/토큰 쓰기 절대 금지. status_query 읽기만 사용.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "btc_live_trading"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LIVE / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("discord_bot")


def _require_token() -> str:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token or token.startswith("your_"):
        print(
            "ERROR: DISCORD_BOT_TOKEN 이 없습니다.\n"
            "  1) https://discord.com/developers/applications 에서 봇 토큰 발급\n"
            "  2) btc_live_trading/.env 에 DISCORD_BOT_TOKEN=... 추가\n"
            "  3) 봇을 서버에 초대한 뒤 다시 실행",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def _guild_id() -> int | None:
    raw = (os.getenv("DISCORD_GUILD_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("DISCORD_GUILD_ID 가 숫자가 아닙니다 — 글로벌 동기화로 진행합니다.")
        return None


def _fmt_num(value: object, *, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _status_embed(snap: dict):
    import discord

    if not snap.get("ok"):
        return discord.Embed(
            title="상태 조회 실패",
            description=str(snap.get("error") or "unknown"),
            color=0xED4245,
        )

    pos = snap.get("position")
    if pos:
        pos_lines = (
            f"**{pos.get('side')}** `{pos.get('symbol')}`\n"
            f"진입 {_fmt_num(pos.get('entry_price'), digits=1)} / "
            f"수량 {_fmt_num(pos.get('position_size'), digits=6)}\n"
            f"손절 {_fmt_num(pos.get('stop_loss'), digits=1)} · "
            f"익절 {_fmt_num(pos.get('take_profit'), digits=1)}\n"
            f"진입시각 {pos.get('opened_at_kst') or '-'}"
        )
    else:
        pos_lines = "없음 (플랫)"

    notify = snap.get("notify") or {}
    embed = discord.Embed(
        title=f"매매 상태 · {snap.get('mode_label')}",
        color=0x57F287 if not snap.get("dry_run") else 0xFEE75C,
        timestamp=None,
    )
    embed.add_field(name="포지션", value=pos_lines, inline=False)
    embed.add_field(
        name="손익",
        value=(
            f"오늘(청산합) {_fmt_num(snap.get('today_realized_pnl_krw'))} KRW / "
            f"{_fmt_num(snap.get('today_realized_pnl_usdt'))} USDT\n"
            f"이번 달 {_fmt_num(snap.get('monthly_profit_krw'))} KRW\n"
            f"누적 {_fmt_num(snap.get('total_profit_krw'))} KRW "
            f"({_fmt_num(snap.get('total_profit_pct'))}%)"
        ),
        inline=False,
    )
    embed.add_field(
        name="잔고(원장)",
        value=f"{_fmt_num(snap.get('balance_krw'))} KRW · {_fmt_num(snap.get('balance_usdt'))} USDT",
        inline=False,
    )
    embed.add_field(
        name="사이클",
        value=(
            f"마지막 `{snap.get('last_cycle_at_kst')}`\n"
            f"지연 {snap.get('cycle_lag_seconds') if snap.get('cycle_lag_seconds') is not None else '-'}s "
            f"/ 루프 {snap.get('loop_seconds')}s"
        ),
        inline=False,
    )
    embed.add_field(
        name="알림",
        value=(
            f"채널 `{notify.get('notify_channel')}`\n"
            f"웹훅 {'OK' if notify.get('discord_webhook_configured') else '없음'} · "
            f"카카오 RT {'있음' if notify.get('kakao_refresh_present') else '없음'}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"조회 {snap.get('checked_at_kst')} · 읽기 전용")
    return embed


def _health_embed(health: dict, snap: dict):
    import discord

    color = {
        "ok": 0x57F287,
        "delayed": 0xFEE75C,
        "unknown": 0x99AAB5,
    }.get(str(health.get("status")), 0x99AAB5)
    embed = discord.Embed(
        title=f"헬스 · {health.get('label')}",
        description=str(health.get("detail") or ""),
        color=color,
    )
    embed.add_field(
        name="기준",
        value=(
            f"last_cycle `{health.get('last_cycle_at_kst')}`\n"
            f"임계 {health.get('threshold_seconds')}s (AI_LOOP_SECONDS×3)"
        ),
        inline=False,
    )
    embed.set_footer(text=f"모드 {snap.get('mode_label')} · systemctl 미사용")
    return embed


def main() -> int:
    token = _require_token()
    guild_id = _guild_id()

    try:
        import discord
        from discord import app_commands
    except ImportError:
        print(
            "ERROR: discord.py 가 설치되어 있지 않습니다.\n"
            "  pip install 'discord.py>=2.3.0'",
            file=sys.stderr,
        )
        return 2

    from status_query import assess_cycle_health, build_status_snapshot

    intents = discord.Intents.default()
    # 슬래시 명령어만 사용 — message_content 인텐트 불필요
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @tree.command(name="status", description="실시간 매매 상태(원장 읽기 전용)")
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            snap = build_status_snapshot()
            await interaction.followup.send(embed=_status_embed(snap))
        except Exception as exc:
            logger.exception("/status 실패")
            await interaction.followup.send(f"조회 실패: {type(exc).__name__}", ephemeral=True)

    @tree.command(name="health", description="매매 루프 지연 여부(last_cycle 기준)")
    async def health_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            snap = build_status_snapshot()
            health = assess_cycle_health(snap)
            await interaction.followup.send(embed=_health_embed(health, snap))
        except Exception as exc:
            logger.exception("/health 실패")
            await interaction.followup.send(f"조회 실패: {type(exc).__name__}", ephemeral=True)

    @client.event
    async def on_ready() -> None:
        try:
            if guild_id is not None:
                guild = discord.Object(id=guild_id)
                tree.copy_global_to(guild=guild)
                synced = await tree.sync(guild=guild)
                logger.info("슬래시 명령어 길드 동기화 %s개 (guild=%s)", len(synced), guild_id)
            else:
                synced = await tree.sync()
                logger.info(
                    "슬래시 명령어 글로벌 동기화 %s개 (반영까지 최대 1시간). "
                    "즉시 반영하려면 DISCORD_GUILD_ID 를 .env 에 넣으세요.",
                    len(synced),
                )
        except Exception:
            logger.exception("슬래시 명령어 동기화 실패")
        user = client.user
        logger.info("discord_bot ready as %s (읽기 전용, 매매 호출 없음)", user)

    logger.info("discord_bot 시작 (조회 전용)")
    client.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
