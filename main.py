import os
import datetime
import requests
import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import math
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

# ==========================================
# 🛠️ 輔助函式：台股特化規則計算
# ==========================================

# 根據台股股價區間，回傳正確的 Tick 步長
def get_taiwan_tick_size(price):
    if price < 10:
        return 0.01
    elif price < 50:
        return 0.05
    elif price < 100:
        return 0.1
    elif price < 500:
        return 0.5
    elif price < 1000:
        return 1.0
    else:
        return 5.0

# 依據台股 Tick 規則進行文字顯示格式化
def get_taiwan_tick_format(price):
    if price < 10:
        return f"{price:.2f}"
    elif price < 50:
        return f"{price:.2f}"
    elif price < 100:
        return f"{price:.1f}"
    elif price < 500:
        return f"{price:.1f}"
    else:
        return f"{int(round(price))}"

# ==========================================
# 📈 主功能 A：專業深色系江波圖繪製
# ==========================================
def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    plt.rcParams['axes.unicode_minus'] = False
    
    # 1. 抓取昨日收盤價 (歷史真實平盤價)
    try:
        ticker = yf.Ticker(ticker_id)
        yesterday_close = ticker.fast_info.get('previous_close')
        if yesterday_close is None or np.isnan(yesterday_close):
            hist_day = ticker.history(period="2d", auto_adjust=False)
            yesterday_close = hist_day['Close'].iloc[-2]
    except Exception as e:
        print(f"嘗試抓取上市股昨收失敗，切換上櫃格式: {e}")
        try:
            ticker_id = f"{stock_id}.TWO"
            ticker = yf.Ticker(ticker_id)
            yesterday_close = ticker.fast_info.get('previous_close')
            if yesterday_close is None or np.isnan(yesterday_close):
                hist_day = ticker.history(period="2d", auto_adjust=False)
                yesterday_close = hist_day['Close'].iloc[-2]
        except:
            return None

    yesterday_close = float(yesterday_close)

    # 2. 抓取當天 1 分鐘線即時數據
    try:
        stock_data = yf.download(ticker_id, period="1d", interval="1m", auto_adjust=False)
    except Exception as e:
        print(f"yfinance 抓取異常: {e}")
        return None
        
    if stock_data.empty:
        return None

    # 清理 yfinance 欄位結構
    if isinstance(stock_data.columns, pd.MultiIndex):
        stock_data.columns = stock_data.columns.get_level_values(0)
    stock_data.columns = [str(col).capitalize() for col in stock_data.columns]

    # 時區轉換與排序
    if stock_data.index.tz is None:
        stock_data.index = stock_data.index.tz_localize('UTC').tz_convert('Asia/Taipei')
    else:
        stock_data.index = stock_data.index.tz_convert('Asia/Taipei')
        
    today_data = stock_data.between_time('09:00', '13:31').sort_index()
    if today_data.empty:
        return None

    # 讀取高低點與收盤價序列
    high_prices = today_data['High'].values.flatten().tolist()
    low_prices = today_data['Low'].values.flatten().tolist()
    close_prices = today_data['Close'].values.flatten().tolist()
    times = today_data.index
    
    # 換算時間為開盤後的第幾分鐘 (09:00 = 0, 13:30 = 270)
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 💡 修正 2：在盤中即時叫圖時，不需要強制補點到 270。
    # 讓時間線與數據自然停留在當前分鐘，但 X 軸範圍（0~270）固定住，這樣盤中圖表就不會變形！
    # 只有在已經收盤（最後一筆大於等於 13:30）且數據未連滿時，才做右側邊界強制連線補點。
    if len(minutes_from_start) > 0 and minutes_from_start[-1] >= 270:
        if minutes_from_start[-1] < 270:
            minutes_from_start.append(270)
            close_prices.append(close_prices[-1])
            high_prices.append(high_prices[-1])
            low_prices.append(low_prices[-1])

    # 從目前的數據範圍內抓取最高與最低價及其位置
    max_price = float(np.max(high_prices))
    min_price = float(np.min(low_prices))
    max_idx = np.argmax(high_prices)
    min_idx = np.argmin(low_prices)

    # ---- 🎨 3. 開始繪製深色系專業圖表 ----
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    fig.patch.set_facecolor('#121212') # 外部畫布背景
    ax.set_facecolor('#181818')     # 圖表內部背景
    
    # 逐段繪製即時走勢線並判斷紅綠顏色
    for i in range(len(close_prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = close_prices[i], close_prices[i+1]
        avg_p = (y1 + y2) / 2
        color = '#ff4444' if avg_p >= yesterday_close else '#00e676'
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    # 畫灰色平盤虛線
    ax.axhline(y=yesterday_close, color='#555555', linestyle='--', alpha=0.7)
    
    # 動態數值標籤防疊線間距
    text_offset = yesterday_close * 0.005
    
    # 最高點與最低點純文字顯示 (移除 Scatter 圓點)
    max_color = '#ff4444' if max_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[max_idx], max_price + text_offset, get_taiwan_tick_format(max_price), 
            color=max_color, fontsize=11, weight='bold', ha='center', va='bottom')
            
    min_color = '#ff4444' if min_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[min_idx], min_price - text_offset, get_taiwan_tick_format(min_price), 
            color=min_color, fontsize=11, weight='bold', ha='center', va='top')

    # ---- 📐 4. X 軸與 Y 軸精確校正 ----
    # 💡 修正 2：X 軸範圍強制死鎖在 0~270 分鐘，不論盤中任何時間叫圖，格式完全固定不跑掉
    ax.set_xlim(0, 270)
    x_ticks = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270]
    x_labels = ['09:00', '09:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30', '13:00', '13:30']
    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_labels, color='#aaaaaa', fontsize=9)
    
    # 💡 修正 1：依照台股法規計算絕對合法的漲跌停限制 (不超過 +10% 且不低於 -10%)
    tick_size = get_taiwan_tick_size(yesterday_close)
    
    # 漲停上限：無條件捨去 (FLOOR) 到最接近的合法 Tick，確保絕對「不超過」10%
    limit_10_up = math.floor((yesterday_close * 1.10) / tick_size) * tick_size
    # 跌停下限：無條件進位 (CEIL) 到最接近的合法 Tick，確保絕對「不低於」-10%
    limit_10_down = math.ceil((yesterday_close * 0.90) / tick_size) * tick_size
    
    # 為了維持 Y 軸對稱性，5% 刻度取上下限與平盤的中間值
    limit_5_up = round(((limit_10_up + yesterday_close) / 2) / tick_size) * tick_size
    limit_5_down = round(((limit_10_down + yesterday_close) / 2) / tick_size) * tick_size
    
    y_ticks = [limit_10_down, limit_5_down, yesterday_close, limit_5_up, limit_10_up]
    
    ax.set_ylim(limit_10_down, limit_10_up)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([get_taiwan_tick_format(val) for val in y_ticks], color='#aaaaaa')
    
    # 💡 修正 3：拿掉 Price 與 Time 的軸標籤註記，只保留最乾淨的標題
    ax.set_title(f"{stock_id}", fontsize=20, weight='bold', color='#ffffff', pad=15)
    
    # 網格美化設定
    ax.grid(True, linestyle=':', color='#333333', alpha=0.6)
    for spine in ax.spines.values():
        spine.set_edgecolor('#333333')
    
    # 儲存圖片
    output_filename = f"{stock_id}_chart.png"
    image_path = os.path.join(STATIC_DIR, output_filename)
    plt.savefig(image_path, bbox_inches='tight', dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close() 
    return output_filename

# ==========================================
# 📱 主功能 B：LINE 訊息接收與關鍵字篩選
# ==========================================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    if user_msg.lower().startswith('p') and user_msg[1:].isdigit() and len(user_msg) >= 5:
        stock_code = user_msg[1:]
        
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
                    print(f"❌ LINE 傳送失敗: {line_error}")
                    return
            
            try:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"目前無法取得股號 {stock_code} 的即時走勢圖。")]
                    )
                )
            except Exception as e:
                print(f"發送失敗通知時錯誤: {e}")
    else:
        return
