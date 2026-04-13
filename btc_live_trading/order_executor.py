"""
주문 실행 모듈
바이낸스 선물 거래소에 실제 주문을 넣고 관리
전략: LIMIT 주문 먼저 시도 → 5초 대기 → 미체결 시 MARKET 주문
"""

import logging
import time
from typing import Optional, Dict, Any
from binance.client import Client
from binance.exceptions import BinanceAPIException
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# 안전 설정
MAX_QUANTITY = 100.0  # 최대 수량 (BTC) - 실수로 큰 주문 방지
MIN_QUANTITY = 0.00001  # 최소 수량 (BTC)
MAX_PRICE = 1000000.0  # 최대 가격 ($) - 비정상 가격 방지
MIN_PRICE = 1.0  # 최소 가격 ($)
# 바이낸스 GET /fapi/v1/income (TRANSFER) 페이지당 최대 건수
FUTURES_TRANSFER_INCOME_PAGE_LIMIT = 1000


class OrderExecutor:
    """주문 실행 클래스"""
    
    def __init__(self, client: Client, symbol: str, dry_run: bool = True):
        """
        Args:
            client: 바이낸스 클라이언트
            symbol: 거래 심볼
            dry_run: 테스트 모드 (True: 실제 주문 안 함)
        """
        self.client = client
        
        # 심볼 검증 (알파벳과 숫자만 허용)
        if not symbol or not symbol.isalnum() or len(symbol) > 20:
            raise ValueError(f"잘못된 심볼 형식: {symbol}")
        
        self.symbol = symbol.upper()
        self.dry_run = dry_run
        
        if dry_run:
            logger.warning("🧪 테스트 모드(DRY RUN): 실제 주문이 실행되지 않습니다")
    
    def set_leverage(self, leverage: int) -> bool:
        """
        레버리지 설정
        
        Args:
            leverage: 레버리지 배수
        
        Returns:
            bool: 성공 여부
        """
        try:
            # 레버리지 범위 검증
            if not isinstance(leverage, int) or leverage < 1 or leverage > 125:
                logger.error(f"잘못된 레버리지 값: {leverage}")
                return False
            
            if self.dry_run:
                logger.info(f"[DRY RUN] 레버리지 {leverage}x 설정 (시뮬레이션)")
                return True
            
            self.client.futures_change_leverage(
                symbol=self.symbol,
                leverage=leverage
            )
            logger.info(f"레버리지 {leverage}x 설정 완료")
            return True
            
        except BinanceAPIException as e:
            logger.error(f"레버리지 설정 실패: {type(e).__name__}")
            return False
    
    def get_current_price(self) -> Optional[float]:
        """
        현재 가격 조회
        
        Returns:
            float: 현재 가격
        """
        try:
            ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
            price = float(ticker['price'])
            return price
            
        except Exception as e:
            logger.error(f"현재 가격 조회 실패: {e}")
            return None
    
    def get_account_balance(self) -> Optional[float]:
        """
        계좌 잔고 조회 (USDT)
        
        Returns:
            float: USDT 잔고
        """
        try:
            if self.dry_run:
                # 테스트 모드에서는 가상 잔고 반환
                logger.debug("[DRY RUN] 가상 잔고: 10000 USDT")
                return 10000.0
            
            account = self.client.futures_account_balance()
            usdt_balance = next(
                (float(item['balance']) for item in account if item['asset'] == 'USDT'),
                None
            )
            
            if usdt_balance is not None:
                logger.debug(f"계좌 잔고: {usdt_balance:.2f} USDT")
            
            return usdt_balance
            
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return None
    
    def get_futures_net_transfer_usdt_since(self, start_time_ms: int) -> Optional[float]:
        """
        선물 지갑 USDT의 TRANSFER 수입 내역 합계 (바이낸스 income 부호: 입금+ / 출금-).
        긴급 정지 %에서 출금을 거래 손실과 분리할 때 사용한다.

        Args:
            start_time_ms: 조회 시작 시각(밀리초, UTC 기준 거래소 타임스탬프와 동일 체계)

        Returns:
            합계 USDT, 조회 실패 시 None
        """
        if self.dry_run:
            return 0.0
        if start_time_ms is None or int(start_time_ms) <= 0:
            return 0.0
        try:
            cursor_ms = int(start_time_ms)
            total_net = 0.0
            while True:
                batch = self.client.futures_income_history(
                    incomeType="TRANSFER",
                    startTime=cursor_ms,
                    limit=FUTURES_TRANSFER_INCOME_PAGE_LIMIT,
                )
                if not batch:
                    break
                for row in batch:
                    if row.get("asset") != "USDT":
                        continue
                    total_net += float(row["income"])
                if len(batch) < FUTURES_TRANSFER_INCOME_PAGE_LIMIT:
                    break
                last_t = int(batch[-1]["time"])
                next_cursor = last_t + 1
                if next_cursor <= cursor_ms:
                    logger.warning(
                        "TRANSFER 수입 페이지네이션 이상: 반복 방지를 위해 중단합니다."
                    )
                    break
                cursor_ms = next_cursor
            return total_net
        except BinanceAPIException as e:
            logger.error(
                "선물 TRANSFER 입출금 조회 실패 (BinanceAPI): %s",
                type(e).__name__,
            )
            return None
        except Exception as e:
            logger.error(f"선물 TRANSFER 입출금 조회 실패: {e}")
            return None
    
    def _validate_order_params(self, side: str, quantity: float, price: float) -> bool:
        """
        주문 파라미터 검증
        
        Args:
            side: "BUY" 또는 "SELL"
            quantity: 수량
            price: 가격
        
        Returns:
            bool: 검증 성공 여부
        """
        # side 검증
        if side not in ("BUY", "SELL"):
            logger.error(f"잘못된 주문 방향: {side}")
            return False
        
        # 수량 검증
        try:
            quantity_decimal = Decimal(str(quantity))
            if quantity_decimal <= 0 or quantity > MAX_QUANTITY or quantity < MIN_QUANTITY:
                logger.error(f"잘못된 수량: {quantity} (허용: {MIN_QUANTITY}~{MAX_QUANTITY})")
                return False
        except (InvalidOperation, ValueError):
            logger.error(f"잘못된 수량 형식: {quantity}")
            return False
        
        # 가격 검증
        try:
            price_decimal = Decimal(str(price))
            if price_decimal <= 0 or price > MAX_PRICE or price < MIN_PRICE:
                logger.error(f"잘못된 가격: {price} (허용: {MIN_PRICE}~{MAX_PRICE})")
                return False
        except (InvalidOperation, ValueError):
            logger.error(f"잘못된 가격 형식: {price}")
            return False
        
        return True
    
    def open_limit_order(
        self,
        side: str,
        quantity: float,
        price: float,
        reduce_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        지정가(LIMIT) 주문 실행
        
        Args:
            side: "BUY" 또는 "SELL"
            quantity: 수량 (BTC)
            price: 지정 가격
            reduce_only: True면 포지션 청산만 가능
        
        Returns:
            Dict: 주문 정보
        """
        try:
            # 파라미터 검증
            if not self._validate_order_params(side, quantity, price):
                logger.error("주문 파라미터 검증 실패")
                return None
            if self.dry_run:
                logger.info(
                    f"[DRY RUN] 지정가 주문 시뮬레이션: "
                    f"{side} {quantity:.6f} {self.symbol} @ ${price:,.2f}"
                )
                
                # 시뮬레이션: 50% 확률로 체결 실패
                import random
                is_filled = random.random() > 0.5
                
                return {
                    'orderId': f'DRY_RUN_LIMIT_{int(time.time())}',
                    'symbol': self.symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'origQty': str(quantity),
                    'price': str(price),
                    'status': 'FILLED' if is_filled else 'NEW',
                    'executedQty': str(quantity) if is_filled else '0',
                    'avgPrice': str(price) if is_filled else None
                }
            
            # 실제 지정가 주문 실행
            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type='LIMIT',
                timeInForce='GTC',  # Good Till Cancel
                quantity=quantity,
                price=price,
                reduceOnly=reduce_only
            )
            
            logger.info(
                f"📝 지정가 주문 접수: {side} {quantity:.6f} {self.symbol} @ ${price:,.2f}, "
                f"주문ID: {order.get('orderId')}"
            )
            
            return order
            
        except BinanceAPIException as e:
            # API 에러 코드만 로깅 (상세 내용 제외)
            logger.error(f"지정가 주문 실패: API 에러 (코드: {e.code if hasattr(e, 'code') else 'N/A'})")
            return None
        except Exception as e:
            logger.error(f"지정가 주문 중 오류: {type(e).__name__}")
            return None

    def check_order_status(self, order_id: str) -> Optional[str]:
        """
        주문 상태 확인
        
        Args:
            order_id: 주문 ID
        
        Returns:
            str: 주문 상태 (FILLED, NEW, PARTIALLY_FILLED, CANCELED 등)
        """
        try:
            if self.dry_run:
                logger.debug(f"[DRY RUN] 주문 상태 조회: {order_id}")
                # DRY RUN 모드에서는 주문 ID에서 상태 추출
                return "FILLED" if "FILLED" in str(order_id) else "NEW"
            
            order = self.client.futures_get_order(
                symbol=self.symbol,
                orderId=order_id
            )
            
            status = order.get('status')
            logger.debug(f"주문 {order_id} 상태: {status}")
            
            return status
            
        except Exception as e:
            logger.error(f"주문 상태 조회 실패: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """
        주문 취소
        
        Args:
            order_id: 주문 ID
        
        Returns:
            bool: 성공 여부
        """
        try:
            if self.dry_run:
                logger.info(f"[DRY RUN] 주문 취소: {order_id}")
                return True
            
            self.client.futures_cancel_order(
                symbol=self.symbol,
                orderId=order_id
            )
            
            logger.info(f"주문 취소 완료: {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return False
    
    def open_market_order(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        시장가 주문 실행
        
        Args:
            side: "BUY" 또는 "SELL"
            quantity: 수량 (BTC)
            reduce_only: True면 포지션 청산만 가능
        
        Returns:
            Dict: 주문 정보
        """
        try:
            if self.dry_run:
                logger.info(
                    f"[DRY RUN] 시장가 주문 시뮬레이션: "
                    f"{side} {quantity:.6f} {self.symbol}"
                )
                
                # 시뮬레이션 주문 정보 반환
                current_price = self.get_current_price()
                return {
                    'orderId': 'DRY_RUN_12345',
                    'symbol': self.symbol,
                    'side': side,
                    'type': 'MARKET',
                    'origQty': str(quantity),
                    'executedQty': str(quantity),
                    'avgPrice': str(current_price),
                    'status': 'FILLED'
                }
            
            # 실제 주문 실행
            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type='MARKET',
                quantity=quantity,
                reduceOnly=reduce_only
            )
            
            logger.info(
                f"✅ 주문 체결: {side} {quantity:.6f} {self.symbol}, "
                f"평균가: {order.get('avgPrice', 'N/A')}"
            )
            
            return order
            
        except BinanceAPIException as e:
            logger.error(f"주문 실행 실패: API 에러 (코드: {e.code if hasattr(e, 'code') else 'N/A'})")
            return None
        except Exception as e:
            logger.error(f"주문 실행 중 오류: {type(e).__name__}")
            return None
    
    def open_order_with_fallback(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        wait_seconds: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        """
        주문 실행 (LIMIT 먼저 시도 → 실패 시 MARKET)
        
        전략:
        1. 현재가 기준으로 LIMIT 주문 (수수료 절감)
        2. wait_seconds 대기
        3. 체결 안 되면 취소 후 MARKET 주문 (체결 보장)
        
        Args:
            side: "BUY" 또는 "SELL"
            quantity: 수량 (BTC)
            reduce_only: True면 포지션 청산만 가능
            wait_seconds: LIMIT 주문 대기 시간 (초, 기본 5초. 청산 시 60초 권장)
        
        Returns:
            Dict: 주문 정보
        """
        # 1. 현재가 조회
        current_price = self.get_current_price()
        if current_price is None:
            logger.error("현재가 조회 실패, MARKET 주문으로 진행")
            return self.open_market_order(side, quantity, reduce_only)
        
        # 2. LIMIT 가격 계산 (약간 유리하게 설정)
        if side == "BUY":
            # 매수: 현재가보다 0.01% 높게 (빠른 체결)
            limit_price = current_price * 1.0001
        else:
            # 매도: 현재가보다 0.01% 낮게 (빠른 체결)
            limit_price = current_price * 0.9999
        
        # 가격 소수점 정리 (바이낸스 규칙에 맞게)
        limit_price = round(limit_price, 2)
        
        logger.info(
            f"💡 LIMIT 주문 시도: {side} {quantity:.6f} BTC @ ${limit_price:,.2f}"
        )
        
        # 3. LIMIT 주문 실행
        limit_order = self.open_limit_order(side, quantity, limit_price, reduce_only)
        
        if limit_order is None:
            logger.warning("LIMIT 주문 실패, 즉시 MARKET 주문으로 전환")
            return self.open_market_order(side, quantity, reduce_only)
        
        order_id = limit_order.get('orderId')
        
        # 4. 체결 대기
        logger.info(f"⏳ {wait_seconds}초 대기 중... (주문ID: {order_id})")
        time.sleep(wait_seconds)
        
        # 5. 주문 상태 확인
        status = self.check_order_status(order_id)
        
        if status == 'FILLED':
            logger.info(f"✅ LIMIT 주문 체결 성공! (수수료 절감)")
            return limit_order
        
        # 6. 미체결 시 취소 후 MARKET 주문
        logger.warning(
            f"⚠️ LIMIT 주문 미체결 (상태: {status}), "
            f"주문 취소 후 MARKET 주문으로 전환"
        )
        
        self.cancel_order(order_id)
        
        logger.info(f"🔄 MARKET 주문으로 재시도...")
        market_order = self.open_market_order(side, quantity, reduce_only)
        
        if market_order:
            logger.info(f"✅ MARKET 주문 체결 완료")
        
        return market_order
    
    def open_long_position(self, quantity: float) -> Optional[Dict[str, Any]]:
        """
        롱 포지션 오픈 (LIMIT → MARKET 폴백)
        
        Args:
            quantity: 수량
        
        Returns:
            Dict: 주문 정보
        """
        logger.info(f"🟢 LONG 포지션 오픈: {quantity:.6f} BTC")
        return self.open_order_with_fallback("BUY", quantity)
    
    def open_short_position(self, quantity: float) -> Optional[Dict[str, Any]]:
        """
        숏 포지션 오픈 (LIMIT → MARKET 폴백)
        
        Args:
            quantity: 수량
        
        Returns:
            Dict: 주문 정보
        """
        logger.info(f"🔴 SHORT 포지션 오픈: {quantity:.6f} BTC")
        return self.open_order_with_fallback("SELL", quantity)
    
    def close_long_position(self, quantity: float) -> Optional[Dict[str, Any]]:
        """
        롱 포지션 청산 (LIMIT → MARKET 폴백, 60초 대기)
        
        전략적 청산용: 밀리지 않게 60초 대기 후 MARKET 전환
        
        Args:
            quantity: 수량
        
        Returns:
            Dict: 주문 정보
        """
        logger.info(f"📤 LONG 포지션 청산: {quantity:.6f} BTC (LIMIT 60초 → MARKET)")
        return self.open_order_with_fallback("SELL", quantity, reduce_only=True, wait_seconds=60.0)
    
    def close_short_position(self, quantity: float) -> Optional[Dict[str, Any]]:
        """
        숏 포지션 청산 (LIMIT → MARKET 폴백, 60초 대기)
        
        전략적 청산용: 밀리지 않게 60초 대기 후 MARKET 전환
        
        Args:
            quantity: 수량
        
        Returns:
            Dict: 주문 정보
        """
        logger.info(f"📤 SHORT 포지션 청산: {quantity:.6f} BTC (LIMIT 60초 → MARKET)")
        return self.open_order_with_fallback("BUY", quantity, reduce_only=True, wait_seconds=60.0)
    
    def get_position_info(self) -> Optional[Dict[str, Any]]:
        """
        현재 포지션 정보 조회
        
        Returns:
            Dict: 포지션 정보
        """
        try:
            if self.dry_run:
                logger.debug("[DRY RUN] 포지션 정보 조회 (시뮬레이션)")
                return None
            
            positions = self.client.futures_position_information(
                symbol=self.symbol
            )
            
            if positions:
                position = positions[0]
                position_amt = float(position['positionAmt'])
                
                if position_amt != 0:
                    return {
                        'symbol': position['symbol'],
                        'positionAmt': position_amt,
                        'entryPrice': float(position['entryPrice']),
                        'unrealizedProfit': float(position['unRealizedProfit']),
                        'leverage': int(position['leverage'])
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"포지션 정보 조회 실패: {e}")
            return None
    
    def cancel_all_orders(self) -> bool:
        """
        모든 미체결 주문 취소
        
        Returns:
            bool: 성공 여부
        """
        try:
            if self.dry_run:
                logger.info("[DRY RUN] 모든 주문 취소 (시뮬레이션)")
                return True
            
            self.client.futures_cancel_all_open_orders(symbol=self.symbol)
            logger.info("모든 미체결 주문 취소 완료")
            return True
            
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return False
    
    def place_stop_loss_order(
        self,
        side: str,
        quantity: float,
        stop_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        손절 주문 등록 (STOP_MARKET)
        
        Args:
            side: "SELL" (LONG 청산) 또는 "BUY" (SHORT 청산)
            quantity: 수량
            stop_price: 손절 가격
        
        Returns:
            Dict: 주문 정보
        """
        try:
            if self.dry_run:
                logger.info(
                    f"[DRY RUN] 손절 주문 등록: {side} {quantity:.6f} @ ${stop_price:,.2f}"
                )
                return {
                    'orderId': f'DRY_RUN_STOP_{int(time.time())}',
                    'symbol': self.symbol,
                    'side': side,
                    'type': 'STOP_MARKET',
                    'stopPrice': str(stop_price),
                    'origQty': str(quantity),
                    'status': 'NEW'
                }
            
            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type='STOP_MARKET',
                stopPrice=stop_price,
                quantity=quantity,
                reduceOnly=True  # 포지션 청산만
            )
            
            logger.info(
                f"🛡️ 손절 주문 등록: {side} {quantity:.6f} @ ${stop_price:,.2f}, "
                f"주문ID: {order.get('orderId')}"
            )
            
            return order
            
        except Exception as e:
            logger.error(f"손절 주문 등록 실패: {type(e).__name__}")
            return None
    
    def place_take_profit_order(
        self,
        side: str,
        quantity: float,
        tp_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        익절 주문 등록 (TAKE_PROFIT_MARKET)
        
        Args:
            side: "SELL" (LONG 청산) 또는 "BUY" (SHORT 청산)
            quantity: 수량
            tp_price: 익절 가격
        
        Returns:
            Dict: 주문 정보
        """
        try:
            if self.dry_run:
                logger.info(
                    f"[DRY RUN] 익절 주문 등록: {side} {quantity:.6f} @ ${tp_price:,.2f}"
                )
                return {
                    'orderId': f'DRY_RUN_TP_{int(time.time())}',
                    'symbol': self.symbol,
                    'side': side,
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': str(tp_price),
                    'origQty': str(quantity),
                    'status': 'NEW'
                }
            
            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type='TAKE_PROFIT_MARKET',
                stopPrice=tp_price,
                quantity=quantity,
                reduceOnly=True  # 포지션 청산만
            )
            
            logger.info(
                f"🎯 익절 주문 등록: {side} {quantity:.6f} @ ${tp_price:,.2f}, "
                f"주문ID: {order.get('orderId')}"
            )
            
            return order
            
        except Exception as e:
            logger.error(f"익절 주문 등록 실패: {type(e).__name__}")
            return None
    
    def update_stop_loss_order(
        self,
        old_order_id: str,
        side: str,
        quantity: float,
        new_stop_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        손절 주문 업데이트 (기존 취소 후 새로 등록)
        
        Args:
            old_order_id: 기존 주문 ID
            side: "SELL" 또는 "BUY"
            quantity: 수량
            new_stop_price: 새 손절 가격
        
        Returns:
            Dict: 새 주문 정보
        """
        try:
            # 1. 기존 주문 취소
            if not self.cancel_order(old_order_id):
                logger.warning("기존 손절 주문 취소 실패, 계속 진행...")
            
            # 2. 새 손절 주문 등록
            new_order = self.place_stop_loss_order(side, quantity, new_stop_price)
            
            if new_order:
                logger.info(f"✅ 손절가 업데이트: ${new_stop_price:,.2f}")
            
            return new_order
            
        except Exception as e:
            logger.error(f"손절 주문 업데이트 실패: {type(e).__name__}")
            return None
    
    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        주문 상세 정보 조회
        
        Args:
            order_id: 주문 ID
        
        Returns:
            Dict: 주문 정보
        """
        try:
            if self.dry_run:
                logger.debug(f"[DRY RUN] 주문 조회: {order_id}")
                return {
                    'orderId': order_id,
                    'status': 'NEW',
                    'type': 'STOP_MARKET'
                }
            
            order = self.client.futures_get_order(
                symbol=self.symbol,
                orderId=order_id
            )
            
            return order
            
        except Exception as e:
            logger.error(f"주문 조회 실패: {type(e).__name__}")
            return None
