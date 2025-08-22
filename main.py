import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime
from urllib.parse import quote

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    QuickReply,
    MessageAction,
)
from linebot.v3.messaging.models import (
    QuickReplyItem,
    StickerMessage,
    ShowLoadingAnimationRequest,
    PushMessageRequest,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
)
from dotenv import load_dotenv
import json

# === Google Sheets API ===
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Utils (你需要確保這個模組存在) ===
try:
    from utils.utils import normalize_llm_text, event_hour_yyyymmddhh
except ImportError:
    # 如果 utils 模組不存在，提供預設實作
    def normalize_llm_text(text):
        return text.strip()
    
    def event_hour_yyyymmddhh(timestamp):
        return datetime.fromtimestamp(timestamp / 1000).strftime('%Y%m%d%H')

# -----------------------------------------------------------------------------
# 基本設定
# -----------------------------------------------------------------------------
load_dotenv()
app = Flask(__name__)

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
llm_api_base = os.getenv("LLM_API_BASE", "http://localhost:8000")

# 背景執行緒池（依你的流量調整）
executor = ThreadPoolExecutor(max_workers=8)

# 共用 requests Session（連線重用、較省時）
_requests_session = requests.Session()

# 用戶狀態管理
user_state = {}
user_location = {}
user_selected_toilet = {}  # 紀錄使用者要評分的廁所

# === 連線 Google Sheet (安全版本) ===
def init_google_sheet():
    try:
        # 方法 1: 從環境變數讀取 JSON 內容
        google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if google_credentials_json:
            # 將環境變數的 JSON 字串轉為字典
            creds_dict = json.loads(google_credentials_json)
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            sheet_id = os.getenv("GOOGLE_SHEET_ID", "1WgWnSofHnYnA40HhucWN9HzbcglkOF9-RqAgNyNAyng")
            sheet = client.open_by_key(sheet_id).sheet1
            print("Google Sheet 連線成功 (環境變數)")
            return sheet
        
        # 方法 2: 如果是本地開發，嘗試讀取檔案
        elif os.path.exists('data/tranquil-apogee-424105-h3-e118373644f0.json'):
            scope = ['https://spreadsheets.google.com/feeds',
                    'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                'data/tranquil-apogee-424105-h3-e118373644f0.json', scope)
            client = gspread.authorize(creds)
            sheet_id = os.getenv("GOOGLE_SHEET_ID", "1WgWnSofHnYnA40HhucWN9HzbcglkOF9-RqAgNyNAyng")
            sheet = client.open_by_key(sheet_id).sheet1
            print("Google Sheet 連線成功 (本地檔案)")
            return sheet
        
        else:
            print("Google Sheet 憑證未設定 - 評分功能將無法使用")
            return None
            
    except Exception as e:
        print(f"Google Sheet 連線失敗: {e}")
        return None

# 初始化 Google Sheet
sheet = init_google_sheet()

# === 載入公廁資料 ===
try:
    toilet_df = pd.read_csv("data/臺北市公廁點位資訊.csv")
    print(f"成功載入 {len(toilet_df)} 筆公廁資料")
except Exception as e:
    print(f"載入公廁資料失敗: {e}")
    toilet_df = pd.DataFrame()

# -----------------------------------------------------------------------------
# AI Chatbot 相關函數
# -----------------------------------------------------------------------------
def call_llm(user_id: str, query: str) -> str:
    """
    呼叫你的 LLM 服務。
    - timeout 拆成 (connect, read)：避免卡在連線建立。
    - 回傳純文字，失敗則回覆友善訊息。
    """
    try:
        r = _requests_session.get(
            f"{llm_api_base}/chat",
            params={"user_id": user_id, "query": query},
            timeout=(5, 30)  # 5秒連線，30秒讀取
        )
        r.raise_for_status()
        return r.text.strip()
    except requests.exceptions.RequestException as e:
        app.logger.error(f"LLM 呼叫失敗：{e}")
        return "有一些問題發生 ... 請稍後再試"

def _push_text(user_id: str, text: str):
    """
    封裝 Push API（不像 reply token 時限）
    """
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=text)],
                )
            )
    except Exception as e:
        app.logger.error(f"Push message 失敗: {e}")

def process_and_push_text(user_id: str, user_id_with_session: str, query: str):
    """
    背景任務：呼叫 LLM → 正規化 → Push 回使用者
    """
    answer = call_llm(user_id=user_id_with_session, query=query)
    answer = normalize_llm_text(answer)
    _push_text(user_id, answer)

# -----------------------------------------------------------------------------
# 原有功能：距離計算與附近廁所搜尋
# -----------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    """計算兩點間距離（公尺）"""
    R = 6371  # 地球半徑 (公里)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c * 1000  # 回傳公尺

def find_nearby_toilets(lat, lon, top_n=5):
    """尋找附近的公廁"""
    if toilet_df.empty:
        return pd.DataFrame()
    
    toilet_df_copy = toilet_df.copy()
    toilet_df_copy["距離"] = toilet_df_copy.apply(
        lambda row: haversine(lat, lon, row["緯度"], row["經度"]), axis=1
    )
    return toilet_df_copy.sort_values("距離").head(top_n)

# -----------------------------------------------------------------------------
# 原有功能：停車場資訊
# -----------------------------------------------------------------------------
def send_parking_info(event):
    """發送停車場資訊"""
    dict_result = [
        {
            'name': '附中公園地下停車場',
            'type': '路外停車場',
            'available_seats': '3',
            'cost': '- 小型車（含大型重型機車）：\n- 白天（08:00-21:00）：50元/小時\n- 夜間（21:00-08:00）：10元/小時'
        },
        {
            'name': '大安高工地下停車場',
            'type': '路外停車場',
            'available_seats': '124',
            'cost': '- 小型車及大型重型機車: \n - 白天（09:00-21:00）：50元/小時 \n- 夜間（21:00-09:00）：10元/小時'
        }
    ]

    bubbles = []
    for info in dict_result:
        google_url = 'https://www.google.com/maps/search/?api=1&query=' + quote(info['name'])
        bubble_data = {
            "type": "bubble",
            "hero": {
                "type": "image", 
                "url": "https://developers-resource.landpress.line.me/fx/img/01_1_cafe.png",
                "size": "full", 
                "aspectRatio": "20:13", 
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": info["name"], "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"類型：{info['type']}", "size": "sm", "color": "#666666"},
                    {"type": "text", "text": f"空位：{info['available_seats']}", "size": "sm", "color": "#666666"},
                    {"type": "text", "text": f"費率：{info['cost']}", "size": "xxs", "wrap": True, "color": "#666666"}
                ]
            },
            "footer": {
                "type": "box", 
                "layout": "vertical", 
                "contents": [
                    {
                        "type": "button", 
                        "style": "link", 
                        "height": "sm",
                        "action": {"type": "uri", "label": "Google Map", "uri": google_url}
                    }
                ]
            }
        }
        bubbles.append(bubble_data)

    # 使用字典格式創建 Flex Message
    flex_content = {
        "type": "carousel",
        "contents": bubbles
    }
    
    flex_message = FlexMessage(alt_text="附近停車場清單", contents=flex_content)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[flex_message]
            )
        )

# -----------------------------------------------------------------------------
# 原有功能：公共廁所資訊
# -----------------------------------------------------------------------------
def send_toilet_info(event, location):
    """發送公廁資訊"""
    if toilet_df.empty:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="抱歉，無法載入公廁資料")]
                )
            )
        return

    lat, lon = map(float, location.split(","))
    nearby = find_nearby_toilets(lat, lon)

    if nearby.empty:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="附近沒有找到公廁資料")]
                )
            )
        return

    bubbles = []
    for _, t in nearby.iterrows():
        bubble_data = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": str(t["公廁名稱"]), "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"地址：{t['公廁地址']}", "size": "sm", "wrap": True, "color": "#666666"},
                    {"type": "text", "text": f"距離：約 {int(t['距離'])} 公尺", "size": "sm", "color": "#666666"},
                    {"type": "text", "text": f"總座數：{int(t['座數'])}", "size": "sm", "color": "#666666"},
                    {"type": "text", "text": f"無障礙廁座數：{int(t['無障礙廁座數'])}", "size": "sm", "color": "#666666"},
                    {"type": "text", "text": f"親子廁座數：{int(t['親子廁座數'])}", "size": "sm", "color": "#666666"},
                ]
            },
            "footer": {
                "type": "box", 
                "layout": "vertical", 
                "contents": [
                    {
                        "type": "button", 
                        "style": "link", 
                        "height": "sm",
                        "action": {
                            "type": "uri", 
                            "label": "Google Map",
                            "uri": f"https://www.google.com/maps/search/?api=1&query={t['緯度']},{t['經度']}"
                        }
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "message",
                            "label": "我要評分💩",
                            "text": f"評分準備|{t['公廁名稱']}|{t['公廁地址']}"
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble_data)

    # 使用字典格式創建 Flex Message
    flex_content = {
        "type": "carousel",
        "contents": bubbles
    }
    
    flex_message = FlexMessage(alt_text="附近公共廁所清單", contents=flex_content)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[flex_message]
            )
        )

# -----------------------------------------------------------------------------
# Flask / LINE Webhook
# -----------------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info(
            "Invalid signature. Please check your channel access token/channel secret."
        )
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    hour_suffix = event_hour_yyyymmddhh(event.timestamp)
    user_id_with_session = f"{user_id}:{hour_suffix}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # === 處理評分相關訊息 ===
        if text.startswith("評分準備|"):
            try:
                _, toilet_name, toilet_address = text.split("|")
                user_selected_toilet[user_id] = {
                    "name": toilet_name,
                    "address": toilet_address
                }

                quick_reply = QuickReply(items=[
                    QuickReplyItem(action=MessageAction(label="💩", text="評分_1")),
                    QuickReplyItem(action=MessageAction(label="💩💩", text="評分_2")),
                    QuickReplyItem(action=MessageAction(label="💩💩💩", text="評分_3")),
                    QuickReplyItem(action=MessageAction(label="💩💩💩💩", text="評分_4")),
                    QuickReplyItem(action=MessageAction(label="💩💩💩💩💩", text="評分_5")),
                ])
                
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text=f"你選擇評分的廁所是：「{toilet_name}」，請給分（💩越多越讚）：",
                            quickReply=quick_reply
                        )]
                    )
                )
            except ValueError:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="評分格式錯誤")]
                    )
                )
            return

        elif text.startswith("評分_"):
            try:
                score = int(text.split("_")[1])
                toilet_info = user_selected_toilet.get(user_id)
                if toilet_info and sheet:
                    sheet.append_row([toilet_info["name"], score])
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(
                                text=f"感謝你對「{toilet_info['name']}」的評分！你的評分是：{'💩'*score}"
                            )]
                        )
                    )
                    user_selected_toilet[user_id] = None
                else:
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="請先選擇要評分的廁所。")]
                        )
                    )
            except Exception as e:
                app.logger.error(f"評分發生錯誤: {e}")
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="評分發生錯誤")]
                    )
                )
            return

        # === 處理停車場查詢 ===
        elif text == "尋找附近停車位":
            if user_location.get(user_id):
                quick_reply = QuickReply(items=[
                    QuickReplyItem(action=MessageAction(label="用原本位置", text="停車位_原位置")),
                    QuickReplyItem(action=MessageAction(label="重新定位", text="停車位_重新定位"))
                ])
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text="你之前有傳過位置，想用原本位置還是重新定位？",
                            quickReply=quick_reply
                        )]
                    )
                )
            else:
                user_state[user_id] = "等待位置_停車場"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="請提供位置資訊，讓我幫你找附近的停車場！")]
                    )
                )
            return

        elif text == "停車位_原位置":
            send_parking_info(event)
            return
        elif text == "停車位_重新定位":
            user_state[user_id] = "等待位置_停車場"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="請提供新的位置資訊，讓我幫你找附近的停車場！")]
                )
            )
            return

        # === 處理公廁查詢 ===
        elif text == "查詢公共廁所":
            if user_location.get(user_id):
                quick_reply = QuickReply(items=[
                    QuickReplyItem(action=MessageAction(label="用原本位置", text="廁所_原位置")),
                    QuickReplyItem(action=MessageAction(label="重新定位", text="廁所_重新定位"))
                ])
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text="你之前有傳過位置，想用原本位置還是重新定位？",
                            quickReply=quick_reply
                        )]
                    )
                )
            else:
                user_state[user_id] = "等待位置_公共廁所"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="請提供位置資訊，讓我幫你找附近的公共廁所！")]
                    )
                )
            return

        elif text == "廁所_原位置":
            send_toilet_info(event, user_location[user_id])
            return
        elif text == "廁所_重新定位":
            user_state[user_id] = "等待位置_公共廁所"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="請提供新的位置資訊，讓我幫你找附近的公共廁所！")]
                )
            )
            return

        # === 處理排行榜查詢 ===
        elif text == "查看排行":
            if not sheet:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="無法連接評分資料庫")]
                    )
                )
                return

            try:
                data = sheet.get_all_values()
                if len(data) > 1:  # 有資料
                    df = pd.DataFrame(data[1:], columns=data[0])
                    df["評分"] = df["評分"].astype(float)
                    avg_score = df.groupby("地點")["評分"].mean().reset_index()
                    avg_score = avg_score.sort_values("評分", ascending=False).head(5)

                    bubbles = []
                    for idx, row in avg_score.iterrows():
                        bubble_data = {
                            "type": "bubble",
                            "body": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {"type": "text", "text": f"🏆 No.{len(bubbles)+1}", "weight": "bold", "size": "lg"},
                                    {"type": "text", "text": row["地點"], "weight": "bold", "size": "xl", "wrap": True},
                                    {"type": "text", "text": f"平均分數：{round(row['評分'],1)} 💩", "size": "md", "color": "#666666"}
                                ]
                            }
                        }
                        bubbles.append(bubble_data)

                    # 使用字典格式創建 Flex Message
                    flex_content = {
                        "type": "carousel",
                        "contents": bubbles
                    }
                    
                    flex_message = FlexMessage(alt_text="公廁排行榜", contents=flex_content)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[flex_message]
                        )
                    )
                else:
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="目前還沒有任何評分紀錄。")]
                        )
                    )
            except Exception as e:
                app.logger.error(f"查看排行發生錯誤: {e}")
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="查看排行發生錯誤")]
                    )
                )
            return

        # === 其他訊息：使用 AI Chatbot 處理 ===
        else:
            # 顯示 loading（只是前端體感，與 webhook 逾時無關）
            try:
                line_bot_api.show_loading_animation(
                    ShowLoadingAnimationRequest(chat_id=user_id, loadingSeconds=60)
                )
            except Exception:
                app.logger.warning("show loading animation failed, continue ...")

            # 立刻回覆簡短訊息，避免 reply token 超時
            try:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="讓我想想...")]
                    )
                )
            except Exception as e:
                app.logger.warning(f"quick reply failed: {e}")

            # 把重運算丟到背景（ThreadPoolExecutor）
            executor.submit(
                process_and_push_text,
                user_id,
                user_id_with_session,
                text,
            )
            return

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    user_id = event.source.user_id
    lat, lon = event.message.latitude, event.message.longitude
    city = event.message.title or ""
    address = event.message.address or ""
    user_location[user_id] = f"{lat},{lon}"

    hour_suffix = event_hour_yyyymmddhh(event.timestamp)
    user_id_with_session = f"{user_id}:{hour_suffix}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if user_state.get(user_id) == "等待位置_停車場":
            # 立刻回覆，然後處理停車場資訊
            try:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="收到定位，我來幫你找停車場～")]
                    )
                )
            except Exception as e:
                app.logger.warning(f"quick reply failed: {e}")

            user_state[user_id] = None
            
            # 使用 AI 來處理停車場查詢
            query = f"緯度：{lat}, 經度：{lon} {city} {address} 附近有什麼停車場"
            
            try:
                line_bot_api.show_loading_animation(
                    ShowLoadingAnimationRequest(chat_id=user_id, loadingSeconds=60)
                )
            except Exception:
                app.logger.warning("show loading animation failed, continue ...")

            # 背景跑 LLM，再用 push 回完整結果
            executor.submit(
                process_and_push_text,
                user_id,
                user_id_with_session,
                query,
            )

        elif user_state.get(user_id) == "等待位置_公共廁所":
            send_toilet_info(event, user_location[user_id])
            user_state[user_id] = None

        else:
            # 沒有特定狀態，使用 AI 處理位置訊息
            query = f"緯度：{lat}, 經度：{lon} {city} {address} 這個位置有什麼特色或附近有什麼"
            
            try:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="收到位置資訊，讓我看看這附近有什麼～")]
                    )
                )
            except Exception as e:
                app.logger.warning(f"quick reply failed: {e}")
            
            try:
                line_bot_api.show_loading_animation(
                    ShowLoadingAnimationRequest(chat_id=user_id, loadingSeconds=60)
                )
            except Exception:
                app.logger.warning("show loading animation failed, continue ...")

            executor.submit(
                process_and_push_text,
                user_id,
                user_id_with_session,
                query,
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
