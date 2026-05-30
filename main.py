import os
import numpy as np
import pandas as pd
import datetime
import yfinance as yf
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

# 📁 1. 建立一個給外網存取圖片的 static 資料夾
STATIC_DIR = "static"
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

# 讓 FastAPI 掛載這個資料夾，外網輸入 /static/檔名 就能看到圖
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 🔒 2. 直接從系統環境（Render 後台設定）讀取憑證
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 你的 Render 服務網址
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://linebot-chart.onrender.com")

configuration = Configuration(access_token=LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# 應付 Render 的健康檢查 (支援 GET 與 HEAD)
@app.get("/")
@app.head("/")
def read_root():
    return {"status": "LINE Bot is running!"}

# 🛠️ 3. Webhook 接收通道（修正：把原本漏掉的這段補回來了！）
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
    
    # 修正 matplotlib 負號顯示問題
    plt.rcParams['axes.unicode_minus'] = False
    
    # 抓取最近 5 天的 1 分鐘 K 線數據
    try:
        stock_data = yf.download(ticker_id, period="5d", interval="1m")
    except Exception as e:
        print(f"yfinance 抓取發生異常: {e}")
        return None
        
    if stock_data.empty:
        try:
            ticker_id_two = f"{stock_id}.TWO"
            stock_data = yf.download(ticker_id_two, period="5d", interval="1m")
        except:
            return None
        if stock_data.empty:
            return None

    # ---- ⏱️ 時區處理與當天數據篩選 ----
    if stock_data.index.tz is None:
        stock_data.index = stock_data.index.tz_localize('UTC').tz_convert('Asia/Taipei')
    else:
        stock_data.index = stock_data.index.tz_convert('Asia/Taipei')
        
    latest_date = stock_data.index.date.max()
    today_data = stock_data[stock_data.index.date == latest_date]
    
    # 💡 關鍵修正：放寬到 13:31，確保 13:30 最後一盤收盤數據被包含進來
    today_data = today_data.between_time('09:00', '13:31')
    
    if today_data.empty:
        print(f"⚠️ 找不到 {latest_date} 當天 09:00-13:30 的交易數據")
        return None

    # ---- 📈 尋找昨日收盤價 (平盤價) ----
    previous_days_data = stock_data[stock_data.index.date < latest_date]
    if not previous_days_data.empty:
        yesterday_close = previous_days_data['Close'].iloc[-1]
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]
    else:
        yesterday_close = today_data['Open'].iloc[0]
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]

    yesterday_close = float(yesterday_close)

    # ---- 🔍 數據整理與最高/最低點計算 ----
    prices = today_data['Close'].values.flatten()
    times = today_data.index
    
    max_price = float(np.max(prices))
    min_price = float(np.min(prices))
    max_idx = np.argmax(prices)
    min_idx = np.argmin(prices)

    # ---- 🎨 開始繪製專業江波圖 ----
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 09:00 是基準點 (0分鐘)，13:30 是 270 分鐘
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 逐段畫線：大於昨收用紅線，小於昨收用綠線
    for i in range(len(prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = prices[i], prices[i+1]
        
        avg_p = (y1 + y2) / 2
        color = '#ff3333' if avg_p >= yesterday_close else '#00cc44'
        
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    # 畫一條灰色的水平虛線代表昨日收盤價（平盤線）
    ax.axhline(y=yesterday_close, color='gray', linestyle='--', alpha=0.6)
    
    # 計算文字上下 offset 的安全間距（以昨收的 0.4% 為基準）
    text_offset = yesterday_close * 0.004
    
    # 💡 修正高低點邏輯：
    # 1. 最高點標示 (不帶有符號，文字顏色依據價格本身與昨收的關係決定)
    max_color = '#ff3333' if max_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[max_idx], max_price, color=max_color, s=30, zorder=5)
    ax.text(minutes_from_start[max_idx], max_price + text_offset, f"{max_price:.2f}", 
            color=max_color, fontsize=10, weight='bold', ha='center', va='bottom')
            
    # 2. 最低點標示 (不帶有符號，若最低點仍大於昨收，一樣使用紅色)
    min_color = '#ff3333' if min_price >= yesterday_close else '#00cc44'
    ax.scatter(minutes_from_start[min_idx], min_price, color=min_color, s=30, zorder=5)
    ax.text(minutes_from_start[min_idx], min_price - text_offset, f"{min_price:.2f}", 
            color=min_color, fontsize=10, weight='bold', ha='center', va='top')

    # ---- 📐 軸線範圍與刻度調整 ----
    # 嚴格控制 X 軸左右不留白
    ax.set_xlim(0, 270)
    ax.set_xticks([0, 60, 120, 180, 240, 270])
    ax.set_xticklabels(['09:00', '10:00', '11:00', '12:00', '13:00', '13:30'])
    
    # 💡 修正 Y 軸刻度：以昨收為出發點，精準設定 ±5% 與 ±10% 的刻度數值
    y_ticks = [
        yesterday_close * 0.90,  # -10%
        yesterday_close * 0.95,  # -5%
        yesterday_close,         # 昨收平盤線
        yesterday_close * 1.05,  # +5%
        yesterday_close * 1.10   # +10%
    ]
    ax.set_ylim(yesterday_close * 0.90, yesterday_close * 1.10)
    ax.set_yticks(y_ticks)
    
    # 格式化 Y 軸文字：顯示兩位小數
    ax.set_yticklabels([f"{val:.2f}" for val in y_ticks])
    
    # 💡 修正標題：只單純顯示股號
    ax.set_title(f"{stock_id}", fontsize=18, weight='bold')
    ax.set_xlabel("Time", fontsize=12)
    ax.set_ylabel("Price", fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.3)
    
    # 儲存到自建的 static 資料夾
    output_filename = f"{stock_id}_chart.png"
    image_path = os.path.join(STATIC_DIR, output_filename)
    
    plt.savefig(image_path, bbox_inches='tight', dpi=150)
    plt.close() 
    return output_filename

# 功能 B：處理 LINE 訊息的核心邏輯
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    if user_msg.isdigit() and len(user_msg) >= 4:
        stock_code = user_msg
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            
            # 1. 畫圖（圖片會存在本地 static/ 內）
            filename = create_stock_chart(stock_code)
            print(f"DEBUG 🔍 畫圖結果 (filename): {filename}")
            
            if filename:
                # 2. 組合出你自己的 Render 靜態網址
                # 加上時間戳 (?t=...) 用來防止 LINE 快取舊圖
                timestamp = int(datetime.datetime.now().timestamp())
                img_url = f"{RENDER_EXTERNAL_URL}/static/{filename}?t={timestamp}"
                print(f"DEBUG 🔍 自建圖床網址 (img_url): {img_url}")
                
                # 3. 回傳圖片給 LINE 使用者
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
            
            # 失敗處理
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
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            try:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="請輸入欲查詢的台灣股號（例如：2330）")]
                    )
                )
            except Exception as e:
                print(f"發送罐頭訊息時發生錯誤: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
