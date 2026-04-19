"""FinMind DataLoader 統一入口，自動讀取 token"""
import os
from pathlib import Path


def _load_env():
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_dataloader():
    _load_env()
    from FinMind.data import DataLoader
    dl = DataLoader()
    token = os.environ.get('FINMIND_TOKEN')
    if token:
        dl.login_by_token(api_token=token)
    return dl
