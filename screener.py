import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone, timedelta

def load_tickers(filename='tickers.txt'):
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
    else:
        print(f"⚠️ 找不到 {filename}，將使用預設名單。")
        return ['SPY', 'QQQ', 'NVDA', 'TSLA', 'AAPL', 'PLTR']

WATCHLIST = load_tickers()

def calculate_indicators(df):
    if len(df) < 20: return df
    df['20MA'] = df['Close'].rolling(window=20).mean()
    df['Volume_20MA'] = df['Volume'].rolling(window=20).mean()
    df['RVOL'] = df['Volume'] / df['Volume_20MA']
    df['Dist_20MA'] = ((df['Close'] - df['20MA']) / df['20MA']) * 100
    return df

def find_swing_high(df, current_idx, lookback=20):
    start = max(0, current_idx - lookback)
    return df['High'].iloc[start:current_idx].max()

def find_swing_low(df, current_idx, lookback=10):
    start = max(0, current_idx - lookback)
    return df['Low'].iloc[start:current_idx].min()

def get_earnings_alert(ticker_obj):
    try:
        cal = ticker_obj.calendar
        if isinstance(cal, dict) and 'Earnings Date' in cal:
            ed = cal['Earnings Date']
            if isinstance(ed, list) and len(ed) > 0: ed = ed[0]
            if hasattr(ed, 'date'): target_date = ed.date()
            elif isinstance(ed, datetime): target_date = ed.date()
            else: target_date = ed 
            today = datetime.now().date()
            if today <= target_date <= today + timedelta(days=3):
                return True
    except: pass
    return False

# ⚡ 升級：雙向共振檢查
def check_intraday_confluence(symbol, direction):
    try:
        df_1h = yf.Ticker(symbol).history(period="10d", interval="1h")
        if df_1h.empty or len(df_1h) < 10: return False
        current_price = df_1h.iloc[-1]['Close']
        
        for i in range(len(df_1h)-1, max(2, len(df_1h)-15), -1):
            if direction == 'long':
                # Bullish 1H FVG
                if df_1h['Low'].iloc[i] > df_1h['High'].iloc[i-2]:
                    fvg_bot = df_1h['High'].iloc[i-2]
                    fvg_mid = (df_1h['Low'].iloc[i] + fvg_bot) / 2
                    is_mitigated = any(df_1h['Low'].iloc[j] < fvg_mid for j in range(i+1, len(df_1h)))
                    if not is_mitigated and current_price > fvg_bot: return True
            else:
                # Bearish 1H FVG
                if df_1h['High'].iloc[i] < df_1h['Low'].iloc[i-2]:
                    fvg_top = df_1h['Low'].iloc[i-2]
                    fvg_mid = (fvg_top + df_1h['High'].iloc[i]) / 2
                    is_mitigated = any(df_1h['High'].iloc[j] > fvg_mid for j in range(i+1, len(df_1h)))
                    if not is_mitigated and current_price < fvg_top: return True
    except: pass
    return False

# ⚡ 升級：雙向 SMC 與 OB (訂單塊) 整合演算法
def detect_smc(df):
    if len(df) < 20:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0, 0, 0, 'long'
        
    current_price = df.iloc[-1]['Close']
    dist_20ma = df.iloc[-1]['Dist_20MA'] if ('Dist_20MA' in df.columns and not pd.isna(df.iloc[-1]['Dist_20MA'])) else 0.0
    
    # 決定戰略方向
    is_uptrend = dist_20ma > 0 
    direction = 'long' if is_uptrend else 'short'
    
    smc_id, smc_text, smc_class = None, "-", "border-gray-600 text-gray-400"
    entry_top, entry_bot, sl, tp, rrr = 0, 0, 0, 0, 0

    for i in range(len(df)-1, 2, -1):
        if is_uptrend:
            # 🟢 做多邏輯 (Bullish Setup)
            if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
                fvg_top = df['Low'].iloc[i]
                fvg_bot = df['High'].iloc[i-2]
                fvg_mid = (fvg_top + fvg_bot) / 2
                
                is_mitigated = any(df['Low'].iloc[j] < fvg_mid for j in range(i+1, len(df)))
                if not is_mitigated and current_price > fvg_bot:
                    swing_low_before_fvg = find_swing_low(df, i-1, 10)
                    is_sweep = df['Low'].iloc[i-2] <= swing_low_before_fvg
                    
                    smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                    smc_text = "🔥 S級 掃損+OB (多)" if is_sweep else "⚡ 1D FVG 缺口 (多)"
                    smc_class = "border-yellow-500 text-yellow-400 font-bold" if is_sweep else "border-blue-500 text-blue-400"
                    
                    # 結合 OB 緊縮區間
                    entry_top = fvg_top
                    entry_bot = fvg_bot
                    
                    ob_low = df['Low'].iloc[i-2]
                    sl_raw = min(ob_low, swing_low_before_fvg)
                    sl = sl_raw * 0.995 
                    
                    tp_raw = find_swing_high(df, len(df)-1, 40) 
                    entry_avg = (entry_top + entry_bot) / 2
                    risk = entry_avg - sl
                    
                    tp = entry_avg + (risk * 2.5) if tp_raw <= entry_top else tp_raw
                    
                    reward = tp - entry_avg
                    rrr = round(reward / risk, 1) if risk > 0 else 0
                    
                    if rrr >= 1.5: break
                    else: smc_id = None 
        else:
            # 🔴 做空邏輯 (Bearish Setup)
            if df['High'].iloc[i] < df['Low'].iloc[i-2]: 
                fvg_bot = df['High'].iloc[i]
                fvg_top = df['Low'].iloc[i-2]
                fvg_mid = (fvg_top + fvg_bot) / 2
                
                is_mitigated = any(df['High'].iloc[j] > fvg_mid for j in range(i+1, len(df)))
                if not is_mitigated and current_price < fvg_top:
                    swing_high_before_fvg = find_swing_high(df, i-1, 10)
                    is_sweep = df['High'].iloc[i-2] >= swing_high_before_fvg
                    
                    smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                    smc_text = "🩸 S級 掃損+OB (空)" if is_sweep else "📉 1D FVG 缺口 (空)"
                    smc_class = "border-red-500 text-red-400 font-bold" if is_sweep else "border-purple-500 text-purple-400"
                    
                    entry_bot = fvg_bot
                    entry_top = fvg_top
                    
                    ob_high = df['High'].iloc[i-2]
                    sl_raw = max(ob_high, swing_high_before_fvg)
                    sl = sl_raw * 1.005 
                    
                    tp_raw = find_swing_low(df, len(df)-1, 40) 
                    entry_avg = (entry_top + entry_bot) / 2
                    risk = sl - entry_avg
                    
                    tp = entry_avg - (risk * 2.5) if tp_raw >= entry_bot else tp_raw
                    
                    reward = entry_avg - tp
                    rrr = round(reward / risk, 1) if risk > 0 else 0
                    
                    if rrr >= 1.5: break
                    else: smc_id = None 
    
    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2), round(tp, 2), rrr, direction

def main():
    print(f"啟動全方位 SMC 量化雷達 (多空雙擊版)... 共監測 {len(WATCHLIST)} 檔股票")
    screener_data, watchlist_data = [], []
    chart_data_map = {} 
    
    for symbol in WATCHLIST:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(period="6mo")
            if df.empty: continue
            
            kline_list = []
            for idx, row in df.iterrows():
                kline_list.append({
                    "time": idx.strftime('%Y-%m-%d'),
                    "open": round(row['Open'], 2),
                    "high": round(row['High'], 2),
                    "low": round(row['Low'], 2),
                    "close": round(row['Close'], 2)
                })
            chart_data_map[symbol] = kline_list
            
            # ⚡ 計算宏觀絕對座標 (Macro Premium/Discount)
            macro_bsl = round(df['High'].max(), 2)
            macro_ssl = round(df['Low'].min(), 2)
            macro_eq = round((macro_bsl + macro_ssl) / 2, 2)
            
            df = calculate_indicators(df)
            
            latest, prev = df.iloc[-1], df.iloc[-2]
            current_price, prev_price = round(latest['Close'], 2), round(prev['Close'], 2)
            change = round(current_price - prev_price, 2)
            change_pct = round((change / prev_price) * 100, 2)
            
            watchlist_data.append({"symbol": symbol, "price": current_price, "change": change, "change_pct": change_pct})
            
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr, direction = detect_smc(df)
            
            rvol = round(latest['RVOL'], 2) if 'RVOL' in df.columns and not pd.isna(latest['RVOL']) else 1.0
            dist_20ma = round(latest['Dist_20MA'], 2) if 'Dist_20MA' in df.columns and not pd.isna(latest['Dist_20MA']) else 0.0
            
            label = "強勢多頭" if dist_20ma > 0 else "弱勢空頭"
            label_class = "bg-green-900/50 text-green-400" if dist_20ma > 0 else "bg-red-900/50 text-red-400"
            if rvol >= 2.0: label, label_class = "爆量行動", "bg-yellow-900/50 text-yellow-400"
            
            earnings_alert = get_earnings_alert(ticker_obj)
            confluence = check_intraday_confluence(symbol, direction) if smc_id is not None else False
            
            screener_data.append({
                "symbol": symbol, "price": current_price, "rvol": rvol, "dist_20ma": dist_20ma,
                "smc_id": smc_id, "smc_text": smc_text, "smc_class": smc_class,
                "label": label, "label_class": label_class, 
                "entry_bot": entry_bot, "entry_top": entry_top, "sl": sl, "tp": tp, "rrr": rrr,
                "earnings_alert": earnings_alert, "confluence": confluence,
                "direction": direction, "macro_bsl": macro_bsl, "macro_ssl": macro_ssl, "macro_eq": macro_eq
            })
            
            status_tag = "👑 共振" if confluence else ("⚠️ 財報" if earnings_alert else "✅ 完成")
            print(f"{status_tag} | {symbol} 解析完畢 ({'多' if direction=='long' else '空'}).")
            
        except Exception as e:
            print(f"❌ {symbol} 發生非致命錯誤 (跳過): {e}")

    def sort_key(x):
        score = 0
        if x.get('confluence'): score += 500
        if x['smc_id'] == 'fvg_sweep': score += 100
        elif x['smc_id'] == 'fvg': score += 50
        if x['rvol'] >= 1.5: score += x['rvol']
        if x.get('earnings_alert'): score -= 200
        return score
        
    screener_data.sort(key=sort_key, reverse=True)

    hk_timezone = timezone(timedelta(hours=8))
    updated_at = datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "updated_at": updated_at, 
        "watchlist_data": watchlist_data, 
        "screener_data": screener_data,
        "chart_data": chart_data_map 
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    print(f"\n🎉 雙向打擊與宏觀座標已成功寫入 data.json！\n🕒 最新更新時間: {updated_at} (HKT)")

if __name__ == "__main__":
    main()
