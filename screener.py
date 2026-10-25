import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone, timedelta

# 擴大雷達網：50 檔精選美股 (涵蓋指數、七大巨頭、熱門科技與半導體)
WATCHLIST = [
    'SPY', 'QQQ', 'DIA', 'IWM', 'SMH', 'XLK', 'XLF', 'XLE',
    'AAPL', 'NVDA', 'MSFT', 'META', 'AMZN', 'GOOGL', 'TSLA', 
    'AMD', 'TSM', 'AVGO', 'ASML', 'QCOM', 'TXN', 'INTC', 'MU',
    'PLTR', 'NFLX', 'CRM', 'ADBE', 'NOW', 'UBER', 'CRWD', 'PANW',
    'COIN', 'MSTR', 'HOOD', 'MARA', 'RIOT', 'SQ', 'PYPL', 'V',
    'JPM', 'GS', 'BAC', 'WMT', 'COST', 'PG', 'KO', 'PEP', 'DIS'
]

def calculate_indicators(df):
    if len(df) < 20: return df
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['Volume_20MA'] = df['Volume'].rolling(window=20).mean()
    df['RVOL'] = df['Volume'] / df['Volume_20MA']
    df['Dist_20MA'] = ((df['Close'] - df['20MA']) / df['20MA']) * 100
    return df

def find_swing_high(df, current_idx, lookback=20):
    """尋找最近的結構性前高 (作為獲利目標)"""
    start = max(0, current_idx - lookback)
    return df['High'].iloc[start:current_idx].max()

def find_swing_low(df, current_idx, lookback=10):
    """尋找最近的結構性前低 (作為防守止損)"""
    start = max(0, current_idx - lookback)
    return df['Low'].iloc[start:current_idx].min()

def detect_smc(df):
    if len(df) < 20:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0, 0, 0
        
    current_price = df.iloc[-1]['Close']
    is_uptrend = df.iloc[-1]['Dist_20MA'] > 0 # 趨勢過濾：只做 20MA 之上的多頭
    
    smc_id, smc_text, smc_class = None, "-", "border-gray-600 text-gray-400"
    entry_top, entry_bot, sl, tp, rrr = 0, 0, 0, 0, 0
    
    if not is_uptrend:
        return smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr

    # 1. 尋找未填補的 Bullish FVG
    for i in range(len(df)-1, 2, -1):
        if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
            fvg_top = df['Low'].iloc[i]
            fvg_bot = df['High'].iloc[i-2]
            fvg_mid = (fvg_top + fvg_bot) / 2
            
            # Mitigation Check: 檢查 FVG 形成後，有沒有被後面的 K 線回踩超過 50%
            is_mitigated = False
            for j in range(i+1, len(df)):
                if df['Low'].iloc[j] < fvg_mid:
                    is_mitigated = True
                    break
                    
            if not is_mitigated and current_price > fvg_bot:
                # 判斷是否帶有掃損 (Liquidity Sweep) 特徵
                swing_low_before_fvg = find_swing_low(df, i-1, 10)
                is_sweep = df['Low'].iloc[i-2] <= swing_low_before_fvg
                
                smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                smc_text = "🔥 S級 掃損+FVG" if is_sweep else "⚡ 1D FVG 缺口"
                smc_class = "border-yellow-500 text-yellow-400 font-bold" if is_sweep else "border-blue-500 text-blue-400"
                
                entry_top, entry_bot = fvg_top, fvg_bot
                sl = find_swing_low(df, i, 10) * 0.995 # 動態止損設在起漲點之下
                tp = find_swing_high(df, len(df)-1, 40) # 目標價設在前高
                
                # 計算盈虧比 RRR (Risk to Reward Ratio)
                risk = entry_bot - sl
                reward = tp - entry_bot
                rrr = round(reward / risk, 1) if risk > 0 else 0
                
                # 若盈虧比 < 1.5，視為劣質信號，直接放棄
                if rrr >= 1.5:
                    break
                else:
                    smc_id = None # 重置，繼續找
    
    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2), round(tp, 2), rrr

def main():
    print("啟動高勝率 SMC 量化雷達...")
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
            
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr = detect_smc(df)
            rvol = round(latest['RVOL'], 2) if not pd.isna(latest['RVOL']) else 1.0
            dist_20ma = round(latest['Dist_20MA'], 2) if not pd.isna(latest['Dist_20MA']) else 0.0
            
            label = "多頭" if dist_20ma > 0 else "空頭"
            label_class = "bg-green-900/50 text-green-400" if dist_20ma > 0 else "bg-red-900/50 text-red-400"
            if rvol >= 2.0: label, label_class = "爆量", "bg-yellow-900/50 text-yellow-400"
            
            screener_data.append({
                "symbol": symbol, "price": current_price, "rvol": rvol, "dist_20ma": dist_20ma,
                "smc_id": smc_id, "smc_text": smc_text, "smc_class": smc_class,
                "label": label, "label_class": label_class, 
                "entry_bot": entry_bot, "entry_top": entry_top, "sl": sl, "tp": tp, "rrr": rrr
            })
            print(f"✅ {symbol} 解析完成.")
        except Exception as e:
            print(f"❌ {symbol} 錯誤: {e}")

    # 排序：S級掃損 > 普通FVG > 爆量，且無形態的放最後
    def sort_key(x):
        score = 0
        if x['smc_id'] == 'fvg_sweep': score += 100
        elif x['smc_id'] == 'fvg': score += 50
        if x['rvol'] >= 1.5: score += x['rvol']
        return score
        
    screener_data.sort(key=sort_key, reverse=True)

    hk_timezone = timezone(timedelta(hours=8))
    updated_at = datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S")

    output = {"updated_at": updated_at, "watchlist_data": watchlist_data, "screener_data": screener_data}

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    print(f"\n🎉 資料已成功寫入 data.json！\n🕒 最新更新時間: {updated_at} (HKT)")

if __name__ == "__main__":
    main()
