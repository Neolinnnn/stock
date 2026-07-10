"""
matplotlib 中文字型設定（跨平台）。
本機用 Microsoft JhengHei，CI（Ubuntu）用 apt 安裝的 Noto Sans CJK。
自動偵測系統實際安裝的 CJK 字型，避免因字型缺失產生「豆腐方塊」。
"""
from matplotlib import font_manager, rcParams

# 依偏好排序的候選字型（macOS / Windows / Linux 常見 CJK 字型）
_CJK_CANDIDATES = [
    'Microsoft JhengHei', 'PingFang TC', 'Heiti TC',
    'Noto Sans CJK TC', 'Noto Sans CJK JP', 'Noto Sans CJK SC',
    'Noto Sans TC', 'Source Han Sans TW', 'WenQuanYi Zen Hei',
    'SimHei', 'Arial Unicode MS',
]


def setup_cjk_font() -> str | None:
    """設定 rcParams 中文字型，回傳實際採用的字型名稱（找不到則 None）。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = [n for n in _CJK_CANDIDATES if n in available]
    # 選中的排前面，其餘候選留作 fallback
    rcParams['font.sans-serif'] = chosen + [n for n in _CJK_CANDIDATES if n not in chosen]
    rcParams['axes.unicode_minus'] = False
    if not chosen:
        print('[WARN] 未偵測到任何 CJK 字型，圖表中文可能顯示為方塊')
        return None
    return chosen[0]
