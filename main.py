import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
)
from linebot.v3.messaging.models import (
    StickerMessage,
    ShowLoadingAnimationRequest,
    PushMessageRequest,
)
from dotenv import load_dotenv
from utils.utils import normalize_llm_text, event_hour_yyyymmddhh

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


def call_llm(user_id: str, query: str) -> str:
    """
    呼叫你的 LLM 服務。
    - timeout 拆成 (connect, read)：避免卡在連線建立。
    - 回傳純文字，失敗則回覆友善訊息。
    """
    try:
        r = _requests_session.get(
            f"{llm_api_base}/chat",
            params={"user_id": user_id, "query": query}
        )
        r.raise_for_status()
        return r.text.strip()
    except requests.exceptions.RequestException as e:
        app.logger.error(f"LLM 呼叫失敗：{e}")
        return "有一些問題發生 ... 請稍後再試"


def _push_text(user_id: str, text: str):
    """
    封裝 Push API（不吃 reply token 時限）
    """
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)],
            )
        )


def process_and_push_text(user_id: str, user_id_with_session: str, query: str):
    """
    背景任務：呼叫 LLM → 正規化 → Push 回使用者
    """
    answer = call_llm(user_id=user_id_with_session, query=query)
    answer = normalize_llm_text(answer)
    _push_text(user_id, answer)


# -----------------------------------------------------------------------------
# Flask / LINE Webhook
# -----------------------------------------------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    # get X-Line-Signature header value
    signature = request.headers.get("X-Line-Signature")

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info(
            "Invalid signature. Please check your channel access token/channel secret."
        )
        abort(400)

    # 立刻回 200，避免 LINE 判定 webhook 超時（~2 秒）
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    hour_suffix = event_hour_yyyymmddhh(event.timestamp)
    user_id_with_session = f"{user_id}:{hour_suffix}"

    # 盡量別在這裡做耗時的事情（<= 2 秒就 return）
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # 顯示 loading（只是前端體感，與 webhook 逾時無關）
        try:
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=user_id, loadingSeconds=60)
            )
        except Exception:
            app.logger.warning("show loading animation failed, continue ...")

    # 把重運算丟到背景（ThreadPoolExecutor）
    executor.submit(
        process_and_push_text,
        user_id,
        user_id_with_session,
        event.message.text,
    )


@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    user_id = event.source.user_id
    hour_suffix = event_hour_yyyymmddhh(event.timestamp)
    user_id_with_session = f"{user_id}:{hour_suffix}"

    lat = event.message.latitude
    lon = event.message.longitude
    city = event.message.title
    address = event.message.address
    query = f"緯度：{lat}, 經度：{lon} {city} {address} 附近有什麼停車場"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        # 立刻回覆短訊息，避免 reply token 超時
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="收到定位，我來幫你找停車場～")],
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


    # 背景跑 LLM，再用 push 回完整結果
    executor.submit(
        process_and_push_text,
        user_id,
        user_id_with_session,
        query,
    )


if __name__ == "__main__":
    # 建議 production 用 WSGI/ASGI server（gunicorn/uvicorn）與反向代理
    app.run(debug=True)