from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import os

app = FastAPI()

LINE_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
AI_API_ENDPOINT = os.getenv("AI_CHATBOT_ENDPOINT")  # 你的 AI API endpoint
HEADERS = {
    "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# fallback: 傳送純文字訊息
async def send_text_reply(reply_token, text):
    payload = {
        "replyToken": reply_token,
        "messages": [{
            "type": "text",
            "text": text
        }]
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=HEADERS,
            json=payload
        )

# 標準 Carousel 卡片格式
def build_carousel(columns):
    return {
        "type": "template",
        "altText": "這是輪播卡片訊息",
        "template": {
            "type": "carousel",
            "columns": columns
        }
    }

# 組 Carousel 欄位（多卡片）
def create_carousel_columns(data_list):
    columns = []
    for item in data_list[:10]:  # 最多 10 張卡片
        columns.append({
            "thumbnailImageUrl": item.get("image_url", "https://example.com/default.jpg"),
            "title": item.get("title", "沒有標題"),
            "text": item.get("description", "沒有描述"),
            "actions": [
                {
                    "type": "uri",
                    "label": "查看更多",
                    "uri": item.get("url", "https://example.com")
                }
            ]
        })
    return columns

@app.post("/webhook")
async def line_webhook(req: Request):
    body = await req.json()
    try:
        events = body["events"]
        for event in events:
            if event["type"] == "message" and event["message"]["type"] == "text":
                user_message = event["message"]["text"]
                reply_token = event["replyToken"]

                # 發送到 AI chatbot API
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        ai_response = await client.post(
                            AI_API_ENDPOINT,
                            json={"query": user_message}
                        )
                        ai_response.raise_for_status()
                        ai_data = ai_response.json()
                        cards = ai_data.get("cards")  # 預期的多卡片欄位

                        # 若 AI 回傳資料正確
                        if cards and isinstance(cards, list):
                            carousel_message = build_carousel(create_carousel_columns(cards))
                            reply_body = {
                                "replyToken": reply_token,
                                "messages": [carousel_message]
                            }
                        else:
                            # fallback：文字訊息
                            reply_body = {
                                "replyToken": reply_token,
                                "messages": [{
                                    "type": "text",
                                    "text": ai_data.get("text", "AI 無法理解您的訊息")
                                }]
                            }

                except Exception as e:
                    # fallback：AI API 發生錯誤
                    reply_body = {
                        "replyToken": reply_token,
                        "messages": [{
                            "type": "text",
                            "text": f"抱歉，系統發生錯誤，請稍後再試。\n({str(e)})"
                        }]
                    }

                # 傳送到 LINE
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "https://api.line.me/v2/bot/message/reply",
                        headers=HEADERS,
                        json=reply_body
                    )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    return JSONResponse(status_code=200, content={"message": "OK"})