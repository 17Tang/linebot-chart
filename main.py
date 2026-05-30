import os
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
    
    # 修正 matplotlib 在某些環境下負號顯示為方塊的問題
    plt.rcParams['axes.unicode_minus'] = False
    
    # 抓取最近 5 天的 1 分鐘 K 線數據，確保能完整拿到「昨天收盤價」與「今天即時數據」
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
    # 1. 將索引轉換為台灣時區 (Asia/Taipei)
    if stock_data.index.tz is None:
        stock_data.index = stock_data.index.tz_localize('UTC').tz_convert('Asia/Taipei')
    else:
        stock_data.index = stock_data.index.tz_convert('Asia/Taipei')
        
    # 2. 找出數據中最新的一天（即今天或最近一個交易日）
    latest_date = stock_data.index.date.max()
    
    # 3. 篩選出屬於最新那一天的數據
    today_data = stock_data[stock_data.index.date == latest_date]
    
    # 4. 嚴格篩選台股交易時間：09:00 到 13:30
    today_data = today_data.between_time('09:00', '13:30')
    
    if today_data.empty:
        print(f"⚠️ 找不到 {latest_date} 當天 09:00-13:30 的交易數據")
        return None

    # ---- 📈 尋找昨日收盤價 (平盤價) ----
    # 找出除了最新一天之外，前一個交易日的最後一筆收盤價
    previous_days_data = stock_data[stock_data.index.date < latest_date]
    if not previous_days_data.empty:
        # 拿到昨天的最後一筆收盤價
        yesterday_close = previous_days_data['Close'].iloc[-1]
        # 如果 yfinance 回傳的是 Series 則取數值
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]
    else:
        # 如果完全沒拿到歷史數據，勉強用當天開盤價當替代（通常 period=5d 一定拿得到昨收）
        yesterday_close = today_data['Open'].iloc[0]
        if isinstance(yesterday_close, pd.Series):
            yesterday_close = yesterday_close.iloc[0]

    # 確保數值為純數字
    yesterday_close = float(yesterday_close)

    # ---- 🎨 開始繪製單日走勢圖 ----
    plt.figure(figsize=(10, 5))
    
    # 畫出當天走勢線
    plt.plot(today_data.index.strftime('%H:%M'), today_data['Close'], label='Price', color='#1f77b4', linewidth=2)
    
    # 畫一條紅色的水平虛線代表昨日收盤價（平盤線）
    plt.axhline(y=yesterday_close, color='red', linestyle='--', alpha=0.6, label=f'Ref ({yesterday_close:.2f})')
    
    # 計算上下振幅 10% 的 Y 軸限制
    y_min = yesterday_close * 0.90
    y_max = yesterday_close * 1.10
    plt.ylim(y_min, y_max)
    
    # 限制 X 軸刻度數量，避免時間標籤太擠（例如每 30 分鐘顯示一次）
    x_ticks = plt.gca().get_xticks()
    if len(x_ticks) > 10:
        plt.gca().set_xticks(x_ticks[::15]) # 依數據密集度適度跳格顯示
        
    plt.title(f"Stock {stock_id} - Day Trend ({latest_date})", fontsize=16)
    plt.xlabel("Time (Taipei)", fontsize=12)
    plt.ylabel("Price", fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.3)
    plt.legend(loc='upper left')
    
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
