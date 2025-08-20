from datetime import datetime
from zoneinfo import ZoneInfo  # Python 3.9+
import json

def normalize_llm_text(s: str) -> str:
    s = s.strip()
    # 如果整段被引號包住（常見於回傳 JSON 字串），用 json.loads 解碼
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            s = json.loads(s)
        except Exception:
            s = s[1:-1]  # 解不開就退而求其次就暴力拆
    # 統一換行
    s = s.replace('\r\n', '\n')
    return s

def event_hour_yyyymmddhh(event_ts_ms: int, tz: str = "Asia/Taipei") -> str:
    dt = datetime.fromtimestamp(event_ts_ms / 1000, ZoneInfo(tz))
    return dt.strftime("%Y%m%d%H")