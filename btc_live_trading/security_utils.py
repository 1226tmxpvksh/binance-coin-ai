"""
보안 유틸리티 모듈
입력 검증, 데이터 sanitization, 민감 정보 보호
"""

import logging
import re
import secrets
from typing import Any, Dict, Optional
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


class InputValidator:
    """입력 검증 클래스"""
    
    # 허용된 로그 레벨
    ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    
    # 허용된 심볼 패턴 (대문자와 숫자만)
    SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]{3,20}$')
    
    # 허용된 타임프레임
    ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
    
    @staticmethod
    def validate_log_level(level: str) -> str:
        """
        로그 레벨 검증
        
        Args:
            level: 로그 레벨 문자열
        
        Returns:
            str: 검증된 로그 레벨
        
        Raises:
            ValueError: 잘못된 로그 레벨
        """
        level_upper = level.upper()
        
        if level_upper not in InputValidator.ALLOWED_LOG_LEVELS:
            logger.error(f"잘못된 로그 레벨: {level}")
            raise ValueError(
                f"허용되지 않은 로그 레벨입니다. "
                f"허용: {InputValidator.ALLOWED_LOG_LEVELS}"
            )
        
        return level_upper
    
    @staticmethod
    def validate_symbol(symbol: str) -> str:
        """
        거래 심볼 검증
        
        Args:
            symbol: 거래 심볼
        
        Returns:
            str: 검증된 심볼
        
        Raises:
            ValueError: 잘못된 심볼
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError("심볼은 비어있을 수 없습니다")
        
        if not InputValidator.SYMBOL_PATTERN.match(symbol):
            raise ValueError(
                f"잘못된 심볼 형식: {symbol}. "
                f"대문자와 숫자만 허용됩니다 (예: BTCUSDT)"
            )
        
        return symbol
    
    @staticmethod
    def validate_timeframe(timeframe: str) -> str:
        """
        타임프레임 검증
        
        Args:
            timeframe: 타임프레임
        
        Returns:
            str: 검증된 타임프레임
        
        Raises:
            ValueError: 잘못된 타임프레임
        """
        if timeframe not in InputValidator.ALLOWED_TIMEFRAMES:
            raise ValueError(
                f"잘못된 타임프레임: {timeframe}. "
                f"허용: {InputValidator.ALLOWED_TIMEFRAMES}"
            )
        
        return timeframe
    
    @staticmethod
    def validate_percentage(value: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
        """
        퍼센트 값 검증
        
        Args:
            value: 퍼센트 값
            min_val: 최소값
            max_val: 최대값
        
        Returns:
            float: 검증된 값
        
        Raises:
            ValueError: 범위를 벗어난 값
        """
        if not isinstance(value, (int, float)):
            raise ValueError(f"숫자가 아닙니다: {value}")
        
        if value < min_val or value > max_val:
            raise ValueError(
                f"퍼센트 값이 범위를 벗어났습니다: {value} "
                f"(허용 범위: {min_val}~{max_val})"
            )
        
        return float(value)
    
    @staticmethod
    def validate_leverage(leverage: int) -> int:
        """
        레버리지 검증
        
        Args:
            leverage: 레버리지 배수
        
        Returns:
            int: 검증된 레버리지
        
        Raises:
            ValueError: 잘못된 레버리지
        """
        if not isinstance(leverage, int):
            raise ValueError(f"레버리지는 정수여야 합니다: {leverage}")
        
        if leverage < 1 or leverage > 125:
            raise ValueError(
                f"레버리지가 범위를 벗어났습니다: {leverage} "
                f"(허용 범위: 1~125)"
            )
        
        return leverage
    
    @staticmethod
    def validate_positive_number(value: float, name: str = "값") -> float:
        """
        양수 검증
        
        Args:
            value: 검증할 값
            name: 값의 이름
        
        Returns:
            float: 검증된 값
        
        Raises:
            ValueError: 양수가 아닌 값
        """
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name}은(는) 숫자여야 합니다: {value}")
        
        if value <= 0:
            raise ValueError(f"{name}은(는) 양수여야 합니다: {value}")
        
        return float(value)
    
    @staticmethod
    def validate_api_response(response: Dict[str, Any], required_keys: list) -> bool:
        """
        API 응답 검증
        
        Args:
            response: API 응답 딕셔너리
            required_keys: 필수 키 리스트
        
        Returns:
            bool: 검증 성공 여부
        """
        if not isinstance(response, dict):
            logger.error("API 응답이 딕셔너리가 아닙니다")
            return False
        
        missing_keys = [key for key in required_keys if key not in response]
        
        if missing_keys:
            logger.error(f"API 응답에 필수 키가 없습니다: {missing_keys}")
            return False
        
        return True


class DataSanitizer:
    """데이터 정제 클래스 - 민감한 정보 보호"""
    
    @staticmethod
    def sanitize_api_key(api_key: str) -> str:
        """
        API 키 마스킹
        
        Args:
            api_key: API 키
        
        Returns:
            str: 마스킹된 API 키
        """
        if not api_key or len(api_key) < 8:
            return "***"
        
        # 앞 4자리와 뒤 4자리만 표시
        return f"{api_key[:4]}...{api_key[-4:]}"
    
    @staticmethod
    def sanitize_token(token: str) -> str:
        """
        토큰 마스킹
        
        Args:
            token: 액세스 토큰
        
        Returns:
            str: 마스킹된 토큰
        """
        if not token or len(token) < 8:
            return "***"
        
        return f"{token[:4]}...{token[-4:]}"
    
    @staticmethod
    def sanitize_error_message(error_msg: str, max_length: int = 200) -> str:
        """
        에러 메시지 정제 - 민감한 정보 제거
        
        Args:
            error_msg: 에러 메시지
            max_length: 최대 길이
        
        Returns:
            str: 정제된 에러 메시지
        """
        # API 키나 토큰 패턴 제거
        patterns = [
            (r'api[_-]?key["\']?\s*[:=]\s*["\']?[\w-]+', 'api_key=***'),
            (r'secret["\']?\s*[:=]\s*["\']?[\w-]+', 'secret=***'),
            (r'token["\']?\s*[:=]\s*["\']?[\w-]+', 'token=***'),
            (r'password["\']?\s*[:=]\s*["\']?[\w-]+', 'password=***'),
        ]
        
        sanitized = error_msg
        for pattern, replacement in patterns:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        
        # 길이 제한
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "..."
        
        return sanitized
    
    @staticmethod
    def sanitize_order_info(order: Dict[str, Any]) -> Dict[str, Any]:
        """
        주문 정보 정제 - 로깅용
        
        Args:
            order: 주문 정보
        
        Returns:
            Dict: 정제된 주문 정보
        """
        if not isinstance(order, dict):
            return {}
        
        # 필요한 정보만 추출
        safe_fields = {
            'orderId', 'symbol', 'side', 'type', 'status',
            'origQty', 'executedQty', 'avgPrice'
        }
        
        return {k: v for k, v in order.items() if k in safe_fields}


class SecureComparison:
    """안전한 비교 연산 - 타이밍 공격 방지"""
    
    @staticmethod
    def constant_time_compare(a: str, b: str) -> bool:
        """
        일정 시간 문자열 비교 (타이밍 공격 방지)
        
        Args:
            a: 첫 번째 문자열
            b: 두 번째 문자열
        
        Returns:
            bool: 비교 결과
        """
        return secrets.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


class ConfigValidator:
    """설정 검증 클래스"""
    
    @staticmethod
    def validate_config(config_dict: Dict[str, Any]) -> None:
        """
        전체 설정 검증
        
        Args:
            config_dict: 설정 딕셔너리
        
        Raises:
            ValueError: 잘못된 설정
        """
        validator = InputValidator()
        
        # 필수 설정 확인
        required_keys = [
            'SYMBOL', 'LEVERAGE', 'RISK_PER_TRADE',
            'DAILY_MAX_LOSS_PERCENT', 'EMERGENCY_STOP_LOSS_PERCENT'
        ]
        
        for key in required_keys:
            if key not in config_dict:
                raise ValueError(f"필수 설정이 없습니다: {key}")
        
        # 개별 검증
        validator.validate_symbol(config_dict['SYMBOL'])
        validator.validate_leverage(config_dict['LEVERAGE'])
        validator.validate_percentage(
            config_dict['RISK_PER_TRADE'],
            min_val=0.001,
            max_val=10.0
        )
        validator.validate_percentage(
            config_dict['DAILY_MAX_LOSS_PERCENT'],
            min_val=1.0,
            max_val=50.0
        )
        validator.validate_percentage(
            config_dict['EMERGENCY_STOP_LOSS_PERCENT'],
            min_val=5.0,
            max_val=100.0
        )
        
        # 논리적 검증
        if config_dict['DAILY_MAX_LOSS_PERCENT'] >= config_dict['EMERGENCY_STOP_LOSS_PERCENT']:
            raise ValueError(
                "일일 최대 손실이 긴급 정지 손실보다 크거나 같을 수 없습니다"
            )
        
        logger.info("✅ 설정 검증 완료")


def sanitize_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """
    로그 레코드 정제
    
    Args:
        record: 로그 레코드
    
    Returns:
        logging.LogRecord: 정제된 로그 레코드
    """
    sanitizer = DataSanitizer()
    
    # 메시지에서 민감한 정보 제거
    if hasattr(record, 'msg'):
        if isinstance(record.msg, str):
            record.msg = sanitizer.sanitize_error_message(record.msg, max_length=500)
    
    return record
