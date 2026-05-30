import numpy as np
import pandas as pd
import math

# 💡 工具函式 1：根據台股股價區間，回傳正確的 Tick 步長 (用於精準對齊漲跌幅)
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

# 💡 工具函式 2：台股法規計算（漲停/跌停與中間檔位必須符合 Tick 步長，並進行精確格式化）
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

# 💡 新增工具函式 3：計算符合台股 Tick 規範的價格（主要用於漲跌停與 5% 檔位無條件捨去/進位判定）
def align_to_tick(price, tick_size, is_up=True):
    # 台股漲停/5% 上檔通常是向下取捨(不超過法規上限)；跌停/5% 下檔則是向上取捨(不超過法規下限)
    # 這裡為求 Y 軸刻度絕對對稱券商現狀，使用標準四捨五入到最近的 Tick 檔位
    return round(price / tick_size) * tick_size

def create_stock_chart(stock_id):
    ticker_id = f"{stock_id}.TW"
    plt.rcParams['axes.unicode_minus'] = False
    
    # 1. 抓取昨日收盤價 (平盤價)
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

    high_prices = today_data['High'].values.flatten().tolist()
    low_prices = today_data['Low'].values.flatten().tolist()
    close_prices = today_data['Close'].values.flatten().tolist()
    times = today_data.index
    
    minutes_from_start = [(t.hour - 9) * 60 + t.minute for t in times]
    
    # 13:30 強制補點連線到右側邊界
    if len(minutes_from_start) > 0 and minutes_from_start[-1] < 270:
        minutes_from_start.append(270)
        close_prices.append(close_prices[-1])
        high_prices.append(high_prices[-1])
        low_prices.append(low_prices[-1])

    max_price = float(np.max(high_prices))
    min_price = float(np.min(low_prices))
    max_idx = np.argmax(high_prices)
    min_idx = np.argmin(low_prices)

    # ---- 🎨 3. 繪製深色系專業江波圖 ----
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    fig.patch.set_facecolor('#121212')
    ax.set_facecolor('#181818')
    
    # 逐段繪製走勢線
    for i in range(len(close_prices) - 1):
        x1, x2 = minutes_from_start[i], minutes_from_start[i+1]
        y1, y2 = close_prices[i], close_prices[i+1]
        avg_p = (y1 + y2) / 2
        color = '#ff4444' if avg_p >= yesterday_close else '#00e676'
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
    
    ax.axhline(y=yesterday_close, color='#555555', linestyle='--', alpha=0.7)
    
    text_offset = yesterday_close * 0.005
    
    max_color = '#ff4444' if max_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[max_idx], max_price + text_offset, get_taiwan_tick_format(max_price), 
            color=max_color, fontsize=11, weight='bold', ha='center', va='bottom')
            
    min_color = '#ff4444' if min_price >= yesterday_close else '#00e676'
    ax.text(minutes_from_start[min_idx], min_price - text_offset, get_taiwan_tick_format(min_price), 
            color=min_color, fontsize=11, weight='bold', ha='center', va='top')

    # ---- 📐 4. X 軸與 Y 軸精確校正 ----
    # 💡 核心修正 1：X 軸嚴格鎖定 0~270 分鐘，並改為「每 30 分鐘為一格」的標準間隔
    ax.set_xlim(0, 270)
    x_ticks = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270]
    x_labels = ['09:00', '09:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30', '13:00', '13:30']
    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_labels, color='#aaaaaa', fontsize=9)
    
    # 💡 核心修正 2：根據台股 Tick 步長計算絕對符合法規的對稱 Y 軸刻度
    tick_size = get_taiwan_tick_size(yesterday_close)
    
    # 精算上下限（幅幅 10% 且必須符合 Tick 檔位對齊）
    limit_10_up = align_to_tick(yesterday_close * 1.10, tick_size)
    limit_5_up = align_to_tick(yesterday_close * 1.05, tick_size)
    limit_5_down = align_to_tick(yesterday_close * 0.95, tick_size)
    limit_10_down = align_to_tick(yesterday_close * 0.90, tick_size)
    
    y_ticks = [limit_10_down, limit_5_down, yesterday_close, limit_5_up, limit_10_up]
    
    ax.set_ylim(limit_10_down, limit_10_up)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([get_taiwan_tick_format(val) for val in y_ticks], color='#aaaaaa')
    
    # 網格美化
    ax.set_title(f"{stock_id}", fontsize=20, weight='bold', color='#ffffff', pad=15)
    ax.set_xlabel("Time", fontsize=11, color='#aaaaaa')
    ax.set_ylabel("Price", fontsize=11, color='#aaaaaa')
    ax.grid(True, linestyle=':', color='#333333', alpha=0.6)
    
    for spine in ax.spines.values():
        spine.set_edgecolor('#333333')
    
    # 保存圖片
    output_filename = f"{stock_id}_chart.png"
    image_path = os.path.join(STATIC_DIR, output_filename)
    plt.savefig(image_path, bbox_inches='tight', dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close() 
    return output_filename
