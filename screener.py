import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone, timedelta

def load_tickers(filename='tickers.txt'):
    """從 tickers.txt 讀取股票代號名單"""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            # 讀取每一行，去除前後空白與換行，並轉大寫。自動忽略空行與 # 註解。
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

# ==========================================
# 🛡️ 升級 1：財報與宏觀避險過濾器 (安全防護版)
# ==========================================
def get_earnings_alert(ticker_obj):
    """安全獲取財報日期，若在 3 日內將返回 True"""
    try:
        cal = ticker_obj.calendar
        if isinstance(cal, dict) and 'Earnings Date' in cal:
            ed = cal['Earnings Date']
            # yfinance 有時返回列表，有時返回單一日期
            if isinstance(ed, list) and len(ed) > 0:
                ed = ed[0]
            
            # 將 datetime 物件轉為標準 date 進行比對
            if hasattr(ed, 'date'):
                target_date = ed.date()
            elif isinstance(ed, datetime):
                target_date = ed.date()
            else:
                target_date = ed # 假設它已經是 date 對象
                
            today = datetime.now().date()
            # 檢查財報日是否在「今天」到「未來 3 天」之內
            if today <= target_date <= today + timedelta(days=3):
                return True
    except Exception as e:
        # 絕對不允許 API 報錯導致腳本崩潰，靜默處理
        pass
    
    return False

# ==========================================
# 🛡️ 升級 2：雙時間週期共振演算法 (1D + 1H)
# ==========================================
def check_intraday_confluence(symbol):
    """檢查小時級別是否也同步出現看漲 FVG 結構"""
    try:
        # 提取最近 10 天的 1 小時 K 線
        df_1h = yf.Ticker(symbol).history(period="10d", interval="1h")
        if df_1h.empty or len(df_1h) < 10: 
            return False
            
        current_price = df_1h.iloc[-1]['Close']
        
        # 檢查最近 15 根小時線是否具備未被填補的看漲缺口 (Bullish FVG)
        for i in range(len(df_1h)-1, max(2, len(df_1h)-15), -1):
            if df_1h['Low'].iloc[i] > df_1h['High'].iloc[i-2]:
                fvg_bot = df_1h['High'].iloc[i-2]
                fvg_mid = (df_1h['Low'].iloc[i] + fvg_bot) / 2
                
                is_mitigated = False
                for j in range(i+1, len(df_1h)):
                    if df_1h['Low'].iloc[j] < fvg_mid:
                        is_mitigated = True
                        break
                        
                # 若 1H 缺口乾淨且現價依然在其上方，確認共振！
                if not is_mitigated and current_price > fvg_bot:
                    return True
    except Exception:
        # 若抓取 1h 報價失敗，安全回退為 False
        pass
    return False

def detect_smc(df):
    if len(df) < 20:
        return None, "-", "border-gray-600 text-gray-400", 0, 0, 0, 0, 0
        
    current_price = df.iloc[-1]['Close']
    
    dist_20ma = 0.0
    if 'Dist_20MA' in df.columns and not pd.isna(df.iloc[-1]['Dist_20MA']):
        dist_20ma = df.iloc[-1]['Dist_20MA']
        
    is_uptrend = dist_20ma > 0 
    
    smc_id, smc_text, smc_class = None, "-", "border-gray-600 text-gray-400"
    entry_top, entry_bot, sl, tp, rrr = 0, 0, 0, 0, 0
    
    if not is_uptrend:
        return smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr

    for i in range(len(df)-1, 2, -1):
        if df['Low'].iloc[i] > df['High'].iloc[i-2]: 
            fvg_top = df['Low'].iloc[i]
            fvg_bot = df['High'].iloc[i-2]
            fvg_mid = (fvg_top + fvg_bot) / 2
            
            is_mitigated = False
            for j in range(i+1, len(df)):
                if df['Low'].iloc[j] < fvg_mid:
                    is_mitigated = True
                    break
                    
            if not is_mitigated and current_price > fvg_bot:
                swing_low_before_fvg = find_swing_low(df, i-1, 10)
                is_sweep = df['Low'].iloc[i-2] <= swing_low_before_fvg
                
                smc_id = 'fvg_sweep' if is_sweep else 'fvg'
                smc_text = "🔥 S級 掃損+FVG" if is_sweep else "⚡ 1D FVG 缺口"
                smc_class = "border-yellow-500 text-yellow-400 font-bold" if is_sweep else "border-blue-500 text-blue-400"
                
                entry_top, entry_bot = fvg_top, fvg_bot
                sl = find_swing_low(df, i, 10) * 0.995 
                tp = find_swing_high(df, len(df)-1, 40) 
                
                risk = entry_bot - sl
                reward = tp - entry_bot
                rrr = round(reward / risk, 1) if risk > 0 else 0
                
                if rrr >= 1.5:
                    break
                else:
                    smc_id = None 
    
    return smc_id, smc_text, smc_class, round(entry_top, 2), round(entry_bot, 2), round(sl, 2), round(tp, 2), rrr

def main():
    print(f"啟動高勝率 SMC 量化雷達... 共監測 {len(WATCHLIST)} 檔股票")
    screener_data, watchlist_data = [], []
    
    for symbol in WATCHLIST:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(period="6mo")
            if df.empty: continue
            
            df = calculate_indicators(df)
            
            latest, prev = df.iloc[-1], df.iloc[-2]
            current_price, prev_price = round(latest['Close'], 2), round(prev['Close'], 2)
            change = round(current_price - prev_price, 2)
            change_pct = round((change / prev_price) * 100, 2)
            
            watchlist_data.append({"symbol": symbol, "price": current_price, "change": change, "change_pct": change_pct})
            
            smc_id, smc_text, smc_class, entry_top, entry_bot, sl, tp, rrr = detect_smc(df)
            
            rvol = 1.0
            if 'RVOL' in df.columns and not pd.isna(latest['RVOL']):
                rvol = round(latest['RVOL'], 2)
                
            dist_20ma = 0.0
            if 'Dist_20MA' in df.columns and not pd.isna(latest['Dist_20MA']):
                dist_20ma = round(latest['Dist_20MA'], 2)
            
            label = "多頭" if dist_20ma > 0 else "空頭"
            label_class = "bg-green-900/50 text-green-400" if dist_20ma > 0 else "bg-red-900/50 text-red-400"
            if rvol >= 2.0: label, label_class = "爆量", "bg-yellow-900/50 text-yellow-400"
            
            # ⚡ 動態植入：財報避險過濾器
            earnings_alert = get_earnings_alert(ticker_obj)
            
            # ⚡ 動態植入：雙週期共振引擎
            confluence = False
            if smc_id is not None:
                # 只有當 1D 發生高勝率形態時，才啟動子執行緒核對 1H 結構，節省運算資源
                confluence = check_intraday_confluence(symbol)
            
            screener_data.append({
                "symbol": symbol, "price": current_price, "rvol": rvol, "dist_20ma": dist_20ma,
                "smc_id": smc_id, "smc_text": smc_text, "smc_class": smc_class,
                "label": label, "label_class": label_class, 
                "entry_bot": entry_bot, "entry_top": entry_top, "sl": sl, "tp": tp, "rrr": rrr,
                "earnings_alert": earnings_alert,  # 傳遞給前端
                "confluence": confluence           # 傳遞給前端
            })
            
            # 終端機狀態輸出
            status_tag = "👑 共振" if confluence else ("⚠️ 財報" if earnings_alert else "✅ 完成")
            print(f"{status_tag} | {symbol} 解析完畢.")
            
        except Exception as e:
            print(f"❌ {symbol} 發生非致命錯誤 (跳過): {e}")

    def sort_key(x):
        score = 0
        # 👑 共振訊號獲得最高權重
        if x.get('confluence'): score += 500
        if x['smc_id'] == 'fvg_sweep': score += 100
        elif x['smc_id'] == 'fvg': score += 50
        if x['rvol'] >= 1.5: score += x['rvol']
        # ⚠️ 財報風險會降低優先度
        if x.get('earnings_alert'): score -= 200
        return score
        
    screener_data.sort(key=sort_key, reverse=True)

    hk_timezone = timezone(timedelta(hours=8))
    updated_at = datetime.now(hk_timezone).strftime("%Y-%m-%d %H:%M:%S")

    output = {"updated_at": updated_at, "watchlist_data": watchlist_data, "screener_data": screener_data}

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    print(f"\n🎉 雙週期量化引擎與財報避險過濾已成功寫入 data.json！\n🕒 最新更新時間: {updated_at} (HKT)")

if __name__ == "__main__":
    main()
