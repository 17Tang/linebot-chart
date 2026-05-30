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
    
    # 💡 修正 1：直接透過 Ticker 抓取最權威的歷史資料，確保昨收（previousClose）100% 乾淨無誤差
    try:
        ticker = yf.Ticker(ticker_id)
        # fast_info 裡面的 previous_close 是台灣券商通用的昨日真實收盤價
        yesterday_close = ticker.fast_info.get('previous_close')
        if yesterday_close is None or np.isnan(yesterday_close):
            # 備用方案：如果 fast_info 抓不到，抓歷史日線的最後一筆
            hist_day = ticker.history(period="2d", auto_adjust=False)
            yesterday_close = hist_day['Close'].iloc[-2]
    except Exception as e:
        print(f"抓取昨日收盤價失敗，嘗試大盤格式: {e}")
        # 如果是上櫃股票，切換成 .TWO
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

    # 💡 修正 2：抓取當天的 1 分鐘即時 K 線
    try:
        stock_data = yf.download(ticker_id, period="1d", interval="1m", auto_adjust=False)
    except Exception as e:
        print(f"yfinance 當天數據抓取發生異常: {e}")
        return None
        
    if stock_data.empty:
        return None

    # 💡 修正 3：徹底清洗 yfinance 欄位名稱（防範多重索引或大小寫 Bug）
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

    # 提取當天價格序列
    prices = today_data['Close'].values.flatten().tolist()
    times = today_data.index
    
    # 計算相對於 09:00 的分鐘數
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 補點連線：如果數據還沒走到 13:30 (270分鐘)，強制把最後一筆價格拉到收盤邊界
    if len(minutes_from_start) > 0 and minutes_from_start[-1] < 270:
        minutes_from_start.append(270)
        prices.append(prices[-1])

    # 計算當日最高與最低點
    max_price = float(np.max(prices))
    min_price = float(np.min(prices))
    max_idx = np.argmax(prices)
    min_idx = np.argmin(prices)

    # ---- 🎨 開始繪圖 ----
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 分段上色（紅/綠）
    for i in range(len(prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = prices[i], prices[i+1]
        avg_p = (y1 + y2) / 2
        color = '#ff3333' if avg_p >= yesterday_close else '#00cc44'
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    # 畫灰色平盤虛線
    ax.axhline(y=yesterday_close, color='gray', linestyle='--', alpha=0.6)
    
    # 動態計算數值標籤的上下 Offset 間距
    text_offset = yesterday_close * 0.004
    
    # 最高點標示 (文字與點的顏色由與昨收的關係決定)
    max_color = '#ff3333' if max_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[max_idx], max_price, color=max_color, s=30, zorder=5)
    ax.text(minutes_from_start[max_idx], max_price + text_offset, f"{max_price:.2f}", 
            color=max_color, fontsize=10, weight='bold', ha='center', va='bottom')
            
    # 最低點標示 (若最低點仍大於昨收，一樣維持紅色)
    min_color = '#ff3333' if min_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[min_idx], min_price, color=min_color, s=30, zorder=5)
    ax.text(minutes_from_start[min_idx], min_price - text_offset, f"{min_price:.2f}", 
            color=min_color, fontsize=10, weight='bold', ha='center', va='top')

    # 軸線設定（左右完全不留白）
    ax.set_xlim(0, 270)
    ax.set_xticks([0, 60, 120, 180, 240, 270])
    ax.set_xticklabels(['09:00', '10:00', '11:00', '12:00', '13:00', '13:30'])
    
    # 嚴格對稱的 Y 軸百分比刻度
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
    
    # 純股號標題
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
