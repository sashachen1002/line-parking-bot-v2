import argparse
import asyncio
from gettext import find
import json
import math
import os
import sys
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from starlette.requests import Request
from starlette.responses import JSONResponse

import requests
from pydantic import BaseModel, Field, field_validator

# MCP SDK
from fastmcp import FastMCP
from dotenv import load_dotenv
load_dotenv()

mcp = FastMCP(name="Parking")
app_id = os.getenv("TDX_APP_ID")
app_key = os.getenv("TDX_APP_KEY")


class City(str, Enum):
    Taipei = "Taipei"
    NewTaipei = "NewTaipei"
    Taichung = "Taichung"
    Tainan = "Tainan"
    Kaohsiung = "Kaohsiung"
    Keelung = "Keelung"
    Hsinchu = "Hsinchu"
    Chiayi = "Chiayi"


class ParkingType(str, Enum):
    OffStreet = "OffStreet"
    OnStreet = "OnStreet"


class ParkingItem(BaseModel):
    type: ParkingType = Field(description="OffStreet=停車場, OnStreet=路邊")
    id: str = Field(description="停車場或路邊車位 ID")
    name: str = Field(description="停車場名稱或路段名稱")
    available_spaces: Union[int, str] = Field(description="剩餘車位數，可能為數字或 '未知'")
    rates: Optional[str] = Field(default=None, description="收費方案")
    service_time: Optional[str] = Field(default=None, description="營業/收費時段")


class ParkingResponse(BaseModel):
    status: Literal["success", "error"]
    message: Optional[str] = None
    data: Optional[List[ParkingItem]] = None

    @field_validator("data")
    @classmethod
    def _ensure_list(cls, v):
        return v or []
    
    
# =========================
# Utilities
# =========================

def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returns distance in meters between two lat/lon points."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _get_env_flag(name: str) -> bool:
    return os.getenv(name, "").strip() in {"1", "true", "True", "YES", "yes"}


# =========================
# Inline TDX backend (optional)
# =========================

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"  # official token endpoint
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic"


class TDXAuthError(RuntimeError):
    pass

def _tdx_get_token(app_id: str, app_key: str) -> str:
    """
    Client Credentials to get OAuth token from TDX.
    """
    headers = {"content-type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_key,
    }
    r = requests.post(TDX_TOKEN_URL, data=data, headers=headers, timeout=15)
    if r.status_code != 200:
        raise TDXAuthError(f"TDX token failed: HTTP {r.status_code} - {r.text[:200]}")
    token = r.json().get("access_token")
    if not token:
        raise TDXAuthError("TDX token response missing access_token")
    return token


def _tdx_get_json(path: str, token: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    GET JSON array from TDX basic API with Bearer token.
    """
    url = f"{TDX_API_BASE}{path}"
    headers = {
        "authorization": f"Bearer {token}",
        "accept-encoding": "gzip",
        "user-agent": "Mozilla/5.0"  # TDX sometimes rejects 'curl' UA; using a browser UA is safe.
    }
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"TDX GET {path} failed: HTTP {resp.status_code} - {resp.text[:200]}")
    try:
        return resp.json()
    except Exception:
        print("resp.text:", resp.text)
        # Some TDX endpoints can default to XML if $format not set; force JSON if needed
        raise RuntimeError(f"TDX GET {path} returned non-JSON payload.")


def _extract_first(values: Dict[str, Any], keys: Tuple[str, ...], default=None):
    for k in keys:
        if k in values and values[k] is not None:
            return values[k]
    return default


def _extract_name(name_field: Any) -> str:
    """
    CarParkName might be a plain string or an object with Zh_tw/En.
    """
    if isinstance(name_field, str):
        return name_field
    if isinstance(name_field, dict):
        return name_field.get("Zh_tw") or name_field.get("En") or json.dumps(name_field, ensure_ascii=False)
    return str(name_field) if name_field is not None else ""


def _extract_position(obj: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """
    Try several field shapes for lat/lon in OffStreet results.
    """
    # Common shape: Position -> PositionLat/PositionLon
    pos = obj.get("Position") or obj.get("CarParkPosition") or obj.get("EntrancePosition")
    if isinstance(pos, dict):
        lat = _extract_first(pos, ("PositionLat", "Lat", "Latitude"))
        lon = _extract_first(pos, ("PositionLon", "Lon", "Longitude"))
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
    # Flat fields
    lat = _extract_first(obj, ("PositionLat", "Lat", "Latitude"))
    lon = _extract_first(obj, ("PositionLon", "Lon", "Longitude"))
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


def _inline_tdx_find_parking(input_json: Dict[str, Any], app_id: str, app_key: str) -> Dict[str, Any]:
    """
    Minimal inline TDX implementation, focusing on OffStreet data.
    Tries also OnStreet availability if Lat/Lon present in payload (optional).
    """
    lat = float(input_json["latitude"])
    lon = float(input_json["longitude"])
    radius = int(input_json.get("radius", 1000))
    city = str(input_json["city"])

    token = _tdx_get_token(app_id, app_key)

    # OffStreet: basic + availability (join by ID/UID-like fields)
    carparks = _tdx_get_json(f"/v1/Parking/OffStreet/CarPark/City/{city}", token, params={"$format": "JSON"})
    carparks = carparks.get('CarParks')
    availability = _tdx_get_json(f"/v1/Parking/OffStreet/ParkingAvailability/City/{city}", token, params={"$format": "JSON"})
    # print("availability:", availability)
    availability = availability.get('ParkingAvailabilities')
    # print("availability:", availability)

    # print("carparks:", carparks)
    # print('carparks:', type(carparks))
    # print('isinstance(carparks):', isinstance(carparks, list))

    # Build quick index for availability by some ID/UID-ish key
    # We attempt common identifiers without assuming the exact schema
    avail_index: Dict[str, Dict[str, Any]] = {}
    for a in availability if isinstance(availability, list) else []:
        key = _extract_first(a, ("CarParkID", "CarParkUID", "ID", "UID", "CarParkNo", "CarParkCode"))
        if key:
            avail_index[str(key)] = a

    results: List[Dict[str, Any]] = []
    for cp in carparks if isinstance(carparks, list) else []:
        # print("\n\ncp:", cp)
        pos = _extract_position(cp)
        if not pos:
            continue
        cplat, cplon = pos
        dist = _haversine_meters(lat, lon, cplat, cplon)
        if dist > radius:
            continue

        # Identify & name
        cid = _extract_first(cp, ("CarParkID", "CarParkUID", "ID", "UID", "CarParkNo", "CarParkCode"))
        name = _extract_name(_extract_first(cp, ("CarParkName", "Name", "CarparkName", "carparkName"), ""))

        # Try availability join
        avail_obj = avail_index.get(str(cid)) or {}
        # Common fields in availability payload
        available = _extract_first(avail_obj, ("AvailableSpaces", "AvailableCar", "availablecar", "available_spaces"))
        if available is None:
            available = "未知"

        rates = _extract_first(cp, ("FareDescription", "FareInfo", "Pricing", "rates"))
        if isinstance(rates, dict):
            rates = rates.get("Zh_tw") or rates.get("En") or json.dumps(rates, ensure_ascii=False)
        service_time = _extract_first(cp, ("ServiceTime", "service_time"))

        item = {
            "type": "OffStreet",
            "id": str(cid) if cid is not None else name or "UNKNOWN",
            "name": name or "（未命名停車場）",
            "available_spaces": available,
            "rates": rates,
            "service_time": service_time,
        }
        results.append(item)

    # Optional: OnStreet dynamic availability (if data includes positions)
    # Endpoint name derived from TDX docs (may vary by city coverage)
    try:
        onstreet = _tdx_get_json(f"/v1/Parking/OnStreet/ParkingCurbSegmentAvailability/City/{city}", token, params={"$format": "JSON"})
        for seg in onstreet if isinstance(onstreet, list) else []:
            # Attempt to find a representative coordinate (center)
            spos = None
            # Try common patterns
            refpos = seg.get("ReferencePosition") or seg.get("Position") or seg.get("CenterPosition")
            if isinstance(refpos, dict):
                s_lat = _extract_first(refpos, ("PositionLat", "Lat", "Latitude"))
                s_lon = _extract_first(refpos, ("PositionLon", "Lon", "Longitude"))
                if isinstance(s_lat, (int, float)) and isinstance(s_lon, (int, float)):
                    spos = (float(s_lat), float(s_lon))
            if not spos:
                continue
            d = _haversine_meters(lat, lon, spos[0], spos[1])
            if d > radius:
                continue

            sid = _extract_first(seg, ("SegmentID", "CurbID", "SegmentUID", "ID", "UID"))
            sname = _extract_first(seg, ("RoadName", "SegmentName", "Name"), "")
            avail = _extract_first(seg, ("AvailableSpaces", "SpacesAvailable", "available_spaces"))
            if avail is None:
                avail = "未知"
            rates = _extract_first(seg, ("FareDescription", "Rate", "rates"))
            service_time = _extract_first(seg, ("ServiceTime", "ChargeTime", "service_time"))

            results.append({
                "type": "OnStreet",
                "id": str(sid) if sid is not None else sname or "SEGMENT",
                "name": str(sname) or "（未命名路段）",
                "available_spaces": avail,
                "rates": rates,
                "service_time": service_time,
            })
    except Exception:
        # Silently skip OnStreet if endpoint/city not supported or fields absent.
        pass

    return {"status": "success", "data": results}

def _call_backend(latitude: float, longitude: float, radius: int, city: str) -> Dict[str, Any]:
    """
    Dispatch to stub, inline TDX, or external parking_tool_api.
    """
    # Stub for quick local testing
    if _get_env_flag("MCP_PARKING_USE_STUB"):
        return {
            "status": "success",
            "data": [
                {
                    "type": "OffStreet",
                    "id": "DEMO-001",
                    "name": "示範停車場",
                    "available_spaces": 42,
                    "rates": "小客車 30元/小時",
                    "service_time": "00:00-24:00",
                },
                {
                    "type": "OnStreet",
                    "id": "SEG-100",
                    "name": "示範路段",
                    "available_spaces": "未知",
                    "rates": "平日 20元/小時",
                    "service_time": "09:00-18:00",
                },
            ],
        }
    app_id = os.getenv("TDX_APP_ID")
    app_key = os.getenv("TDX_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Missing TDX credentials (TDX_APP_ID / TDX_APP_KEY).")
    return _inline_tdx_find_parking(
        {"latitude": latitude, "longitude": longitude, "radius": radius, "city": city},
        app_id,
        app_key,
    )


@mcp.tool()
async def find_parking(
    latitude: float = Field(..., description="查詢中心點緯度，例如 25.0375"),
    longitude: float = Field(..., description="查詢中心點經度，例如 121.5637"),
    city: City = Field(..., description="TDX 支援的英文縣市代碼"),
    radius: int = Field(1000, ge=1, le=1000, description="查詢半徑(公尺)，預設(最大)為 1000"),
) -> ParkingResponse:
    """
    查詢中心點附近（半徑最多 1000 公尺）的停車場與路邊車位，回傳 JSON 結果。
    Input/Output strictly follow your schemas.
    """
    raw = await asyncio.to_thread(_call_backend, latitude, longitude, radius, city.value)
    return ParkingResponse.model_validate(raw)

def _find_parking(
    latitude: float = Field(..., description="查詢中心點緯度，例如 25.0375"),
    longitude: float = Field(..., description="查詢中心點經度，例如 121.5637"),
    city: City = Field(..., description="TDX 支援的英文縣市代碼"),
    radius: int = Field(1000, ge=1, le=1000, description="查詢半徑(公尺)，預設(最大)為 1000"),
) -> ParkingResponse:
    """
    查詢中心點附近（半徑最多 1000 公尺）的停車場與路邊車位，回傳 JSON 結果。
    Input/Output strictly follow your schemas.
    """
    raw = _call_backend(latitude, longitude, radius, city.value)
    return ParkingResponse.model_validate(raw)

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({"status": "200"})

async def main():
    await mcp.run_async(transport="streamable-http", host='0.0.0.0', port=9001)
    
if __name__ == "__main__":
    asyncio.run(main())
    # print(_find_parking(latitude=25.0375, longitude=121.5637, city=City.Taipei, radius=1000))
    