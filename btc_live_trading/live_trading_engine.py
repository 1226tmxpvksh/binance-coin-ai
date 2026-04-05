"""
실시간 매매 엔진
실시간으로 시장을 모니터링하고 전략에 따라 거래 실행
btc_day_strategy와 무관하게 독립 실행
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from strategy.indicators import add_all_indicators, calculate_k_value
from strategy.market_regime import determine_market_state, MarketState
from strategy.strategy_entry import evaluate_entry_signal, PositionSide
from strategy.strategy_exit import (
    calculate_initial_stops,
    check_long_hold_extension,
    calculate_trailing_stop,
    check_extension_exit,
    should_force_short_exit,
)
from strategy.risk_manager import calculate_position_size, calculate_position_value

logger = logging.getLogger(__name__)

# TRANSFER 보정 금액이 이 값(USDT) 이상일 때만 INFO 로그
_TRANSFER_NET_LOG_THRESHOLD_USDT = 0.01


class LiveTradingEngine:
    """실시간 매매 엔진"""
    
    def __init__(
        self,
        config: Dict[str, Any],
        order_executor,
        position_manager,
        safety_manager,
        notifier
    ):
        """
        Args:
            config: 설정 딕셔너리
            order_executor: 주문 실행기
            position_manager: 포지션 관리자
            safety_manager: 안전장치 관리자
            notifier: 알림 모듈
        """
        self.config = config
        self.executor = order_executor
        self.position_mgr = position_manager
        self.safety = safety_manager
        self.notifier = notifier
        
        self.is_running = False
        self.last_check_date = None
    
    def start(self):
        """매매 엔진 시작"""
        
        self.is_running = True
        
        # 시작 알림
        if self.config['DRY_RUN']:
            self.notifier.notify_dry_run_mode()
        else:
            self.notifier.notify_start()
        
        # 레버리지 설정
        self.executor.set_leverage(self.config['LEVERAGE'])
        
        logger.info(
            f"설정: 심볼={self.config['SYMBOL']}, "
            f"레버리지={self.config['LEVERAGE']}x, "
            f"DRY_RUN={self.config['DRY_RUN']}"
        )
    
    def stop(self):
        """매매 엔진 중지"""
        logger.info("매매 엔진 중지")
        self.is_running = False
        
        # 포지션이 있으면 경고
        if self.position_mgr.has_position():
            logger.warning("⚠️ 포지션이 남아있습니다!")
    
    def check_and_trade(self, data_fetcher) -> bool:
        """
        시장 체크 및 거래 실행
        
        Args:
            data_fetcher: 데이터 가져오기 함수
        
        Returns:
            bool: 계속 실행 여부
        """
        try:
            # 1. 계좌 잔고 조회
            balance = self.executor.get_account_balance()
            if balance is None:
                logger.error("잔고 조회 실패")
                return True
            
            min_entry_balance = self.config['MIN_TRADING_BALANCE_USDT']
            if (
                not self.position_mgr.has_position()
                and balance < min_entry_balance
            ):
                logger.warning(
                    f"최소 진입 잔고 미만 (${balance:,.2f} < ${min_entry_balance:,.2f}): "
                    "오늘은 신규 진입·진입 신호 검사를 생략합니다."
                )
                self.notifier.notify_insufficient_balance(balance, min_entry_balance)
                return True
            
            # 2. 안전장치 체크 (긴급 정지 %: 바이낸스 TRANSFER로 입출금 보정)
            transfer_net = self.executor.get_futures_net_transfer_usdt_since(
                self.safety.initial_capital_baseline_ms
            )
            if transfer_net is None:
                logger.warning(
                    "선물 TRANSFER 입출금 내역 조회 실패 — "
                    "출금을 손실에서 제외하는 보정 없이 긴급 정지 %를 계산합니다."
                )
                transfer_net = 0.0
            elif abs(transfer_net) >= _TRANSFER_NET_LOG_THRESHOLD_USDT:
                logger.info(
                    f"긴급 정지용 TRANSFER 누적(기준 시각 이후): {transfer_net:,.2f} USDT "
                    "(입금+, 출금-; 순잔고 산출 시 반영)"
                )
            can_trade, reason = self.safety.can_trade(
                balance,
                external_transfer_net_usdt=transfer_net,
            )
            if not can_trade:
                logger.warning(f"거래 불가: {reason}")
                
                if self.safety.emergency_stopped:
                    self.notifier.notify_emergency_stop(
                        reason,
                        self.safety.initial_capital - balance
                    )
                    return False  # 프로그램 종료
                
                return True  # 계속 모니터링
            
            # 3. 데이터 가져오기
            data = data_fetcher()
            if data is None or data.empty:
                logger.error("데이터 가져오기 실패")
                return True
            
            # 4. 데이터 검증 (최소 2행 필요: current, prev)
            if len(data) < 2:
                logger.error(f"데이터 부족: {len(data)}개 캔들 (최소 2개 필요)")
                return True

            # 5. 지표 계산
            data = add_all_indicators(
                data,
                self.config['EMA_SHORT_PERIOD'],
                self.config['EMA_LONG_PERIOD'],
                self.config['SMA_LONG_PERIOD'],
                self.config['ATR_PERIOD'],
                self.config['VOLUME_MA_PERIOD']
            )
            
            # 6. 최신 데이터 (NaN 체크)
            current = data.iloc[-1]
            prev = data.iloc[-2]
            if (
                current['ema_short'] != current['ema_short']  # NaN 체크
                or prev['vol_ma'] != prev['vol_ma']
                or prev['atr'] != prev['atr']
            ):
                logger.warning("지표 계산 미완료 (NaN): 데이터 부족. 거래 보류")
                return True

            # 7. 포지션 관리
            
            logger.info(
                f"현재가: ${current['close']:,.2f}, "
                f"EMA20: ${current['ema_short']:,.2f}, "
                f"EMA60: ${current['ema_long']:,.2f}"
            )
            
            if self.position_mgr.has_position():
                # 청산 체크
                self._check_exit(current, prev, balance)
            else:
                # 진입 체크
                self._check_entry(current, prev, data, balance)
            
            return True
            
        except Exception as e:
            logger.error(f"체크 및 거래 중 오류: {e}", exc_info=True)
            self.notifier.notify_error(str(e))
            return True
    
    def _check_entry(self, current, prev, data, balance):
        """진입 체크 및 실행"""
        
        # 일일 거래 횟수 체크
        if not self.safety.check_trade_limit():
            return
        
        # 시장 상태 판단
        market_state = determine_market_state(
            current['close'],
            current['ema_short'],
            current['ema_long'],
            current['sma_long'],
            current['sma_slope']
        )
        
        if market_state == MarketState.NEUTRAL:
            logger.debug("중립 구간: 거래 안 함")
            return
        
        # K값 계산
        atr_lookback = data['atr'].tail(self.config['ATR_PERIOD'])
        atr_max = atr_lookback.max()
        k_value = calculate_k_value(
            current['atr'],
            atr_max,
            self.config['K_MAX']
        )
        
        # 진입 신호 평가
        current_data = {
            'high': current['high'],
            'low': current['low'],
            'open': current['open'],
            'close': current['close'],
            'volume': current['volume']
        }
        
        prev_data = {
            'high': prev['high'],
            'low': prev['low'],
            'volume': prev['volume'],
            'vol_ma': prev['vol_ma']
        }
        
        signal = evaluate_entry_signal(
            market_state.value,
            current_data,
            prev_data,
            k_value
        )
        
        # 진입 신호가 있으면 주문 실행
        if signal is not None and signal != PositionSide.NONE:
            self._execute_entry(
                signal.value,
                current['close'],
                current['atr'],
                balance,
                market_state.value
            )
    
    def _execute_entry(
        self,
        side: str,
        entry_price: float,
        atr: float,
        balance: float,
        market_state: str
    ):
        """진입 주문 실행"""
        
        # 손절/익절 계산
        stop_loss, take_profit = calculate_initial_stops(
            entry_price,
            atr,
            side,
            self.config['STOP_LOSS_MULTIPLIER'],
            self.config['TAKE_PROFIT_MULTIPLIER']
        )
        
        # 포지션 사이즈 계산
        position_size = calculate_position_size(
            balance,
            entry_price,
            stop_loss,
            self.config['RISK_PER_TRADE'],
            self.config['LEVERAGE']
        )
        
        if position_size <= 0:
            logger.warning("포지션 사이즈가 0입니다. 진입 취소")
            return
        
        # 최소 주문 크기 체크
        position_value = calculate_position_value(position_size, entry_price)
        if position_value < self.config['MIN_POSITION_SIZE_USDT']:
            logger.warning(
                f"포지션이 너무 작습니다: ${position_value:.2f} < "
                f"${self.config['MIN_POSITION_SIZE_USDT']}"
            )
            return
        
        # 진입 신호 알림
        self.notifier.notify_entry_signal(
            side,
            entry_price,
            market_state,
            "변동성 돌파"
        )
        
        # 주문 실행
        if side == "LONG":
            order = self.executor.open_long_position(position_size)
        else:
            order = self.executor.open_short_position(position_size)
        
        if order is None:
            logger.error("주문 실행 실패")
            self.notifier.notify_error("주문 실행 실패")
            return
        
        # 실제 체결 가격 (DRY RUN에서는 현재가 사용)
        filled_price = float(order.get('avgPrice', entry_price))
        
        # 손절/익절 주문을 바이낸스에 등록
        close_side = "SELL" if side == "LONG" else "BUY"
        
        # 손절 주문 등록
        stop_order = self.executor.place_stop_loss_order(
            close_side,
            position_size,
            stop_loss
        )
        
        # 익절 주문 등록
        tp_order = self.executor.place_take_profit_order(
            close_side,
            position_size,
            take_profit
        )
        
        # 주문 등록 실패 시 진입 주문 취소
        if stop_order is None or tp_order is None:
            logger.error("손절/익절 주문 등록 실패! 진입 주문 취소 시도...")
            
            # 진입 포지션 즉시 청산
            if side == "LONG":
                self.executor.close_long_position(position_size)
            else:
                self.executor.close_short_position(position_size)
            
            self.notifier.notify_error("손절/익절 주문 등록 실패로 진입 취소")
            return
        
        # 포지션 관리자에 등록
        self.position_mgr.open_position(
            side=side,
            entry_price=filled_price,
            size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_id=order.get('orderId'),
            stop_order_id=stop_order.get('orderId'),
            tp_order_id=tp_order.get('orderId')
        )
        
        # 안전장치 업데이트
        self.safety.increment_daily_trades()
        
        # 체결 알림
        self.notifier.notify_order_filled(
            side,
            filled_price,
            position_size,
            stop_loss,
            take_profit,
            position_value
        )
        
        logger.info(
            f"✅ 진입 완료 + 손절/익절 주문 바이낸스 등록 완료 "
            f"(Stop ID: {stop_order.get('orderId')}, TP ID: {tp_order.get('orderId')})"
        )
    
    def _check_exit(self, current, prev, balance):
        """청산 체크 및 실행 (주문 상태 확인)"""
        
        position = self.position_mgr.get_position()
        if position is None:
            return
        
        # 보유 일수 업데이트
        position.update_hold_days(datetime.now())
        
        # 현재 가격 가져오기
        current_price = self.executor.get_current_price()
        if current_price is None:
            logger.error("현재 가격 조회 실패")
            return
        
        # 최고/최저가 업데이트
        position.update_price_extremes(current_price)
        
        # 바이낸스 주문 상태 확인 (손절/익절 체결 확인 - 기록용)
        # 체결되었으면 기록만 하고 종료 (바이낸스가 이미 처리 완료)
        if position.stop_order_id:
            stop_order_info = self.executor.get_order_status(position.stop_order_id)
            if stop_order_info and stop_order_info.get('status') == 'FILLED':
                exit_price = float(stop_order_info.get('avgPrice', current['close']))
                logger.info(f"🛡️ 손절 주문 체결됨 (바이낸스 자동 처리): ${exit_price:,.2f}")
                
                # 익절 주문 취소
                if position.tp_order_id:
                    self.executor.cancel_order(position.tp_order_id)
                
                self._record_exit(exit_price, "stop_loss", balance)
                return
        
        if position.tp_order_id:
            tp_order_info = self.executor.get_order_status(position.tp_order_id)
            if tp_order_info and tp_order_info.get('status') == 'FILLED':
                exit_price = float(tp_order_info.get('avgPrice', current['close']))
                logger.info(f"🎯 익절 주문 체결됨 (바이낸스 자동 처리): ${exit_price:,.2f}")
                
                # 손절 주문 취소
                if position.stop_order_id:
                    self.executor.cancel_order(position.stop_order_id)
                
                self._record_exit(exit_price, "take_profit", balance)
                return
        
        # SHORT는 익일 강제 청산
        if should_force_short_exit(position.side, position.hold_days):
            logger.info("SHORT 익일 청산")
            # 손절/익절 주문 취소 후 시장가 청산
            if position.stop_order_id:
                self.executor.cancel_order(position.stop_order_id)
            if position.tp_order_id:
                self.executor.cancel_order(position.tp_order_id)
            
            self._execute_exit(current['close'], "short_day_exit", balance)
            return
        
        # LONG 확장 보유 체크
        if position.side == "LONG":
            self._check_long_extension(current, balance)
    
    def _check_long_extension(self, current, balance):
        """LONG 확장 보유 체크"""
        
        position = self.position_mgr.get_position()
        
        # 보유 연장 가능한지 확인
        can_extend = check_long_hold_extension(
            current['close'],
            position.entry_price,
            current['ema_short'],
            current['ema_long']
        )
        
        if can_extend:
            # 확장 모드 활성화
            if not position.is_extended:
                position.is_extended = True
                logger.info("LONG 확장 모드 진입")
            
            # 트레일링 스탑 계산
            new_stop = calculate_trailing_stop(
                position.stop_loss,
                current['ema_short'],
                current['atr'],
                self.config['TRAILING_STOP_ATR_MULT']
            )
            
            # 손절가가 상승했으면 바이낸스 주문 업데이트
            if new_stop > position.stop_loss:
                close_side = "SELL" if position.side == "LONG" else "BUY"
                
                # 바이낸스 손절 주문 업데이트
                new_stop_order = self.executor.update_stop_loss_order(
                    position.stop_order_id,
                    close_side,
                    position.size,
                    new_stop
                )
                
                if new_stop_order:
                    # 포지션 정보 업데이트
                    position.update_trailing_stop(new_stop)
                    position.update_stop_order_id(new_stop_order.get('orderId'))
                    
                    logger.info(f"✅ 트레일링 스탑 바이낸스 주문 업데이트 완료: ${new_stop:,.2f}")
                else:
                    logger.warning("트레일링 스탑 주문 업데이트 실패")
            
            # 확장 중 청산 조건 체크
            should_exit, exit_reason = check_extension_exit(
                current['close'],
                current['volume'],
                current['ema_short'],
                current['vol_ma'],
                position.hold_days,
                self.config['MAX_HOLD_DAYS']
            )
            
            if should_exit:
                self._execute_exit(current['close'], exit_reason, balance)
        else:
            # 확장 조건 불충족 시 청산
            self._execute_exit(current['close'], "extension_fail", balance)
    
    def _execute_exit(self, exit_price: float, exit_reason: str, balance: float):
        """청산 주문 실행 (시장가 즉시 청산)"""
        
        position = self.position_mgr.get_position()
        if position is None:
            return
        
        # 기존 손절/익절 주문 취소
        if position.stop_order_id:
            self.executor.cancel_order(position.stop_order_id)
        if position.tp_order_id:
            self.executor.cancel_order(position.tp_order_id)
        
        # 청산 주문 실행
        if position.side == "LONG":
            order = self.executor.close_long_position(position.size)
        else:
            order = self.executor.close_short_position(position.size)
        
        if order is None:
            logger.error("청산 주문 실패")
            self.notifier.notify_error("청산 주문 실패")
            return
        
        # 실제 청산 가격
        filled_price = float(order.get('avgPrice', exit_price))
        
        self._record_exit(filled_price, exit_reason, balance)
    
    def _record_exit(self, exit_price: float, exit_reason: str, balance: float):
        """청산 기록"""
        
        # 포지션 관리자에서 청산 처리
        trade_record = self.position_mgr.close_position(exit_price, exit_reason)
        
        if trade_record is None:
            return
        
        # 안전장치 업데이트
        self.safety.update_daily_pnl(trade_record['pnl'])
        
        # 청산 알림
        self.notifier.notify_position_closed(
            trade_record['side'],
            trade_record['entry_price'],
            trade_record['exit_price'],
            trade_record['pnl'],
            trade_record['profit_rate'],
            exit_reason,
            balance + trade_record['pnl']
        )
    
    def send_daily_summary(self, balance: float):
        """일일 요약 전송"""
        stats = self.position_mgr.get_daily_stats()
        
        self.notifier.notify_daily_summary(
            stats['daily_trades'],
            stats['winning_trades'],
            stats['daily_pnl'],
            balance
        )
        
        # 일일 통계 초기화
        self.position_mgr.reset_daily_stats()
        self.safety.reset_daily_stats()
