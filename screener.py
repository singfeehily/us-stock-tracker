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

def find_swing_low(df, current_idx, lookback=20):
    start = max(0, current_idx - lookback)
    return df['Low'].iloc[start:current_idx].min()

# ⚡ 核心演算法升級：尋找真實的 SMC 目標價 (未緩解 FVG 或 流動性池)
def find_smc_tp(df, current_idx, direction, entry_price):
    lookback = min(60, current_idx)
    ufvg_levels = []
    
    for i in range(current_idx - lookback + 2, current_idx):
        if direction == 'long':
            # 尋找做多目標：上方未緩解的看跌 FVG (Bearish FVG)
            if df['High'].iloc[i] < df['Low'].iloc[i-2]:
                fvg_bot = df['High'].iloc[i]
                if fvg_bot > entry_price:
                    # 檢查此 FVG 是否已被後續的 K 線刺破 (緩解)
                    mitigated = False
                    for j in range(i+1, current_idx+1):
                        if df['High'].iloc[j] >= fvg_bot:
                            mitigated = True
                            break
                    if not mitigated:
                        ufvg_levels.append(fvg_bot)
        else:
            # 尋找做空目標：下方未緩解的看漲 FVG (Bullish FVG)
            if df['Low'].iloc[i] > df['High'].iloc[i-2]:
                fvg_top = df['Low'].iloc[i]
                if fvg_top < entry_price:
                    mitigated = False
                    for j in range(i+1, current_idx+1):
                        if df['Low'].iloc[j] <= fvg_top:
                            mitigated = True
                            break
                    if not mitigated:
                        ufvg_levels.append(fvg_top)
                        
    # 結合波段高低點 (Liquidity Pool)
    if direction == 'long':
        swing_target = find_swing_high(df, current_idx, 40)
        # 取距離最近的阻力作為 TP1
        if ufvg_levels:
            return min(min(ufvg_levels), swing_target)
        return swing_target
    else:
        swing_target = find_swing_low(df, current_idx, 40)
        if ufvg_levels:
            return max(max(ufvg_levels), swing_target)
        return swing_target

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

def check_intraday_confluence(symbol, direction):
    try:
        df_1h = yf.Ticker(symbol).history(period="10d", interval="1h")
        if df_1h.empty or len(df_1h) < 10: return False
        current_price = df_1h.iloc[-1]['Close']
        for i in range(len(df_1h)-1, max(2, len(df_1h)-15), -1):
            if direction == 'long':
                if df_1h['Low'].iloc[i] > df_1h['High'].iloc[i-2]:
                    fvg_bot = df_1h['High'].iloc[i-2]
                    fvg_mid = (df_1h['Low'].iloc[i] + fvg_bot) / 2
                    if not any(df_1h['Low'].iloc[j] < fvg_mid for j in range(i+1, len(df_1h))) and current_price > fvg_bot: return True
            else:
                if df_1h['High'].iloc[i] < df_1h['Low'].iloc[i-2]:
                    fvg_top = df_1h['Low'].iloc[i-2]
                    fvg_mid = (fvg_top + df_1h['High'].iloc[i]) / 2
                    if not any(df_1h['High'].iloc[j] > fvg_mid for j in range(i+1, len(df_1h))) and current_price < fvg_top: return True
    except: pass
    return False

def detect_smc(df):
    if len(df) < 20:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0, 0, 0, 'long', False, 0
        
    current_price = df.iloc[-1]['Close']
    dist_20ma = df.iloc[-1]['Dist_20MA'] if ('Dist_20MA' in df.columns and not pd.isna(df.iloc[-1]['Dist_20MA'])) else 0.0
    is_uptrend = dist_20ma > 0 
    direction = 'long' if is_uptrend else 'short'
    
    smc_id, smc_text, smc_class = None, "-", "border-gray-600 text-gray-400"
    entry_top, entry_bot, sl, tp, rrr = 0, 0, 0, 0, 0
    is_sweep = False

    for i in range(len(df)-1, 2, -1):
        if is_uptrend:
            if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
                fvg_top, fvg_bot = df['Low'].iloc[i], df['High'].iloc[i-2]
                fvg_mid = (fvg_top + fvg_bot) / 2
                
                if not any(df['Low'].iloc[j] < fvg_mid for j in range(i+1, len(df))) and current_price > fvg_bot:
                    swing_low_before_fvg = find_swing_low(df, i-1, 10)
                    is_sweep = df['Low'].iloc[i-2] <= swing_low_before_fvg
                    
                    smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                    smc_text = "🔥 掃損+OB (多)" if is_sweep else "⚡ 1D FVG (多)"
                    smc_class = "border-green-500 text-green-400"
                    
                    entry_top, entry_bot = fvg_top, fvg_bot
                    
                    # 防守(SL)：波段低點往下預留 0.5% 防止獵殺
                    sl = min(df['Low'].iloc[i-2], swing_low_before_fvg) * 0.995 
                    
                    # 真實進場中軸與真實 TP1
                    entry_mid = (entry_top + entry_bot) / 2
                    tp_raw = find_smc_tp(df, len(df)-1, 'long', entry_top)
                    
                    risk = entry_mid - sl
                    reward = tp_raw - entry_mid
                    
                    if risk > 0 and reward > 0:
                        tp = tp_raw
                        rrr = round(reward / risk, 1)
                        if rrr >= 1.0: break # 只顯示 RRR 大於 1 的，讓濾網去擋小於 2 的
                    smc_id = None 
        else:
            if df['High'].iloc[i] < df['Low'].iloc[i-2]: 
                fvg_bot, fvg_top = df['High'].iloc[i], df['Low'].iloc[i-2]
                fvg_mid = (fvg_top + fvg_bot) / 2
                
                if not any(df['High'].iloc[j] > fvg_mid for j in range(i+1, len(df))) and current_price < fvg_top:
                    swing_high_before_fvg = find_swing_high(df, i-1, 10)
                    is_sweep = df['High'].iloc[i-2] >= swing_high_before_fvg
                    
                    smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                    smc_text = "🩸 掃損+OB (空)" if is_sweep else "📉 1D FVG (空)"
                    smc_class = "border-red-500 text-red-400"
                    
                    entry_top, entry_bot = fvg_top, fvg_bot
                    
                    # 防守(SL)：波段高點往上預留 0.5% 防止獵殺
                    sl = max(df['High'].iloc[i-2], swing_high_before_fvg) * 1.005 
                    
                    entry_mid = (entry_top + entry_bot) / 2
                    tp_raw = find_smc_tp(df, len(df)-1, 'short', entry_bot)
                    
                    risk = sl - entry_mid
                    reward = entry_mid - tp_raw
                    
                    if risk > 0 and reward > 0:
                        tp = tp_raw
                        rrr = round(reward / risk, 1)
                        if rrr >= 1.0: break
                    smc_id = None 
    
    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2), round(tp, 2), rrr, direction, is_sweep

def main():
    print(f"啟動全方位 SMC 量化雷達 (真實 TP 演算法升級)...")
    screener_data, watchlist_data, chart_data_map = [], [], {}
    
    for symbol in WATCHLIST:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(period="6mo")
            if df.empty: continue
            
            chart_data_map[symbol] = [{"time": idx.strftime('%Y-%m-%d'), "open": round(row['Open'], 2), "high": round(row['High'], 2), "low": round(row['Low'], 2), "close": round(row['Close'], 2)} for idx, row in df.iterrows()]
            
            macro_bsl, macro_ssl = round(df['High'].max(), 2), round(df['Low'].min(), 2)
            macro_eq = round((macro_bsl + macro_ssl) / 2, 2)
            
            df = calculate_indicators(df)
            latest, prev = df.iloc[-1], df.iloc[-2]
            current_price = round(latest['Close'], 2)
            change_pct = round(((current_price - round(prev['Close'], 2)) / round(prev['Close'], 2)) * 100, 2)
            
            pd_percent = 50
            if macro_bsl - macro_ssl > 0:
                pd_percent = round(((current_price - macro_ssl) / (macro_bsl - macro_ssl)) * 100, 1)
                pd_percent = max(0, min(100, pd_percent))
            
            watchlist_data.append({"symbol": symbol, "price": current_price, "change": round(current_price - round(prev['Close'], 2), 2), "change_pct": change_pct})
            
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr, direction, is_sweep = detect_smc(df)
            earnings_alert = get_earnings_alert(ticker_obj)
            confluence = check_intraday_confluence(symbol, direction) if smc_id else False
            
            stars = 0
            if smc_id:
                stars += 1 
                if is_sweep: stars += 1 
                if (direction == 'long' and pd_percent < 50) or (direction == 'short' and pd_percent > 50): stars += 1 
                if rrr >= 2.0: stars += 1 
                if confluence: stars += 1 
            
            trade_status = "無效"
            status_color = "text-gray-500"
            inv_text = "無形態"
            
            if smc_id:
                if earnings_alert:
                    trade_status = "⚠️ 財報將至 (暫停交易)"
                    status_color = "text-yellow-500 font-bold"
                # ⚡ 嚴格濾網發揮作用：真實 TP 算出來的 R:R 若低於 2，強制擋單
                elif rrr < 2.0:
                    trade_status = f"🔴 觀察中 (❌ RRR {rrr} 不佳)"
                    status_color = "text-red-500"
                elif stars >= 4:
                    trade_status = "🟢 可執行"
                    status_color = "text-green-400 font-bold"
                else:
                    trade_status = f"🔴 觀察中 (⭐ {stars}星 條件不足)"
                    status_color = "text-gray-400"
                
                inv_dir = "收低於" if direction == 'long' else "收高於"
                inv_struct = "看漲" if direction == 'long' else "看跌"
                inv_text = f"🚨 紀律底線：若日線實體{inv_dir} ${sl}，{inv_struct}結構即刻失效，必須無條件平倉。"

            screener_data.append({
                "symbol": symbol, "price": current_price,
                "smc_id": smc_id, "smc_text": smc_text, "smc_class": smc_class,
                "entry_bot": entry_bot, "entry_top": entry_top, "sl": sl, "tp": tp, "rrr": rrr,
                "direction": direction, "macro_bsl": macro_bsl, "macro_ssl": macro_ssl, "macro_eq": macro_eq,
                "pd_percent": pd_percent, "stars": stars, "trade_status": trade_status, 
                "status_color": status_color, "inv_text": inv_text, "confluence": confluence
            })
            print(f"完成 {symbol} | 狀態: {trade_status} | TP1: ${tp} | RRR: {rrr}")
            
        except Exception as e:
            print(f"❌ {symbol} 錯誤: {e}")

    def sort_key(x):
        if x['smc_id'] is None: return -100
        score = x['stars'] * 1000 + x['rrr'] * 100
        if x['trade_status'].startswith('⚠️'): score -= 5000
        if '❌' in x['trade_status']: score -= 2000
        return score
    screener_data.sort(key=sort_key, reverse=True)

    hk_timezone = timezone(timedelta(hours=8))
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump({"updated_at": datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S"), "watchlist_data": watchlist_data, "screener_data": screener_data, "chart_data": chart_data_map}, f, ensure_ascii=False, indent=4)
    print("🎉 data.json 寫入完成！真實 SMC 目標已生效。")

if __name__ == "__main__":
    main()
