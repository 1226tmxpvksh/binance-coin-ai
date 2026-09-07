# legacy/kakao — 카카오 알림 레거시

## 이 폴더는?

카카오톡 “나에게 보내기” OAuth·알림·진단 스크립트를 **삭제하지 않고** 모아 둔 격리 디렉터리입니다.

## 왜 만들었나?

카카오 **refresh_token 조기 폐기**(`invalid_grant`)가 반복되어, 운영 알림 기본 채널을 **디스코드 웹훅**(`NOTIFY_CHANNEL=discord`)으로 전환했습니다.  
코드와 운영 지식은 비상 시 되돌릴 수 있도록 여기에 보존합니다.

## 다시 쓰려면

1. `btc_live_trading/.env`에서 `NOTIFY_CHANNEL=kakao` 로 변경  
2. `KAKAO_*` 토큰·키가 유효한지 확인 (`kakao_code.json`은 **여전히** `btc_live_trading/` 에 둠 — 옮기지 않음)  
3. `coinbot.service` 재시작  
4. 필요 시: `bash scripts/coinbot_watch.sh` 또는 `python scripts/auth_kakao.py` (옛 경로 shim → 이 폴더)

자격증명 경로·토큰 저장/회전 로직은 검증된 그대로입니다. **임의 리팩터링 금지.**

## 포함 파일

| 파일 | 역할 |
|------|------|
| `kakao_utils.py` | OAuth2, 토큰 정본, 원자 저장, 화이트리스트 |
| `kakao_notifier.py` | Stateless 카카오 메시지 전송 |
| `auth_kakao.py` | 대화형/코드 재인증 |
| `check_kakao_auth.py` | 토큰 가용성 점검 |
| `test_kakao_send.py` | 1회 발송 진단 |
| `kakao_rt_healthcheck.py` | refresh force 헬스체크 로그 |
| `kakao_rt_health_report.py` | 사망 구간 요약 |

호환 shim: `btc_live_trading/kakao_*.py`, `scripts/auth_kakao.py` 등 → 이 구현을 가리킴.  
`scripts/coinbot_watch.sh`는 로그 팔로우 겸용이므로 **scripts에 유지**.

## 정상 동작 확인일

- **2026-09-04** — Discord 전환 직전 카카오 경로가 운영·문서화되어 있었고, 격리 후 import 경로 검증 완료.
