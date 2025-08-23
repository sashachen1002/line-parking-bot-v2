import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, LocationMessage,
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from urllib.parse import quote
from dotenv import load_dotenv
import pandas as pd
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime
import json

# === Google Sheets API ===
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Utils ===
def normalize_llm_text(text):
    return text.strip()

def event_hour_yyyymmddhh(timestamp):
    return datetime.fromtimestamp(timestamp / 1000).strftime('%Y%m%d%H')

# === 初始化 Flask ===
load_dotenv()
app = Flask(__name__)

line_bot_api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET"))

# AI Chatbot 設定
llm_api_base = os.getenv("LLM_API_BASE", "http://localhost:8000")
executor = ThreadPoolExecutor(max_workers=8)
_requests_session = requests.Session()

# 用戶狀態管理
user_state = {}
user_location = {}
user_selected_toilet = {}

# === 連線 Google Sheet ===
def init_google_sheet():
    try:
        google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if google_credentials_json:
            creds_dict = json.loads(google_credentials_json)
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            sheet_id = os.getenv("GOOGLE_SHEET_ID", "1WgWnSofHnYnA40HhucWN9HzbcglkOF9-RqAgNyNAyng")
            sheet = client.open_by_key(sheet_id).sheet1
            print("Google Sheet 連線成功 (環境變數)")
            return sheet
        else:
            print("Google Sheet 憑證未設定")
            return None
    except Exception as e:
        print(f"Google Sheet 連線失敗: {e}")
        return None

sheet = init_google_sheet()

# === 載入公廁資料 ===
try:
    toilet_df = pd.read_csv("data/臺北市公廁點位資訊.csv")
    print(f"✅ 成功載入 {len(toilet_df)} 筆公廁資料")
    print(f"欄位名稱: {list(toilet_df.columns)}")
except Exception as e:
    print(f"❌ 載入公廁資料失敗: {e}")
    toilet_df = pd.DataFrame()

# === AI 相關函數 ===
def call_llm(user_id: str, query: str) -> str:
    try:
        r = _requests_session.get(
            f"{llm_api_base}/chat",
            params={"user_id": user_id, "query": query},
            timeout=(5, 30)
        )
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:
        return "AI 暫時無法回應，請稍後再試"

def process_and_push_text(user_id: str, user_id_with_session: str, query: str):
    try:
        answer = call_llm(user_id=user_id_with_session, query=query)
        answer = normalize_llm_text(answer)
        line_bot_api.push_message(user_id, TextSendMessage(text=answer))
    except Exception as e:
        print(f"Push message 失敗: {e}")

# === 距離計算 ===
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c * 1000

def find_nearby_toilets(lat, lon, top_n=5):
    if toilet_df.empty:
        return pd.DataFrame()
    
    toilet_df_copy = toilet_df.copy()
    toilet_df_copy["距離"] = toilet_df_copy.apply(
        lambda row: haversine(lat, lon, row["緯度"], row["經度"]), axis=1
    )
    return toilet_df_copy.sort_values("距離").head(top_n)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/health", methods=["GET"])
def health_check():
    return {
        "status": "healthy",
        "toilet_data_loaded": len(toilet_df) > 0,
        "toilet_rows": len(toilet_df),
        "google_sheet_connected": sheet is not None,
        "columns": list(toilet_df.columns) if not toilet_df.empty else []
    }

# ===== 除錯測試函數 =====
def test_simple_flex():
    """測試最簡單的 Flex Message"""
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "測試卡片", "weight": "bold", "size": "xl"},
                {"type": "text", "text": "如果你看到這個，代表 Flex Message 正常運作！", "wrap": True}
            ]
        }
    }

def test_simple_carousel():
    """測試簡單的 Carousel"""
    bubbles = []
    for i in range(3):
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"卡片 {i+1}", "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"這是第 {i+1} 張測試卡片", "wrap": True}
                ]
            }
        }
        bubbles.append(bubble)
    
    return {"type": "carousel", "contents": bubbles}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    
    print(f"收到訊息: '{text}' from {user_id}")

    # === 除錯測試命令 ===
    if text == "測試卡片":
        flex_content = test_simple_flex()
        flex_message = FlexSendMessage(alt_text="測試卡片", contents=flex_content)
        line_bot_api.reply_message(event.reply_token, flex_message)
        return
    
    if text == "測試輪播":
        flex_content = test_simple_carousel()
        flex_message = FlexSendMessage(alt_text="測試輪播", contents=flex_content)
        line_bot_api.reply_message(event.reply_token, flex_message)
        return
    
    if text == "檢查資料":
        info_text = f"""
資料檢查結果：
📊 公廁資料：{len(toilet_df)} 筆
🔗 Google Sheet：{'已連線' if sheet else '未連線'}
📁 欄位：{list(toilet_df.columns)[:5] if not toilet_df.empty else '無資料'}
        """.strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=info_text))
        return

    # === 評分相關 ===
    if text.startswith("評分準備|"):
        try:
            _, toilet_name, toilet_address = text.split("|")
            user_selected_toilet[user_id] = {"name": toilet_name, "address": toilet_address}
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="💩", text="評分_1")),
                QuickReplyButton(action=MessageAction(label="💩💩", text="評分_2")),
                QuickReplyButton(action=MessageAction(label="💩💩💩", text="評分_3")),
                QuickReplyButton(action=MessageAction(label="💩💩💩💩", text="評分_4")),
                QuickReplyButton(action=MessageAction(label="💩💩💩💩💩", text="評分_5")),
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"你選擇評分的廁所是：「{toilet_name}」，請給分（💩越多越讚）：", quick_reply=quick_reply)
            )
        except Exception as e:
            print(f"評分準備錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="評分格式錯誤"))
        return

    elif text.startswith("評分_"):
        try:
            score = int(text.split("_")[1])
            toilet_info = user_selected_toilet.get(user_id)
            if toilet_info and sheet:
                sheet.append_row([toilet_info["name"], score])
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"感謝你對「{toilet_info['name']}」的評分！你的評分是：{'💩'*score}")
                )
                user_selected_toilet[user_id] = None
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先選擇要評分的廁所。"))
        except Exception as e:
            print(f"評分錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="評分發生錯誤"))
        return

    # === 停車場查詢 ===
    if text == "尋找附近停車位":
        print("進入停車場查詢")
        if user_location.get(user_id):
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="用原本位置", text="停車位_原位置")),
                QuickReplyButton(action=MessageAction(label="重新定位", text="停車位_重新定位"))
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="你之前有傳過位置，想用原本位置還是重新定位？", quick_reply=quick_reply)
            )
        else:
            user_state[user_id] = "等待位置_停車場"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請提供位置資訊，讓我幫你找附近的停車場！"))
        return

    elif text == "停車位_原位置":
        print("使用原位置查詢停車場")
        send_parking_info(event)
        return
        
    elif text == "停車位_重新定位":
        user_state[user_id] = "等待位置_停車場"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請提供新的位置資訊，讓我幫你找附近的停車場！"))
        return

    # === 公廁查詢 ===
    if text == "查詢公共廁所":
        print("進入公廁查詢")
        if user_location.get(user_id):
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="用原本位置", text="廁所_原位置")),
                QuickReplyButton(action=MessageAction(label="重新定位", text="廁所_重新定位"))
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="你之前有傳過位置，想用原本位置還是重新定位？", quick_reply=quick_reply)
            )
        else:
            user_state[user_id] = "等待位置_公共廁所"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請提供位置資訊，讓我幫你找附近的公共廁所！"))
        return

    elif text == "廁所_原位置":
        print("使用原位置查詢公廁")
        if user_location.get(user_id):
            send_toilet_info(event, user_location[user_id])
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="找不到之前的位置資訊"))
        return
        
    elif text == "廁所_重新定位":
        user_state[user_id] = "等待位置_公共廁所"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請提供新的位置資訊，讓我幫你找附近的公共廁所！"))
        return

    # === 排行榜查詢 ===
    if text == "查看排行":
        print("進入排行榜查詢")
        if not sheet:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="無法連接評分資料庫"))
            return

        try:
            # 測試：先顯示簡單的排行榜
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="排行榜功能開發中，請先使用其他功能"))
            return
        except Exception as e:
            print(f"排行榜錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="查看排行發生錯誤"))
        return

    # === AI 處理其他訊息 ===
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="讓我想想..."))
    hour_suffix = event_hour_yyyymmddhh(event.timestamp)
    user_id_with_session = f"{user_id}:{hour_suffix}"
    executor.submit(process_and_push_text, user_id, user_id_with_session, text)

@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):
    user_id = event.source.user_id
    lat, lon = event.message.latitude, event.message.longitude
    user_location[user_id] = f"{lat},{lon}"
    
    print(f"收到位置: {lat}, {lon} from {user_id}")

    if user_state.get(user_id) == "等待位置_停車場":
        print("處理停車場位置")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到定位，我來幫你找停車場～"))
        
        # 這裡暫時不用 AI，直接顯示測試卡片
        send_parking_info(event)
        user_state[user_id] = None
        
    elif user_state.get(user_id) == "等待位置_公共廁所":
        print("處理公廁位置")
        send_toilet_info(event, user_location[user_id])
        user_state[user_id] = None
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到位置資訊！"))

def send_parking_info(event):
    print("開始生成停車場卡片")
    try:
        # 最簡單的測試卡片
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "測試停車場", "weight": "bold", "size": "xl"},
                    {"type": "text", "text": "這是停車場測試卡片", "wrap": True},
                    {"type": "text", "text": "如果你看到這個卡片，代表停車場功能正常！", "wrap": True}
                ]
            }
        }
        
        flex_message = FlexSendMessage(alt_text="停車場資訊", contents=bubble)
        line_bot_api.reply_message(event.reply_token, flex_message)
        print("停車場卡片發送成功")
    except Exception as e:
        print(f"停車場卡片發送失敗: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"停車場功能錯誤: {str(e)}"))

def send_toilet_info(event, location):
    print(f"開始生成公廁卡片，位置: {location}")
    try:
        if toilet_df.empty:
            print("公廁資料為空")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="抱歉，無法載入公廁資料"))
            return

        lat, lon = map(float, location.split(","))
        print(f"解析位置: {lat}, {lon}")
        
        nearby = find_nearby_toilets(lat, lon)
        print(f"找到 {len(nearby)} 個附近公廁")

        if nearby.empty:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="附近沒有找到公廁資料"))
            return

        # 先測試一個簡單的卡片
        first_toilet = nearby.iloc[0]
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": str(first_toilet["公廁名稱"]), "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"地址：{first_toilet['公廁地址']}", "size": "sm", "wrap": True},
                    {"type": "text", "text": f"距離：約 {int(first_toilet['距離'])} 公尺", "size": "sm"}
                ]
            }
        }

        flex_message = FlexSendMessage(alt_text="附近公廁", contents=bubble)
        line_bot_api.reply_message(event.reply_token, flex_message)
        print("公廁卡片發送成功")
        
    except Exception as e:
        print(f"公廁卡片發送失敗: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"公廁功能錯誤: {str(e)}"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
