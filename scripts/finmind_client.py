"""FinMind DataLoader 統一入口（委派到 datafeed，取得多 token 輪替支援）。"""
from datafeed import make_dataloader


def get_dataloader():
    return make_dataloader()
