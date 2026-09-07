"""
BTC 데이 확장형 트레이딩 전략 패키지
"""

__version__ = "1.0.0"
__author__ = "Your Name"
__description__ = "BTC Day Extension Trading Strategy with Backtest"

from . import config
from . import data_loader
from . import indicators
from . import market_regime
from . import strategy_entry
from . import strategy_exit
from . import risk_manager
from . import backtest_engine
from . import performance

# visualization은 matplotlib에 의존하며 백테스트 차트 전용이다.
# 라이브 매매 서버에는 matplotlib이 없을 수 있으므로 선택적으로 로드한다.
try:
    from . import visualization  # noqa: F401
    _HAS_VISUALIZATION = True
except ImportError:
    visualization = None  # type: ignore[assignment]
    _HAS_VISUALIZATION = False

__all__ = [
    'config',
    'data_loader',
    'indicators',
    'market_regime',
    'strategy_entry',
    'strategy_exit',
    'risk_manager',
    'backtest_engine',
    'performance',
    'visualization'
]
