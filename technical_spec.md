# TECHNICAL_SPEC.md

# BTC ATR EMA200 Trading Bot

Technical Specification

Version 1.0

---

# 개요

본 문서는 실제 거래 가능한 BTC 자동매매 시스템의 기술 설계를 정의한다.

전략은 이미 확정되었다.

본 문서에서는 전략이 아닌 시스템 구현 방식을 정의한다.

---

# 기술 스택

## Language

Python 3.12

---

## Package Manager

uv

---

## Database

SQLite

(v1)

PostgreSQL

(v2)

---

## Exchange

Binance Futures

---

## Notification

Telegram Bot API

---

## Deployment

Docker

Ubuntu VPS

---

# 프로젝트 구조

project/

├── src/

├── config/

├── database/

├── logs/

├── reports/

├── tests/

├── docker/

├── docs/

├── main.py

└── requirements.txt

---

# Source Structure

src/

├── exchange/

├── strategy/

├── risk/

├── execution/

├── monitor/

├── notification/

├── database/

├── utils/

└── models/

---

# Core Architecture

+----------------+

Scheduler

+-------+--------+

|

v

+----------------+

Market Data

+-------+--------+

|

v

+----------------+

Strategy Engine

+-------+--------+

|

v

+----------------+

Risk Engine

+-------+--------+

|

v

+----------------+

Execution Engine

+-------+--------+

|

v

+----------------+

Exchange

+----------------+

---

# Configuration

config/settings.yaml

---

example

exchange:

name: binance

symbol: BTCUSDT

timeframe: 4h

---

risk:

risk_per_trade: 0.01

daily_loss_limit: 0.03

max_consecutive_losses: 5

---

strategy:

ema_period: 200

atr_period: 14

atr_multiplier: 1.0

atr_stop: 2.0

pivot_length: 5

---

telegram:

enabled: true

---

# Exchange Layer

exchange/binance_client.py

---

역할

바이낸스 API 연결

---

기능

get_balance()

get_position()

get_open_orders()

place_market_order()

cancel_order()

place_stop_order()

get_ohlcv()

---

# Market Data Layer

market_data.py

---

기능

4시간봉 수집

EMA 계산

ATR 계산

Pivot 계산

---

출력

DataFrame

---

필수 컬럼

timestamp

open

high

low

close

volume

ema200

atr14

pivot_high

pivot_low

---

# Strategy Engine

strategy/atr_breakout.py

---

입력

Market Data

---

출력

Signal

---

Signal Enum

BUY

HOLD

EXIT

---

# 진입 조건

close > ema200

AND

close > previous_close + atr

---

# Risk Engine

risk/risk_manager.py

---

역할

포지션 크기 계산

---

입력

balance

entry_price

stop_price

risk_percent

---

출력

position_size

---

공식

risk_amount

=

balance × risk_percent

---

position_size

=

risk_amount

/

abs(entry-stop)

---

# Execution Engine

execution/executor.py

---

역할

실제 주문 실행

---

흐름

신호 수신

↓

포지션 확인

↓

주문 생성

↓

손절 생성

↓

DB 저장

↓

Telegram 전송

---

# Position Manager

position_manager.py

---

역할

포지션 상태 관리

---

상태

NO_POSITION

LONG

---

기능

entry()

exit()

update_stop()

---

# Trailing Stop Module

pivot_trailing.py

---

규칙

새 Pivot Low 생성

↓

Stop Price 상승

---

조건

신규 Stop

>

기존 Stop

일 때만 변경

---

# Database

SQLite

database/trading.db

---

# Tables

positions

orders

trades

signals

system_logs

---

# positions

id

symbol

entry_time

entry_price

size

stop_price

status

---

# trades

id

entry_time

exit_time

entry_price

exit_price

pnl

pnl_percent

holding_hours

---

# signals

id

timestamp

signal

reason

price

---

# Logging

logs/

---

strategy.log

---

trade.log

---

error.log

---

system.log

---

# Notification

Telegram

---

메시지 예시

[ENTRY]

BTCUSDT

Price: 62500

Size: 0.12 BTC

Stop: 60350

---

[EXIT]

BTCUSDT

PnL: +2.8%

Reason: Pivot Trailing

---

# Scheduler

scheduler.py

---

실행 주기

5분

---

동작

새 캔들 확인

↓

신호 생성

↓

주문 실행

---

# Safety System

safety.py

---

기능

일일 손실 제한

연속 손실 제한

API 장애 감지

비정상 가격 감지

---

# Daily Loss Protection

일일 손실

3%

초과

↓

자동 중지

---

# Consecutive Loss Protection

5연속 손실

↓

24시간 정지

---

# Error Handling

API Timeout

↓

3회 재시도

---

Order Failure

↓

재시도

↓

실패 시 알림

---

Data Error

↓

거래 금지

---

# Paper Trading Mode

MODE=PAPER

---

실주문 금지

가상 체결만 수행

---

# Live Trading Mode

MODE=LIVE

---

실주문 허용

---

실행 전

2중 확인

---

# Monitoring Dashboard

v2

---

표시

계좌 잔고

현재 수익률

열린 포지션

손절 위치

최근 거래

누적 수익률

MDD

---

# 개발 순서

STEP 1

Exchange Module

---

STEP 2

Market Data Module

---

STEP 3

Indicator Engine

---

STEP 4

Strategy Engine

---

STEP 5

Risk Engine

---

STEP 6

Paper Trading

---

STEP 7

Database

---

STEP 8

Telegram

---

STEP 9

Live Trading

---

STEP 10

Docker + VPS 배포

---

# 출시 조건

30일 Paper Trading

완료

---

오류 없음

---

실주문 테스트 성공

---

손절 정상 작동

---

Telegram 정상 동작

---

위 조건 충족 시

실거래 전환 가능
