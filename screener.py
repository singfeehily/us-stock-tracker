import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone, timedelta

# 核心監察與篩選清單 (可隨時自行新增)
# 把 screener.py 第8行改成這樣：
WATCHLIST = ['AAPL', 'NVDA', 'TSLA', 'PLTR', 'SPY', 'QQQ', 'DIA', 'IWM', 'SMH', 'XLK', 'MSFT', 'META', 'AMZN', 'GOOGL', 'AMD']

def calculate_indicators(df):
    """計算均線與成交量指標"""
    if len(df) < 20:
        return df
    
    # 20日均線與20日均量
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['Volume_20MA'] = df['Volume'].rolling(window=20).mean()
    
    # RVOL (相對成交量)
    df['RVOL'] = df['Volume'] / df['Volume_20MA']
    
    # 乖離率 (距離20MA的百分比)
    df['Dist_20MA'] = ((df['Close'] - df['20MA']) / df['20MA']) * 100
    
    return df

def detect_smc(df):
    """SMC (Smart Money Concepts) 形態偵測：尋找 FVG 與 Order Block"""
    if len(df) < 5:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0
        
    current_price = df.iloc[-1]['Close']
    
    smc_id = None
    smc_text = "-"
    smc_class = "border-gray-600 text-gray-400"
    
    # 預設買入與止損位 (如果沒有找到形態，預設抓最近支撐)
    entry_top = df.iloc[-1]['Low']
    entry_bot = entry_top * 0.98
    sl = entry_bot * 0.99
    
    # 1. 尋找 Bullish FVG (看漲合理價值缺口): 兩天前的最高價 < 今天的最低價
    # 反向迴圈從最近的日期找回去
    for i in range(len(df)-1, 2, -1):
        if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
            fvg_top = df['Low'].iloc[i]
            fvg_bot = df['High'].iloc[i-2]
            
            # 如果目前價格還沒跌破這個缺口底部，視為有效支撐
            if current_price > fvg_bot:
                smc_id = 'fvg'
                smc_text = "1D FVG 缺口"
                smc_class = "border-yellow-500 text-yellow-400"
                entry_top = fvg_top
                entry_bot = fvg_bot
                sl = fvg_bot * 0.99  # 跌破缺口即止損
                break
    
    # 2. 如果沒有 FVG，尋找 Bullish Order Block (看漲訂單塊)
    if not smc_id:
        for i in range(len(df)-2, 1, -1):
            is_down_candle = df['Close'].iloc[i] < df['Open'].iloc[i]
            is_strong_up = df['Close'].iloc[i+1] > df['Open'].iloc[i+1] and \
                           (df['Close'].iloc[i+1] - df['Open'].iloc[i+1]) > (df['Open'].iloc[i] - df['Close'].iloc[i]) * 1.5
            
            if is_down_candle and is_strong_up:
                ob_top = df['Open'].iloc[i]
                ob_bot = df['Low'].iloc[i]
                
                if current_price > ob_bot:
                    smc_id = 'ob'
                    smc_text = "1D Order Block"
                    smc_class = "border-purple-500 text-purple-400"
                    entry_top = ob_top
                    entry_bot = ob_bot
                    sl = ob_bot * 0.99
                    break

    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2)

def main():
    print("開始獲取美股資料並進行 AI 運算...")
    
    screener_data = []
    watchlist_data = []
    
    for symbol in WATCHLIST:
        try:
            # 抓取最近半年歷史數據
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="6mo")
            
            if df.empty:
                continue
                
            df = calculate_indicators(df)
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 基本報價計算
            current_price = round(latest['Close'], 2)
            prev_price = round(prev['Close'], 2)
            change = round(current_price - prev_price, 2)
            change_pct = round((change / prev_price) * 100, 2)
            
            # 寫入 Watchlist 卡片所需的資料
            watchlist_data.append({
                "symbol": symbol,
                "price": current_price,
                "change": change,
                "change_pct": change_pct
            })
            
            # SMC 作戰區間計算
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl = detect_smc(df)
            
            # RVOL 與 MA 狀態標籤
            rvol = round(latest['RVOL'], 2) if not pd.isna(latest['RVOL']) else 1.0
            dist_20ma = round(latest['Dist_20MA'], 2) if not pd.isna(latest['Dist_20MA']) else 0.0
            
            label = "多頭" if dist_20ma > 0 else "空頭"
            label_class = "bg-green-900/50 text-green-400" if dist_20ma > 0 else "bg-red-900/50 text-red-400"
            
            if rvol >= 2.0:
                label = "爆量"
                label_class = "bg-yellow-900/50 text-yellow-400"
            
            # 寫入量化篩選表資料
            screener_data.append({
                "symbol": symbol,
                "price": current_price,
                "rvol": rvol,
                "dist_20ma": dist_20ma,
                "smc_id": smc_id,
                "smc_text": smc_text,
                "smc_class": smc_class,
                "label": label,
                "label_class": label_class,
                "entry_bot": entry_bot,
                "entry_top": entry_top,
                "sl": sl
            })
            print(f"✅ {symbol} 處理完成.")
            
        except Exception as e:
            print(f"❌ 處理 {symbol} 時發生錯誤: {e}")

    # 排序邏輯：將有出現 FVG/OB 形態，或者爆量 (RVOL > 1.5) 的股票排在前面
    def sort_key(x):
        score = 0
        if x['smc_id'] in ['fvg', 'ob']: score += 10
        if x['rvol'] >= 1.5: score += x['rvol']
        return score
        
    screener_data.sort(key=sort_key, reverse=True)

    # 🕒 核心修正：強制轉換為香港時間 (UTC+8)
    hk_timezone = timezone(timedelta(hours=8))
    updated_at = datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S")

    # 統整輸出
    output = {
        "updated_at": updated_at,
        "watchlist_data": watchlist_data,
        "screener_data": screener_data
    }

    # 寫出 data.json
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    print(f"\n🎉 資料已成功寫入 data.json！")
    print(f"🕒 最新更新時間: {updated_at} (HKT)")

if __name__ == "__main__":
    main()
