import os
import requests
import yfinance as yf
import matplotlib.pyplot as plt
from fastapi import FastAPI, Request, HTTPException

# LINE Bot v3 SDK 套件
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, 
    ReplyMessageRequest, TextMessage, ImageMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = FastAPI()

# 🔒 直接從系統環境（Render 後台設定）讀取憑證
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 初始化 LINE Bot 配置
# 注意：在本地測試時如果沒設定環境變數會報錯，部署到 Render 就正常了
configuration = Configuration(access_token=LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# 功能 A：抓數據並畫走勢圖
def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    # 抓取最近 5 天的 15 分鐘 K 線數據
    stock_data = yf.download(ticker_id, period="5d", interval="15m")
    
    if stock_data.empty:
        return None
        
    # 繪製精美走勢圖
    plt.figure(figsize=(10, 5))
    plt.plot(stock_data['Close'], label='Close Price', color='#1f77b4', linewidth=2)
    plt.title(f"Stock {stock_id} - Recent Trend", fontsize=16)
    plt.xlabel("Date/Time", fontsize=12)
    plt.ylabel("Price", fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend()
    
    # 儲存到本地伺服器暫存
    output_filename = f"{stock_id}_chart.png"
    plt.savefig(output_filename, bbox_inches='tight', dpi=150)
    plt.close() 
    return output_filename

# 功能 B：上傳到 Upload.cc 圖床 (免 Key、台灣可用)
def upload_to_uploadcc(image_path):
    url = "https://upload.cc/image_upload"
    try:
        with open(image_path, 'rb') as f:
            files = {'uploaded_file[]': f}
            response = requests.post(url, files=files)
            
        if response.status_code == 200:
            res_json = response.json()
            # 檢查是否上傳成功並取得檔名
            if res_json.get('success_image') and len(res_json['success_image']) > 0:
                img_name = res_json['success_image'][0]['logged_filename']
                return f"https://upload.cc/{img_name}" # 這裡就是給 LINE 的網址
        return None
    except Exception as e:
        print(f"Upload.cc 上傳失敗: {e}")
        return None

# 功能 C：LINE Webhook 接收通道
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get('X-Line-Signature')
    body = await request.body()
    try:
        handler.handle(body.decode('utf-8'), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return 'OK'

# 功能 D：處理 LINE 訊息的核心邏輯
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 判斷是否為股號（純數字且大於等於4碼）
    if user_msg.isdigit() and len(user_msg) >= 4:
        stock_code = user_msg
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            
            # 1. 畫圖
            image_path = create_stock_chart(stock_code)
            
            if image_path and os.path.exists(image_path):
                # 2. 上傳圖床
                img_url = upload_to_uploadcc(image_path)
                
                # 3. 刪除暫存圖，釋放 Render 空間
                if os.path.exists(image_path):
                    os.remove(image_path)
                
                # 4. 回傳圖片給 LINE 使用者
                if img_url:
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[ImageMessage(original_content_url=img_url, preview_image_url=img_url)]
                        )
                    )
                    return
            
            # 錯誤應答
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"抱歉，目前無法取得股號 {stock_code} 的走勢圖。")]
                )
            )
    else:
        # 非股號應答
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="請輸入欲查詢的台灣股號（例如：2330）")]
                )
            )

if __name__ == "__main__":
    import uvicorn
    # 本地測試用，Render 上線時會用 Start Command 啟動
    uvicorn.run(app, host="0.0.0.0", port=8000)