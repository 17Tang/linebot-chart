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

# 💡 新增：自動計算台股 Tick 格式化字串的工具函式
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
        return f"{int(round(price))}" # 500元以上不可能有小數點，直接轉整數字串

def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    plt.rcParams['axes.unicode_minus'] = False
    
    # 1. 抓取昨日收盤價 (歷史真實價格)
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

    # 2. 抓取當天 1 分鐘線
    try:
        stock_data = yf.download(ticker_id, period="1d", interval="1m", auto_adjust=False)
    except Exception as e:
        print(f"yfinance 抓取異常: {e}")
        return None
        
    if stock_data.empty:
        return None

    # 清理欄位
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

    # 💡 核心修正 1：精確對齊高低點數據
    # 過去只拿 Close 序列去算極值會有誤差，標準做法必須從 High 欄位找最高、Low 欄位找最低
    high_prices = today_data['High'].values.flatten().tolist()
    low_prices = today_data['Low'].values.flatten().tolist()
    close_prices = today_data['Close'].values.flatten().tolist()
    times = today_data.index
    
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 13:30 強制補點連線
    if len(minutes_from_start) > 0 and minutes_from_start[-1] < 270:
        minutes_from_start.append(270)
        close_prices.append(close_prices[-1])
        high_prices.append(high_prices[-1])
        low_prices.append(low_prices[-1])

    # 從全日範圍內抓取精準的最高與最低價及其所在的分鐘位置
    max_price = float(np.max(high_prices))
    min_price = float(np.min(low_prices))
    max_idx = np.argmax(high_prices)
    min_idx = np.argmin(low_prices)

    # ---- 🎨 3. 繪製深色系專業江波圖 ----
    # 使用 matplotlib 內建的黑夜極客風格底圖
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 設定畫布背景色為深灰色（比純黑更具備券商 App 質感）
    fig.patch.set_facecolor('#121212')
    ax.set_facecolor('#181818')
    
    # 逐段繪製走勢線
    for i in range(len(close_prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = close_prices[i], close_prices[i+1]
        avg_p = (y1 + y2) / 2
        
        # 決定顏色：大於昨收用亮紅，小於用亮綠
        color = '#ff4444' if avg_p >= yesterday_close else '#00e676'
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    # 畫灰色平盤虛線
    ax.axhline(y=yesterday_close, color='#555555', linestyle='--', alpha=0.7)
    
    # 動態計算數值標籤防疊線的 Offset 間距
    text_offset = yesterday_close * 0.005
    
    # 💡 核心修正 3：高低點純文字顯示（移除 scatter 圓點，修正顏色邏輯）
    max_color = '#ff4444' if max_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[max_idx], max_price + text_offset, get_taiwan_tick_format(max_price), 
            color=max_color, fontsize=11, weight='bold', ha='center', va='bottom')
            
    min_color = '#ff4444' if min_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[min_idx], min_price - text_offset, get_taiwan_tick_format(min_price), 
            color=min_color, fontsize=11, weight='bold', ha='center', va='top')

    # ---- 📐 4. 軸線範圍與 Tick 刻度校正 ----
    ax.set_xlim(0, 270)
    ax.set_xticks([0, 60, 120, 180, 240, 270])
    ax.set_xticklabels(['09:00', '10:00', '11:00', '12:00', '13:00', '13:30'], color='#aaaaaa')
    
    # 計算 Y 軸 ±5%、±10% 的原始價格
    raw_y_ticks = [
        yesterday_close * 0.90,
        yesterday_close * 0.95,
        yesterday_close,
        yesterday_close * 1.05,
        yesterday_close * 1.10
    ]
    ax.set_ylim(yesterday_close * 0.90, yesterday_close * 1.10)
    ax.set_yticks(raw_y_ticks)
    
    # 💡 核心修正 2：依據台股 Tick 規則動態格式化 Y 軸的所有刻度文字
    ax.set_yticklabels([get_taiwan_tick_format(val) for val in raw_y_ticks], color='#aaaaaa')
    
    # 美化圖表邊框與網格
    ax.set_title(f"{stock_id}", fontsize=20, weight='bold', color='#ffffff', pad=15)
    ax.set_xlabel("Time", fontsize=11, color='#aaaaaa')
    ax.set_ylabel("Price", fontsize=11, color='#aaaaaa')
    ax.grid(True, linestyle=':', color='#333333', alpha=0.6)
    
    # 移除 matplotlib 預設的亮白色外框線
    for spine in ax.spines.values():
        spine.set_edgecolor('#333333')
    
    # 保存圖片
    output_filename = f"{stock_id}_chart.png"
    image_path = os.path.join(STATIC_DIR, output_filename)
    plt.savefig(image_path, bbox_inches='tight', dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
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
