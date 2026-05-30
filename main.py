import os
import datetime
import requests
import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles

# LINE Bot v3 SDK 套件
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, 
    ReplyMessageRequest, TextMessage, ImageMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = FastAPI()

STATIC_DIR = "static"
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://linebot-chart.onrender.com")

configuration = Configuration(access_token=LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

@app.get("/")
@app.head("/")
def read_root():
    return {"status": "LINE Bot is running!"}

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get('X-Line-Signature')
    body = await request.body()
    try:
        handler.handle(body.decode('utf-8'), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return 'OK'

def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    plt.rcParams['axes.unicode_minus'] = False
    
    try:
        # 💡 核心修正：加入 auto_adjust=False，強制抓取最真實的市面成交價，拒絕還原股價的誤差
        stock_data = yf.download(ticker_id, period="5d", interval="1m", auto_adjust=False)
    except Exception as e:
        print(f"yfinance 抓取發生異常: {e}")
        return None
        
    if stock_data.empty:
        try:
            ticker_id_two = f"{stock_id}.TWO"
            stock_data = yf.download(ticker_id_two, period="5d", interval="1m", auto_adjust=False)
        except:
            return None
        if stock_data.empty:
            return None

    if stock_data.index.tz is None:
        stock_data.index = stock_data.index.tz_localize('UTC').tz_convert('Asia/Taipei')
    else:
        stock_data.index = stock_data.index.tz_convert('Asia/Taipei')
        
    latest_date = stock_data.index.date.max()
    today_data = stock_data[stock_data.index.date == latest_date]
    today_data = today_data.between_time('09:00', '13:31')
    
    if today_data.empty:
        return None

    # 確保時間序列由早到晚正確排序
    today_data = today_data.sort_index()

    # 找出歷史數據中的「昨日交易日」
    previous_days_data = stock_data[stock_data.index.date < latest_date]
    if not previous_days_data.empty:
        previous_days_data = previous_days_data.sort_index()
        # 💡 核心修正：明確抓取 'Close' 欄位，不拿還原的 'Adj Close'
        yesterday_close = previous_days_data['Close'].iloc[-1]
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]
    else:
        yesterday_close = today_data['Open'].iloc[0]
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]

    yesterday_close = float(yesterday_close)

    # 💡 核心修正：明確指定拿真實成交價 'Close' 的數值序列
    prices = today_data['Close'].values.flatten().tolist()
    times = today_data.index
    
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 終極修正：如果最後一筆數據沒到 13:30 (270分鐘)，強行複製最後一筆價格補滿到 270 分鐘
    if len(minutes_from_start) > 0 and minutes_from_start[-1] < 270:
        minutes_from_start.append(270)
        prices.append(prices[-1])

    max_price = float(np.max(prices))
    min_price = float(np.min(prices))
    max_idx = np.argmax(prices)
    min_idx = np.argmin(prices)

    fig, ax = plt.subplots(figsize=(10, 5))
    
    for i in range(len(prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = prices[i], prices[i+1]
        avg_p = (y1 + y2) / 2
        color = '#ff3333' if avg_p >= yesterday_close else '#00cc44'
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    ax.axhline(y=yesterday_close, color='gray', linestyle='--', alpha=0.6)
    
    text_offset = yesterday_close * 0.004
    
    max_color = '#ff3333' if max_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[max_idx], max_price, color=max_color, s=30, zorder=5)
    ax.text(minutes_from_start[max_idx], max_price + text_offset, f"{max_price:.2f}", 
            color=max_color, fontsize=10, weight='bold', ha='center', va='bottom')
            
    min_color = '#ff3333' if min_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[min_idx], min_price, color=min_color, s=30, zorder=5)
    ax.text(minutes_from_start[min_idx], min_price - text_offset, f"{min_price:.2f}", 
            color=min_color, fontsize=10, weight='bold', ha='center', va='top')

    ax.set_xlim(0, 270)
    ax.set_xticks([0, 60, 120, 180, 240, 270])
    ax.set_xticklabels(['09:00', '10:00', '11:00', '12:00', '13:00', '13:30'])
    
    y_ticks = [
        yesterday_close * 0.90,
        yesterday_close * 0.95,
        yesterday_close,
        yesterday_close * 1.05,
        yesterday_close * 1.10
    ]
    ax.set_ylim(yesterday_close * 0.90, yesterday_close * 1.10)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([f"{val:.2f}" for val in y_ticks])
    
    ax.set_title(f"{stock_id}", fontsize=18, weight='bold')
    ax.set_xlabel("Time", fontsize=12)
    ax.set_ylabel("Price", fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.3)
    
    output_filename = f"{stock_id}_chart.png"
    image_path = os.path.join(STATIC_DIR, output_filename)
    plt.savefig(image_path, bbox_inches='tight', dpi=150)
    plt.close() 
    return output_filename

# 功能 B：核心篩選器（只辨識 p + 股號）
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 💡 關鍵修正：檢查是否為 p 開頭，且後面跟著的是數字（長度至少為5，例如 p2330）
    if user_msg.lower().startswith('p') and user_msg[1:].isdigit() and len(user_msg) >= 5:
        stock_code = user_msg[1:] # 剃除掉 p，只留下股號
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            filename = create_stock_chart(stock_code)
            
            if filename:
                timestamp = int(datetime.datetime.now().timestamp())
                img_url = f"{RENDER_EXTERNAL_URL}/static/{filename}?t={timestamp}"
                try:
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[ImageMessage(original_content_url=img_url, preview_image_url=img_url)]
                        )
                    )
                    return
                except Exception as line_error:
                    print(f"❌ LINE 訊息傳送失敗: {line_error}")
                    return
            
            # 抓取失敗回應
            try:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"抱歉，目前無法取得股號 {stock_code} 的走勢圖。")]
                    )
                )
            except Exception as e:
                print(f"發送失敗通知時發生錯誤: {e}")
    else:
        # 💡 關鍵修正：如果使用者輸入其他文字、貼圖、或單純的數字，直接 return 結束。
        # 這樣機器人會完全保持沉默，不會在後端噴錯，也不會干擾群組對話！
        return
