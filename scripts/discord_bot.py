#!/usr/bin/env python3
"""디스코드 슬래시 명령어 봇 (매매 루프와 분리).

조회: /status /health /mode /help
모드: /setmode /confirm_live  (.env AI_DRY_RUN 한 줄만, 주문 API 없음)

환경변수 (btc_live_trading/.env):
  DISCORD_BOT_TOKEN              필수
  DISCORD_GUILD_ID               권장 (길드 즉시 동기화)
  DISCORD_AUTHORIZED_USER_ID     /setmode · /confirm_live 허용 사용자

실행:
  cd ~/Coin && source ~/venv/bin/activate
  python scripts/discord_bot.py

※ 매매/주문/청산 함수 호출 금지. 잔고는 status_query 읽기만.
※ 실전 전환은 30초 /confirm_live 2단계. systemctl 재시작은 안내만.
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

try:
    import discord
    from discord import app_commands
except ImportError:  # discord.py 미설치 시 main()에서 안내
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]

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
    source = str(snap.get("balance_source") or "")
    if source == "binance_futures":
        bal_name = "실잔고 (Binance USDT-M)"
    elif snap.get("dry_run"):
        bal_name = "가상 잔고 (원장)"
    else:
        bal_name = "잔고 (원장 폴백 · 선물조회 실패)"
    embed.add_field(
        name=bal_name,
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


_MUTATING_COMMANDS = {"setmode", "confirm_live"}
_RESTART_HINT = (
    "매매 봇(`coinbot.service`)은 재시작해야 새 모드를 읽습니다.\n"
    "`sudo systemctl restart coinbot.service` 를 SSH에서 실행하세요.\n"
    "(조회 봇 자동 재시작은 승인 전까지 넣지 않았습니다.)"
)


def _help_embed(tree) -> object:
    import discord

    commands = list(tree.get_commands())
    commands.sort(key=lambda c: str(getattr(c, "name", "")))
    ro_lines: list[str] = []
    rw_lines: list[str] = []
    for cmd in commands:
        name = str(getattr(cmd, "name", "") or "")
        desc = str(getattr(cmd, "description", "") or "").strip() or "(설명 없음)"
        line = f"`/{name}` — {desc}"
        if name in _MUTATING_COMMANDS:
            rw_lines.append(f"⚠️ {line}")
        else:
            ro_lines.append(line)
    embed = discord.Embed(
        title="Coin 봇 명령어",
        description="조회는 누구나, 모드 변경은 등록된 운영자만 가능합니다.",
        color=0x5865F2,
    )
    if ro_lines:
        embed.add_field(name="조회 전용", value="\n".join(ro_lines), inline=False)
    if rw_lines:
        embed.add_field(name="상태 변경", value="\n".join(rw_lines), inline=False)
    embed.set_footer(text="목록은 등록된 슬래시 명령어에서 자동 생성")
    return embed


def _mode_embed(*, dry_run: bool | None, note: str = ""):
    import discord

    from mode_control import mode_label

    live = dry_run is False
    embed = discord.Embed(
        title=f"모드 · {mode_label(dry_run)}",
        description="`AI_DRY_RUN` (.env 파일 기준)",
        color=0xED4245 if live else 0xFEE75C,
    )
    embed.add_field(
        name="값",
        value=f"`AI_DRY_RUN={'false' if live else 'true' if dry_run else '?'}`",
        inline=False,
    )
    if note:
        embed.add_field(name="안내", value=note, inline=False)
    return embed


def _deny_text() -> str:
    from mode_control import authorized_user_id

    if authorized_user_id() is None:
        return "권한 없음 — `DISCORD_AUTHORIZED_USER_ID` 가 .env 에 없습니다."
    return "권한 없음"


def main() -> int:
    token = _require_token()
    guild_id = _guild_id()

    if discord is None or app_commands is None:
        print(
            "ERROR: discord.py 가 설치되어 있지 않습니다.\n"
            "  pip install 'discord.py>=2.3.0'",
            file=sys.stderr,
        )
        return 2

    from mode_control import (
        CONFIRM_WINDOW_SEC,
        append_mode_change_event,
        consume_live_confirm,
        is_authorized,
        read_dry_run_from_env_file,
        replace_ai_dry_run_line,
        request_live_confirm,
    )
    from status_query import assess_cycle_health, build_status_snapshot

    intents = discord.Intents.default()
    # 슬래시 명령어만 사용 — message_content 인텐트 불필요
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @tree.command(name="status", description="조회 전용, 현재 매매 상태·손익·포지션 확인")
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            snap = build_status_snapshot()
            await interaction.followup.send(embed=_status_embed(snap))
        except Exception as exc:
            logger.exception("/status 실패")
            await interaction.followup.send(f"조회 실패: {type(exc).__name__}", ephemeral=True)

    @tree.command(name="health", description="조회 전용, 봇 프로세스 정상 동작 여부 확인")
    async def health_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            snap = build_status_snapshot()
            health = assess_cycle_health(snap)
            await interaction.followup.send(embed=_health_embed(health, snap))
        except Exception as exc:
            logger.exception("/health 실패")
            await interaction.followup.send(f"조회 실패: {type(exc).__name__}", ephemeral=True)

    @tree.command(name="mode", description="조회 전용, 현재 페이퍼/실전 모드 확인")
    async def mode_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            dry_run = read_dry_run_from_env_file()
            await interaction.followup.send(
                embed=_mode_embed(dry_run=dry_run),
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception("/mode 실패")
            await interaction.followup.send(f"조회 실패: {type(exc).__name__}", ephemeral=True)

    @tree.command(
        name="setmode",
        description="모드 전환 (live는 2단계 확인 필요, 본인만 실행 가능)",
    )
    @app_commands.describe(mode="paper=가상(즉시) / live=실전(확인 필요)")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="paper", value="paper"),
            app_commands.Choice(name="live", value="live"),
        ]
    )
    async def setmode_cmd(
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        uid = interaction.user.id if interaction.user else None
        if not is_authorized(uid):
            await interaction.response.send_message(_deny_text(), ephemeral=True)
            return
        target = str(mode.value).strip().lower()
        current = read_dry_run_from_env_file()
        if target == "paper":
            if current is True:
                await interaction.response.send_message(
                    embed=_mode_embed(dry_run=True, note="이미 페이퍼입니다."),
                    ephemeral=True,
                )
                return
            ok, detail = replace_ai_dry_run_line("true")
            if not ok:
                await interaction.response.send_message(f"전환 실패: {detail}", ephemeral=True)
                return
            append_mode_change_event(
                previous="live" if current is False else "unknown",
                next_mode="paper",
                discord_user_id=uid,
                extra={"via": "setmode", "env_write": detail},
            )
            logger.info("mode change paper by user_id=%s result=%s", uid, detail)
            await interaction.response.send_message(
                embed=_mode_embed(
                    dry_run=True,
                    note=f".env 를 페이퍼로 바꿨습니다.\n{_RESTART_HINT}",
                ),
                ephemeral=True,
            )
            return
        if target == "live":
            if current is False:
                await interaction.response.send_message(
                    embed=_mode_embed(dry_run=False, note="이미 실전입니다."),
                    ephemeral=True,
                )
                return
            request_live_confirm(int(uid))
            await interaction.response.send_message(
                "정말 실전으로 전환하시겠습니까?\n"
                f"{int(CONFIRM_WINDOW_SEC)}초 안에 `/confirm_live` 를 입력하세요.\n"
                "시간이 지나면 자동 취소됩니다. 실주문 모드입니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("paper 또는 live 만 선택할 수 있습니다.", ephemeral=True)

    @tree.command(name="confirm_live", description="실전 전환 확인 (30초, 본인만)")
    async def confirm_live_cmd(interaction: discord.Interaction) -> None:
        uid = interaction.user.id if interaction.user else None
        if not is_authorized(uid):
            await interaction.response.send_message(_deny_text(), ephemeral=True)
            return
        state = consume_live_confirm(int(uid))
        if state == "missing":
            await interaction.response.send_message(
                "대기 중인 실전 전환이 없습니다. 먼저 `/setmode live` 를 실행하세요.",
                ephemeral=True,
            )
            return
        if state == "expired":
            await interaction.response.send_message(
                "확인 시간이 지났습니다. `/setmode live` 를 다시 실행하세요.",
                ephemeral=True,
            )
            return
        current = read_dry_run_from_env_file()
        ok, detail = replace_ai_dry_run_line("false")
        if not ok:
            await interaction.response.send_message(f"전환 실패: {detail}", ephemeral=True)
            return
        append_mode_change_event(
            previous="paper" if current is True else "unknown",
            next_mode="live",
            discord_user_id=uid,
            extra={"via": "confirm_live", "env_write": detail},
        )
        logger.info("mode change live by user_id=%s result=%s", uid, detail)
        await interaction.response.send_message(
            embed=_mode_embed(
                dry_run=False,
                note=f"실전으로 전환했습니다.\n{_RESTART_HINT}",
            ),
            ephemeral=True,
        )

    @tree.command(name="help", description="이 도움말")
    async def help_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_help_embed(tree), ephemeral=True)

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
        logger.info("discord_bot ready as %s (매매 호출 없음)", user)

    logger.info("discord_bot 시작")
    client.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
