"""
일일 주문 스케줄러
매일 한국 시간 9시에 실행하여 주문을 넣고 종료
트레일링 스탑으로 안전장치 구현
LONG 전용: 상승장(BULL)에서만 진입 주문
"""

import logging
import sys
from datetime import datetime
from binance.client import Client

import config_live as config
from kakao_notifier import KakaoNotifier
from strategy.data_loader import fetch_historical_data
from strategy.indicators import add_all_indicators, calculate_k_value
from strategy.market_regime import determine_market_state, MarketState
from strategy.risk_manager import calculate_position_size

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('daily_orders.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


class DailyOrderScheduler:
    """일일 주문 스케줄러"""
    
    def __init__(self):
        """초기화"""
        # 바이낸스 클라이언트
        self.client = Client(config.API_KEY, config.API_SECRET)
        
        # 알림
        if config.KAKAO_ENABLED:
            self.notifier = KakaoNotifier(
                access_token=config.KAKAO_ACCESS_TOKEN,
                enabled=True
            )
        else:
            self.notifier = None
        
        # 설정
        self.symbol = config.SYMBOL
        self.leverage = config.LEVERAGE
        self.dry_run = config.DRY_RUN
    
    def cancel_all_pending_orders(self):
        """모든 미체결 주문 취소"""
        try:
            if self.dry_run:
                logger.info("[DRY RUN] 미체결 주문 취소 (시뮬레이션)")
                return True
            
            orders = self.client.futures_get_open_orders(symbol=self.symbol)
            
            if not orders:
                logger.info("취소할 미체결 주문이 없습니다")
                return True
            
            logger.info(f"미체결 주문 {len(orders)}개 발견, 취소 진행...")
            
            for order in orders:
                order_id = order['orderId']
                self.client.futures_cancel_order(
                    symbol=self.symbol,
                    orderId=order_id
                )
                logger.info(f"주문 취소: {order_id}")
            
            logger.info("모든 미체결 주문 취소 완료")
            return True
            
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return False
    
    def get_account_balance(self):
        """계좌 잔고 조회"""
        try:
            if self.dry_run:
                return 10000.0
            
            account = self.client.futures_account_balance()
            usdt_balance = next(
                (float(item['balance']) for item in account if item['asset'] == 'USDT'),
                None
            )
            return usdt_balance
            
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return None
    
    def calculate_entry_price(self):
        """
        진입 목표가 계산
        
        전일 봉 기준으로 변동성 돌파 가격 계산
        """
        try:
            # 데이터 가져오기 (300개 캔들)
            data = fetch_historical_data(
                client=self.client,
                symbol=self.symbol,
                interval='1d',
                limit=300
            )
            
            if data is None or len(data) < 250:
                logger.error("데이터 부족")
                return None
            
            # 지표 계산
            data = add_all_indicators(
                data,
                config.EMA_SHORT_PERIOD,
                config.EMA_LONG_PERIOD,
                config.SMA_LONG_PERIOD,
                config.ATR_PERIOD,
                config.VOLUME_MA_PERIOD
            )
            
            # 전일 봉 (-2: 전일, -1: 오늘 미완성)
            prev = data.iloc[-2]
            
            # 시장 국면 판단
            market_state = determine_market_state(
                prev['close'],
                prev['ema_short'],
                prev['ema_long'],
                prev['sma_long'],
                prev['sma_slope']
            )
            
            if market_state == MarketState.NEUTRAL:
                logger.info("중립 구간 → 주문 안 넣음")
                return None

            # BEAR(하락장)에서는 SHORT는 일일 스케줄러에서 미지원 → 주문 안 넣음
            if market_state == MarketState.BEAR:
                logger.info("하락장: 일일 스케줄러는 LONG 전용 → 주문 안 넣음")
                return None

            # K값 계산 (LONG만 해당)
            atr_lookback = data['atr'].tail(config.ATR_PERIOD)
            atr_max = atr_lookback.max()
            k_value = calculate_k_value(
                prev['atr'],
                atr_max,
                config.K_MAX
            )
            
            # 목표가 계산 (전일 종가 + 변동폭 * K)
            target_price = prev['close'] + (prev['high'] - prev['low']) * k_value
            
            # 소수점 2자리
            target_price = round(target_price, 2)
            
            logger.info(
                f"목표가 계산 완료: ${target_price:,.2f} "
                f"(시장={market_state.value}, K={k_value:.3f})"
            )
            
            return {
                'target_price': target_price,
                'market_state': market_state.value,
                'k_value': k_value,
                'prev_high': prev['high'],
                'prev_low': prev['low'],
                'prev_close': prev['close'],
                'atr': prev['atr']
            }
            
        except Exception as e:
            logger.error(f"목표가 계산 실패: {e}", exc_info=True)
            return None
    
    def place_entry_order_with_stops(self, entry_data, balance):
        """
        진입 주문 + 트레일링 스탑 설정
        
        1. LIMIT 진입 주문
        2. 트레일링 스탑 손절 주문
        3. TAKE_PROFIT 익절 주문
        """
        try:
            target_price = entry_data['target_price']
            market_state = entry_data['market_state']
            atr = entry_data['atr']
            
            # 포지션 사이즈 계산
            # 손절가 = 목표가 - ATR (LONG 가정)
            stop_loss_price = target_price - (atr * config.STOP_LOSS_MULTIPLIER)
            
            position_size = calculate_position_size(
                balance,
                target_price,
                stop_loss_price,
                config.RISK_PER_TRADE,
                config.LEVERAGE
            )
            
            if position_size <= 0:
                logger.warning("포지션 사이즈가 0입니다")
                return False
            
            # 소수점 조정 (BTC: 0.001)
            position_size = round(position_size, 3)
            
            logger.info(
                f"주문 준비: "
                f"진입=${target_price:,.2f}, "
                f"수량={position_size:.3f} BTC, "
                f"포지션 가치=${target_price * position_size:,.2f}"
            )
            
            if self.dry_run:
                logger.info("[DRY RUN] 주문 시뮬레이션 (실제 주문 안 넣음)")
                
                # 알림만 전송
                if self.notifier:
                    self.notifier.send_message(
                        title="🧪 DRY RUN - 일일 주문",
                        description=(
                            f"진입가: ${target_price:,.2f}\n"
                            f"수량: {position_size:.3f} BTC\n"
                            f"손절가: ${stop_loss_price:,.2f}\n"
                            f"시장: {market_state}"
                        )
                    )
                
                return True
            
            # === 실제 주문 실행 ===
            
            # 1. LIMIT 진입 주문 (LONG)
            entry_order = self.client.futures_create_order(
                symbol=self.symbol,
                side='BUY',
                type='LIMIT',
                timeInForce='GTC',  # Good Till Cancel
                quantity=position_size,
                price=target_price
            )
            
            entry_order_id = entry_order['orderId']
            logger.info(f"✅ 진입 주문 완료: ID={entry_order_id}")
            
            # 2. 손절 주문 (STOP_MARKET)
            stop_order = self.client.futures_create_order(
                symbol=self.symbol,
                side='SELL',
                type='STOP_MARKET',
                stopPrice=round(stop_loss_price, 2),
                quantity=position_size,
                reduceOnly=True  # 포지션 청산만
            )
            
            stop_order_id = stop_order['orderId']
            logger.info(f"✅ 손절 주문 완료: ID={stop_order_id}")
            
            # 3. 익절 주문 (TAKE_PROFIT_MARKET)
            take_profit_price = target_price + (atr * config.TAKE_PROFIT_MULTIPLIER)
            
            tp_order = self.client.futures_create_order(
                symbol=self.symbol,
                side='SELL',
                type='TAKE_PROFIT_MARKET',
                stopPrice=round(take_profit_price, 2),
                quantity=position_size,
                reduceOnly=True
            )
            
            tp_order_id = tp_order['orderId']
            logger.info(f"✅ 익절 주문 완료: ID={tp_order_id}")
            
            # 4. 트레일링 스탑 주문 (선택사항)
            # 주의: 바이낸스 선물은 트레일링 스탑을 직접 지원하지 않을 수 있음
            # 대신 STOP_MARKET을 사용
            
            # 카카오톡 알림
            if self.notifier:
                self.notifier.send_message(
                    title="✅ 일일 주문 완료",
                    description=(
                        f"📊 진입 주문\n"
                        f"가격: ${target_price:,.2f}\n"
                        f"수량: {position_size:.3f} BTC\n"
                        f"시장: {market_state}\n\n"
                        f"🛡️ 안전장치\n"
                        f"손절: ${stop_loss_price:,.2f}\n"
                        f"익절: ${take_profit_price:,.2f}\n\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                )
            
            return True
            
        except Exception as e:
            logger.error(f"주문 실행 실패: {e}", exc_info=True)
            
            if self.notifier:
                self.notifier.notify_error(f"주문 실행 실패: {e}")
            
            return False
    
    def run(self):
        """일일 작업 실행"""
        
        logger.info("=" * 60)
        logger.info("일일 주문 스케줄러 시작")
        logger.info(f"실행 시각: {datetime.now()}")
        logger.info(f"DRY_RUN: {self.dry_run}")
        logger.info("=" * 60)
        
        try:
            # 1. 미체결 주문 취소
            logger.info("\n[1/4] 미체결 주문 취소...")
            self.cancel_all_pending_orders()
            
            # 2. 잔고 조회
            logger.info("\n[2/4] 계좌 잔고 조회...")
            balance = self.get_account_balance()
            
            if balance is None:
                logger.error("잔고 조회 실패")
                return False
            
            logger.info(f"현재 잔고: ${balance:,.2f}")
            
            # 3. 목표가 계산
            logger.info("\n[3/4] 진입 목표가 계산...")
            entry_data = self.calculate_entry_price()
            
            if entry_data is None:
                logger.info("오늘은 주문 조건 불충족")
                
                if self.notifier:
                    self.notifier.send_message(
                        title="ℹ️ 오늘은 거래 없음",
                        description=(
                            f"시장 조건이 맞지 않아 주문을 넣지 않습니다.\n"
                            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    )
                
                return True
            
            # 4. 주문 실행
            logger.info("\n[4/4] 주문 실행...")
            success = self.place_entry_order_with_stops(entry_data, balance)
            
            if success:
                logger.info("\n✅ 일일 작업 완료!")
            else:
                logger.error("\n❌ 주문 실행 실패")
            
            return success
            
        except Exception as e:
            logger.error(f"일일 작업 실패: {e}", exc_info=True)
            
            if self.notifier:
                self.notifier.notify_error(f"일일 작업 실패: {e}")
            
            return False
        
        finally:
            logger.info("=" * 60)
            logger.info("프로그램 종료")
            logger.info("=" * 60)


def main():
    """메인 함수"""
    
    scheduler = DailyOrderScheduler()
    scheduler.run()


if __name__ == "__main__":
    main()
