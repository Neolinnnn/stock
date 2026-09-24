"""台股交易日曆：判斷某日是否為當週最後一個交易日。

休市日來源：證交所 openapi 開（休）市日期（免認證，只列當年度）。
清單中「開始交易日／最後交易日」是交易日的公告，不是休市日。
取不到清單時退回「週五＝最後交易日」，與原排程行為一致。

用法：python scripts/trading_calendar.py [YYYYMMDD]
      → 印出「是否交易日 是否當週最後交易日」，例：true false
"""
import json
import ssl
import sys
import urllib.request
from datetime import date, datetime, timedelta

HOLIDAY_URL = 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule'


def fetch_holidays() -> set[date] | None:
    # 證交所憑證缺 SKI，新版 Python 嚴格驗證會失敗；比照 rotation_radar.fetch_json 降級重試（公開資料）
    rows = None
    for ctx in (None, ssl._create_unverified_context()):
        try:
            with urllib.request.urlopen(HOLIDAY_URL, timeout=20, context=ctx) as r:
                rows = json.load(r)
            break
        except Exception as e:
            err = e
    if rows is None:
        print(f'[trading_calendar] 休市日清單取得失敗，退回週五規則：{err}', file=sys.stderr)
        return None
    out = set()
    for row in rows:
        name, roc = row.get('Name', ''), row.get('Date', '')
        if '開始交易' in name or '最後交易' in name or len(roc) != 7:
            continue
        out.add(date(int(roc[:3]) + 1911, int(roc[3:5]), int(roc[5:])))
    return out


def is_trading_day(d: date, holidays: set[date] | None) -> bool:
    return d.weekday() < 5 and d not in (holidays or ())


def is_last_trading_day_of_week(d: date, holidays: set[date] | None) -> bool:
    """d 為交易日，且 d 之後到週五都休市。"""
    if holidays is None:
        return d.weekday() == 4
    if not is_trading_day(d, holidays):
        return False
    return all(not is_trading_day(d + timedelta(days=i), holidays)
               for i in range(1, 5 - d.weekday()))


if __name__ == '__main__':
    d = datetime.strptime(sys.argv[1], '%Y%m%d').date() if len(sys.argv) > 1 else date.today()
    hol = fetch_holidays()
    fmt = lambda b: 'true' if b else 'false'   # noqa: E731
    print(fmt(is_trading_day(d, hol)), fmt(is_last_trading_day_of_week(d, hol)))
