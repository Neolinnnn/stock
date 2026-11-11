"""gemini_writer 呼叫層的錯誤處理（以 monkeypatch 取代 urlopen，不打網路）。"""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gemini_writer as gw  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(payload):
    return lambda req, timeout=None: _Resp(json.dumps(payload).encode())


def test_gemini_joins_all_text_parts(monkeypatch):
    payload = {'candidates': [{'content': {'parts': [
        {'text': '{"a": '}, {'text': 'thinking', 'thought': True}, {'text': '1}'}]}}]}
    monkeypatch.setattr(gw.urllib.request, 'urlopen', _ok(payload))
    assert gw.call_gemini('p', keys=['k']) == '{"a": 1}'


def test_gemini_blocked_response_is_runtime_error(monkeypatch):
    monkeypatch.setattr(gw.urllib.request, 'urlopen', _ok({'promptFeedback': {}}))
    with pytest.raises(RuntimeError):
        gw.call_gemini('p', keys=['k'])


def test_groq_connection_error_normalized(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError('timed out')
    monkeypatch.setattr(gw.urllib.request, 'urlopen', boom)
    with pytest.raises(RuntimeError, match='Groq 連線失敗'):
        gw.call_groq('p', keys=['k'])


def test_call_llm_wraps_both_failures(monkeypatch):
    # Gemini 與 Groq 都斷線時，呼叫端要拿到 RuntimeError（含兩邊原因），不是 URLError
    def boom(req, timeout=None):
        raise urllib.error.URLError('down')
    monkeypatch.setattr(gw.urllib.request, 'urlopen', boom)
    monkeypatch.setenv('GROQ_API_KEY', 'g')
    with pytest.raises(RuntimeError, match='皆失敗'):
        gw.call_llm('p', gemini_keys=['k'])
