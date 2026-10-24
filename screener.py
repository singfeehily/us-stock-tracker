import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone, timedelta

# 加入大盤及板塊指數，讓前端的「大盤與板塊即時資金流向」可以直接抓取
WATCHLIST = ['AAPL', 'NVDA', 'TSLA', 'PLTR', 'SPY', 'QQQ', 'DIA', 'IWM', 'SMH', 'XLK', 'XLF', 'XLE', 'MSFT', 'META', 'AMZN', 'GOOGL', 'AMD']

def calculate_indicators(df):
    if len(df) < 20:
        return df
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['Volume_20MA'] = df['Volume'].rolling(window=20).mean()
    df['RVOL'] = df['Volume'] / df['Volume_20MA']
    df['Dist_20MA'] = ((df['Close'] - df['20MA']) / df['20MA']) * 100
    return df

def detect_smc(df):
    if len(df) < 5:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0
    current_price = df.iloc[-1]['Close']
    smc_id, smc_text, smc_class = None, "-", "border-gray-600 text-gray-400"
    entry_top, entry_bot = df.iloc[-1]['Low'], df.iloc[-1]['Low'] * 0.98
    sl = entry_bot * 0.99
    
    # FVG Detection
    for i in range(len(df)-1, 2, -1):
        if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
            fvg_top, fvg_bot = df['Low'].iloc[i], df['High'].iloc[i-2]
            if current_price > fvg_bot:
                smc_id, smc_text, smc_class = 'fvg', "1D FVG 缺口", "border-yellow-500 text-yellow-400"
                entry_top, entry_bot, sl = fvg_top, fvg_bot, fvg_bot * 0.99
                break
    
    # OB Detection
    if not smc_id:
        for i in range(len(df)-2, 1, -1):
            is_down = df['Close'].iloc[i] < df['Open'].iloc[i]
            is_up = df['Close'].iloc[i+1] > df['Open'].iloc[i+1] and (df['Close'].iloc[i+1] - df['Open'].iloc[i+1]) > (df['Open'].iloc[i] - df['Close'].iloc[i]) * 1.5
            if is_down and is_up:
                ob_top, ob_bot = df['Open'].iloc[i], df['Low'].iloc[i]
                if current_price > ob_bot:
                    smc_id, smc_text, smc_class = 'ob', "1D Order Block", "border-purple-500 text-purple-400"
                    entry_top, entry_bot, sl = ob_top, ob_bot, ob_bot * 0.99
                    break

    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2)

def main():
    print("開始獲取美股資料並進行 AI 運算...")
    screener_data, watchlist_data = [], []
    
    for symbol in WATCHLIST:
        try:
            df = yf.Ticker(symbol).history(period="6mo")
            if df.empty: continue
            df = calculate_indicators(df)
            
            latest, prev = df.iloc[-1], df.iloc[-2]
            current_price, prev_price = round(latest['Close'], 2), round(prev['Close'], 2)
            change = round(current_price - prev_price, 2)
            change_pct = round((change / prev_price) * 100, 2)
            
            watchlist_data.append({"symbol": symbol, "price": current_price, "change": change, "change_pct": change_pct})
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl = detect_smc(df)
            
            rvol = round(latest['RVOL'], 2) if not pd.isna(latest['RVOL']) else 1.0
            dist_20ma = round(latest['Dist_20MA'], 2) if not pd.isna(latest['Dist_20MA']) else 0.0
            
            label = "多頭" if dist_20ma > 0 else "空頭"
            label_class = "bg-green-900/50 text-green-400" if dist_20ma > 0 else "bg-red-900/50 text-red-400"
            if rvol >= 2.0:
                label, label_class = "爆量", "bg-yellow-900/50 text-yellow-400"
            
            screener_data.append({
                "symbol": symbol, "price": current_price, "rvol": rvol, "dist_20ma": dist_20ma,
                "smc_id": smc_id, "smc_text": smc_text, "smc_class": smc_class,
                "label": label, "label_class": label_class, "entry_bot": entry_bot, "entry_top": entry_top, "sl": sl
            })
            print(f"✅ {symbol} 處理完成.")
        except Exception as e:
            print(f"❌ 處理 {symbol} 時發生錯誤: {e}")

    def sort_key(x):
        return (10 if x['smc_id'] in ['fvg', 'ob'] else 0) + (x['rvol'] if x['rvol'] >= 1.5 else 0)
    screener_data.sort(key=sort_key, reverse=True)

    hk_timezone = timezone(timedelta(hours=8))
    updated_at = datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S")

    output = {"updated_at": updated_at, "watchlist_data": watchlist_data, "screener_data": screener_data}

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    print(f"\n🎉 資料已成功寫入 data.json！\n🕒 最新更新時間: {updated_at} (HKT)")

if __name__ == "__main__":
    main()
