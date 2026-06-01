# -*- coding: utf-8 -*-
"""
趋势交易分析器（移植自 github.com/Neolinnnn/-）
供雙篩選流程使用：對今日雙條件推薦個股進行 MA/MACD/RSI/量能 分析，輸出買入建議。
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_BIAS_THRESHOLD = 5.0  # 乖离率阈值（%），超过此值提示不追高


class TrendStatus(Enum):
    STRONG_BULL = "強勢多頭"
    BULL = "多頭排列"
    WEAK_BULL = "弱勢多頭"
    CONSOLIDATION = "盤整"
    WEAK_BEAR = "弱勢空頭"
    BEAR = "空頭排列"
    STRONG_BEAR = "強勢空頭"


class VolumeStatus(Enum):
    HEAVY_VOLUME_UP = "放量上漲"
    HEAVY_VOLUME_DOWN = "放量下跌"
    SHRINK_VOLUME_UP = "縮量上漲"
    SHRINK_VOLUME_DOWN = "縮量回調"
    NORMAL = "量能正常"


class BuySignal(Enum):
    STRONG_BUY = "強力買入"
    BUY = "買入"
    HOLD = "持有"
    WAIT = "觀望"
    SELL = "賣出"
    STRONG_SELL = "強力賣出"


class MACDStatus(Enum):
    GOLDEN_CROSS_ZERO = "零軸上金叉"
    GOLDEN_CROSS = "金叉"
    BULLISH = "多頭"
    CROSSING_UP = "上穿零軸"
    CROSSING_DOWN = "下穿零軸"
    BEARISH = "空頭"
    DEATH_CROSS = "死叉"


class RSIStatus(Enum):
    OVERBOUGHT = "超買"
    STRONG_BUY = "強勢"
    NEUTRAL = "中性"
    WEAK = "弱勢"
    OVERSOLD = "超賣"


@dataclass
class TrendAnalysisResult:
    code: str
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""
    support_ma5: bool = False
    support_ma10: bool = False
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'trend_status': self.trend_status.value,
            'ma_alignment': self.ma_alignment,
            'trend_strength': self.trend_strength,
            'ma5': round(self.ma5, 2),
            'ma10': round(self.ma10, 2),
            'ma20': round(self.ma20, 2),
            'ma60': round(self.ma60, 2),
            'current_price': self.current_price,
            'bias_ma5': round(self.bias_ma5, 2),
            'bias_ma10': round(self.bias_ma10, 2),
            'bias_ma20': round(self.bias_ma20, 2),
            'volume_status': self.volume_status.value,
            'volume_ratio_5d': round(self.volume_ratio_5d, 2),
            'volume_trend': self.volume_trend,
            'support_ma5': self.support_ma5,
            'support_ma10': self.support_ma10,
            'buy_signal': self.buy_signal.value,
            'signal_score': self.signal_score,
            'signal_reasons': self.signal_reasons,
            'risk_factors': self.risk_factors,
            'macd_dif': round(self.macd_dif, 4),
            'macd_dea': round(self.macd_dea, 4),
            'macd_bar': round(self.macd_bar, 4),
            'macd_status': self.macd_status.value,
            'macd_signal': self.macd_signal,
            'rsi_6': round(self.rsi_6, 1),
            'rsi_12': round(self.rsi_12, 1),
            'rsi_24': round(self.rsi_24, 1),
            'rsi_status': self.rsi_status.value,
            'rsi_signal': self.rsi_signal,
        }


class StockTrendAnalyzer:
    VOLUME_SHRINK_RATIO = 0.7
    VOLUME_HEAVY_RATIO = 1.5
    MA_SUPPORT_TOLERANCE = 0.02
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL_PERIOD = 9
    RSI_SHORT = 6
    RSI_MID = 12
    RSI_LONG = 24
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30

    def analyze(self, df: pd.DataFrame, code: str) -> TrendAnalysisResult:
        result = TrendAnalysisResult(code=code)
        if df is None or df.empty or len(df) < 20:
            result.risk_factors.append("資料不足，無法完成分析")
            return result
        df = df.sort_values('date').reset_index(drop=True)
        df = self._calculate_mas(df)
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)
        latest = df.iloc[-1]
        result.current_price = float(latest['close'])
        result.ma5  = float(latest['MA5'])
        result.ma10 = float(latest['MA10'])
        result.ma20 = float(latest['MA20'])
        result.ma60 = float(latest.get('MA60', 0))
        self._analyze_trend(df, result)
        self._calculate_bias(result)
        self._analyze_volume(df, result)
        self._analyze_support_resistance(df, result)
        self._analyze_macd(df, result)
        self._analyze_rsi(df, result)
        self._generate_signal(result)
        return result

    def _calculate_mas(self, df):
        df = df.copy()
        df['MA5']  = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['MA60'] = df['close'].rolling(60).mean() if len(df) >= 60 else df['MA20']
        return df

    def _calculate_macd(self, df):
        df = df.copy()
        ema_fast = df['close'].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.MACD_SLOW, adjust=False).mean()
        df['MACD_DIF'] = ema_fast - ema_slow
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=self.MACD_SIGNAL_PERIOD, adjust=False).mean()
        df['MACD_BAR'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2
        return df

    def _calculate_rsi(self, df):
        df = df.copy()
        for period in [self.RSI_SHORT, self.RSI_MID, self.RSI_LONG]:
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
            rs = avg_gain / avg_loss
            df[f'RSI_{period}'] = (100 - 100 / (1 + rs)).fillna(50)
        return df

    def _analyze_trend(self, df, result):
        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20
        if ma5 > ma10 > ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA5'] - prev['MA20']) / prev['MA20'] * 100 if prev['MA20'] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "強勢多頭排列，均線發散上行"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "多頭排列 MA5>MA10>MA20"
                result.trend_strength = 75
        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "弱勢多頭，MA5>MA10 但 MA10≤MA20"
            result.trend_strength = 55
        elif ma5 < ma10 < ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA20'] - prev['MA5']) / prev['MA5'] * 100 if prev['MA5'] > 0 else 0
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BEAR
                result.ma_alignment = "強勢空頭排列，均線發散下行"
                result.trend_strength = 10
            else:
                result.trend_status = TrendStatus.BEAR
                result.ma_alignment = "空頭排列 MA5<MA10<MA20"
                result.trend_strength = 25
        elif ma5 < ma10 and ma10 >= ma20:
            result.trend_status = TrendStatus.WEAK_BEAR
            result.ma_alignment = "弱勢空頭，MA5<MA10 但 MA10≥MA20"
            result.trend_strength = 40
        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "均線糾纏，趨勢不明"
            result.trend_strength = 50

    def _calculate_bias(self, result):
        p = result.current_price
        if result.ma5  > 0: result.bias_ma5  = (p - result.ma5)  / result.ma5  * 100
        if result.ma10 > 0: result.bias_ma10 = (p - result.ma10) / result.ma10 * 100
        if result.ma20 > 0: result.bias_ma20 = (p - result.ma20) / result.ma20 * 100

    def _analyze_volume(self, df, result):
        if len(df) < 5:
            return
        latest = df.iloc[-1]
        vol_5d_avg = df['volume'].iloc[-6:-1].mean()
        if vol_5d_avg > 0:
            result.volume_ratio_5d = float(latest['volume']) / vol_5d_avg
        prev_close = df.iloc[-2]['close']
        price_change = (latest['close'] - prev_close) / prev_close * 100
        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
                result.volume_trend = "放量上漲，多頭力量強勁"
            else:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
                result.volume_trend = "放量下跌，注意風險"
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
                result.volume_trend = "縮量上漲，上攻動能不足"
            else:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
                result.volume_trend = "縮量回調，洗盤特徵明顯（佳）"
        else:
            result.volume_status = VolumeStatus.NORMAL
            result.volume_trend = "量能正常"

    def _analyze_support_resistance(self, df, result):
        p = result.current_price
        if result.ma5 > 0 and abs(p - result.ma5) / result.ma5 <= self.MA_SUPPORT_TOLERANCE and p >= result.ma5:
            result.support_ma5 = True
            result.support_levels.append(result.ma5)
        if result.ma10 > 0 and abs(p - result.ma10) / result.ma10 <= self.MA_SUPPORT_TOLERANCE and p >= result.ma10:
            result.support_ma10 = True
            if result.ma10 not in result.support_levels:
                result.support_levels.append(result.ma10)
        if result.ma20 > 0 and p >= result.ma20:
            result.support_levels.append(result.ma20)
        if len(df) >= 20:
            recent_high = df['high'].iloc[-20:].max()
            if recent_high > p:
                result.resistance_levels.append(float(recent_high))

    def _analyze_macd(self, df, result):
        if len(df) < self.MACD_SLOW:
            result.macd_signal = "資料不足"
            return
        latest = df.iloc[-1]
        prev   = df.iloc[-2]
        result.macd_dif = float(latest['MACD_DIF'])
        result.macd_dea = float(latest['MACD_DEA'])
        result.macd_bar = float(latest['MACD_BAR'])
        prev_diff = prev['MACD_DIF'] - prev['MACD_DEA']
        curr_diff = result.macd_dif - result.macd_dea
        is_golden  = prev_diff <= 0 and curr_diff > 0
        is_death   = prev_diff >= 0 and curr_diff < 0
        is_cross_up   = prev['MACD_DIF'] <= 0 and result.macd_dif > 0
        is_cross_down = prev['MACD_DIF'] >= 0 and result.macd_dif < 0
        if is_golden and result.macd_dif > 0:
            result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
            result.macd_signal = "零軸上金叉，強力買入訊號"
        elif is_cross_up:
            result.macd_status = MACDStatus.CROSSING_UP
            result.macd_signal = "DIF上穿零軸，趨勢轉強"
        elif is_golden:
            result.macd_status = MACDStatus.GOLDEN_CROSS
            result.macd_signal = "金叉，趨勢向上"
        elif is_death:
            result.macd_status = MACDStatus.DEATH_CROSS
            result.macd_signal = "死叉，趨勢向下"
        elif is_cross_down:
            result.macd_status = MACDStatus.CROSSING_DOWN
            result.macd_signal = "DIF下穿零軸，趨勢轉弱"
        elif result.macd_dif > 0 and result.macd_dea > 0:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "多頭排列，持續上漲"
        elif result.macd_dif < 0 and result.macd_dea < 0:
            result.macd_status = MACDStatus.BEARISH
            result.macd_signal = "空頭排列，持續下跌"
        else:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "MACD 中性區域"

    def _analyze_rsi(self, df, result):
        if len(df) < self.RSI_LONG:
            result.rsi_signal = "資料不足"
            return
        latest = df.iloc[-1]
        result.rsi_6  = float(latest[f'RSI_{self.RSI_SHORT}'])
        result.rsi_12 = float(latest[f'RSI_{self.RSI_MID}'])
        result.rsi_24 = float(latest[f'RSI_{self.RSI_LONG}'])
        rsi = result.rsi_12
        if rsi > self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
            result.rsi_signal = f"RSI超買({rsi:.1f}>70)，短期回調風險高"
        elif rsi > 60:
            result.rsi_status = RSIStatus.STRONG_BUY
            result.rsi_signal = f"RSI強勢({rsi:.1f})，多頭力量充足"
        elif rsi >= 40:
            result.rsi_status = RSIStatus.NEUTRAL
            result.rsi_signal = f"RSI中性({rsi:.1f})，震盪整理中"
        elif rsi >= self.RSI_OVERSOLD:
            result.rsi_status = RSIStatus.WEAK
            result.rsi_signal = f"RSI弱勢({rsi:.1f})，關注反彈"
        else:
            result.rsi_status = RSIStatus.OVERSOLD
            result.rsi_signal = f"RSI超賣({rsi:.1f}<30)，反彈機會大"

    def _generate_signal(self, result):
        score = 0
        reasons = []
        risks = []

        # 趨勢（30分）
        trend_scores = {
            TrendStatus.STRONG_BULL: 30, TrendStatus.BULL: 26,
            TrendStatus.WEAK_BULL: 18,   TrendStatus.CONSOLIDATION: 12,
            TrendStatus.WEAK_BEAR: 8,    TrendStatus.BEAR: 4,
            TrendStatus.STRONG_BEAR: 0,
        }
        score += trend_scores.get(result.trend_status, 12)
        if result.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL):
            reasons.append(f"{result.trend_status.value}，順勢做多")
        elif result.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR):
            risks.append(f"{result.trend_status.value}，不宜做多")

        # 乖離率（20分）
        bias = result.bias_ma5 or 0.0
        effective_threshold = (
            _BIAS_THRESHOLD * 1.5
            if result.trend_status == TrendStatus.STRONG_BULL and result.trend_strength >= 70
            else _BIAS_THRESHOLD
        )
        if bias < 0:
            if bias > -3:
                score += 20; reasons.append(f"價格略低於MA5({bias:.1f}%)，回踩買點")
            elif bias > -5:
                score += 16; reasons.append(f"價格回踩MA5({bias:.1f}%)，觀察支撐")
            else:
                score += 8;  risks.append(f"乖離率過大({bias:.1f}%)，可能破位")
        elif bias < 2:
            score += 18; reasons.append(f"價格貼近MA5({bias:.1f}%)，介入好時機")
        elif bias < _BIAS_THRESHOLD:
            score += 14; reasons.append(f"價格略高於MA5({bias:.1f}%)，可小倉介入")
        elif bias > effective_threshold:
            score += 4;  risks.append(f"乖離率過高({bias:.1f}%>{effective_threshold:.1f}%)，嚴禁追高")
        else:
            score += 4;  risks.append(f"乖離率過高({bias:.1f}%)，嚴禁追高")

        # 量能（15分）
        vol_scores = {
            VolumeStatus.SHRINK_VOLUME_DOWN: 15, VolumeStatus.HEAVY_VOLUME_UP: 12,
            VolumeStatus.NORMAL: 10, VolumeStatus.SHRINK_VOLUME_UP: 6,
            VolumeStatus.HEAVY_VOLUME_DOWN: 0,
        }
        score += vol_scores.get(result.volume_status, 8)
        if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            reasons.append("縮量回調，主力洗盤")
        elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
            risks.append("放量下跌，注意風險")

        # 支撐（10分）
        if result.support_ma5:  score += 5; reasons.append("MA5支撐有效")
        if result.support_ma10: score += 5; reasons.append("MA10支撐有效")

        # MACD（15分）
        macd_scores = {
            MACDStatus.GOLDEN_CROSS_ZERO: 15, MACDStatus.GOLDEN_CROSS: 12,
            MACDStatus.CROSSING_UP: 10,       MACDStatus.BULLISH: 8,
            MACDStatus.BEARISH: 2, MACDStatus.CROSSING_DOWN: 0, MACDStatus.DEATH_CROSS: 0,
        }
        score += macd_scores.get(result.macd_status, 5)
        if result.macd_status in (MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS):
            reasons.append(result.macd_signal)
        elif result.macd_status in (MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN):
            risks.append(result.macd_signal)
        else:
            reasons.append(result.macd_signal)

        # RSI（10分）
        rsi_scores = {
            RSIStatus.OVERSOLD: 10, RSIStatus.STRONG_BUY: 8,
            RSIStatus.NEUTRAL: 5,   RSIStatus.WEAK: 3, RSIStatus.OVERBOUGHT: 0,
        }
        score += rsi_scores.get(result.rsi_status, 5)
        if result.rsi_status in (RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY):
            reasons.append(result.rsi_signal)
        elif result.rsi_status == RSIStatus.OVERBOUGHT:
            risks.append(result.rsi_signal)
        else:
            reasons.append(result.rsi_signal)

        result.signal_score   = score
        result.signal_reasons = reasons
        result.risk_factors   = risks

        if score >= 75 and result.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL):
            result.buy_signal = BuySignal.STRONG_BUY
        elif score >= 60 and result.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL, TrendStatus.WEAK_BULL):
            result.buy_signal = BuySignal.BUY
        elif score >= 45:
            result.buy_signal = BuySignal.HOLD
        elif score >= 30:
            result.buy_signal = BuySignal.WAIT
        elif result.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR):
            result.buy_signal = BuySignal.STRONG_SELL
        else:
            result.buy_signal = BuySignal.SELL


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    return StockTrendAnalyzer().analyze(df, code)
