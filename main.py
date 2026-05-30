import os
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

# 功能 A：抓數據並畫走勢圖（存到內建 static 資料夾，徹底解決第三方圖床擋 IP 問題）
def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    
    # 修正：用具體日期範圍，徹底避開 period="5d" 的 yfinance 解析 Bug
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=7)
    
    try:
        stock_data = yf.download(
            ticker_id, 
            start=start_date.strftime('%Y-%m-%d'), 
            end=today.strftime('%Y-%m-%d'), 
            interval="15m"
        )
    except Exception as e:
        print(f"yfinance 抓取發生異常: {e}")
        return None
    
    # 支援上櫃股票處理
    if stock_data.empty:
        try:
            ticker_id_two = f"{stock_id}.TWO"
            stock_data = yf.download(
                ticker_id_two, 
                start=start_date.strftime('%Y-%m-%d'), 
                end=today.strftime('%Y-%m-%d'), 
                interval="15m"
            )
        except:
            return None
        if stock_data.empty:
            return None
        
    plt.figure(figsize=(10, 5))
    plt.plot(stock_data['Close'], label='Close Price', color='#1f77b4', linewidth=2)
    plt.title(f"Stock {stock_id} - Recent Trend", fontsize=16)
    plt.xlabel("Date/Time", fontsize=12)
    plt.ylabel("Price", fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend()
    
    # 直接存進自建的 static 資料夾
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
