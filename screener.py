import json
import os
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# ==========================================
# 動態讀取 tickers.txt 核心監測名單
# ==========================================
def load_symbols(filename="tickers.txt"):
    symbols = []
    if not os.path.exists(filename):
        print(f"⚠️ 找不到 {filename}，使用預設名單。")
        return ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
        
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            clean_line = line.split('#')[0].strip().upper()
            if clean_line:
                symbols.append(clean_line)
                
    return list(dict.fromkeys(symbols))

SYMBOLS = load_symbols()

def get_market_data(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        df_1d = ticker.history(period="3mo", interval="1d")
        df_1h = ticker.history(period="1mo", interval="1h")
        df_15m = ticker.history(period="5d", interval="15m")
        df_4h = ticker.history(period="2mo", interval="1h") # 可近似模擬或以1h聚合
        if df_1d.empty or len(df_1d) < 20:
            return None, None, None, None
        return df_1d, df_1h, df_15m, df_4h
    except Exception as e:
        print(f"[{symbol}] 數據抓取失敗: {e}")
        return None, None, None, None

def calculate_smc(symbol: str, df_1d: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_4h: pd.DataFrame):
    try:
        current_price = round(float(df_1d['Close'].iloc[-1]), 2)
        prev_close = round(float(df_1d['Close'].iloc[-2]), 2)
        change = round(current_price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2)

        df_1d['MA20'] = df_1d['Close'].rolling(window=20).mean()
        ma20_val = float(df_1d['MA20'].iloc[-1])
        dist_20ma = round(current_price - ma20_val, 2)

        recent_bars = df_1d.tail(20)
        macro_bsl = round(float(recent_bars['High'].max()), 2)
        macro_ssl = round(float(recent_bars['Low'].min()), 2)
        macro_eq = round((macro_bsl + macro_ssl) / 2.0, 2)

        rng = max(macro_bsl - macro_ssl, 0.01)
        pd_percent = int(min(max(((current_price - macro_ssl) / rng) * 100, 0), 100))
        is_discount = current_price < macro_eq
        direction = "long" if is_discount else "short"

        down_candles = recent_bars[recent_bars['Close'] < recent_bars['Open']]
        if not down_candles.empty:
            last_down = down_candles.iloc[-1]
            entry_bot = round(float(last_down['Low']), 2)
            entry_top = round(float(last_down['High']), 2)
        else:
            entry_bot = round(macro_ssl * 1.01, 2)
            entry_top = round(macro_ssl * 1.03, 2)

        if entry_bot > entry_top:
            entry_bot, entry_top = entry_top, entry_bot

        if direction == "long":
            sl = round(entry_bot * 0.985, 2)
            tp = round(macro_bsl * 0.99, 2)
        else:
            sl = round(entry_top * 1.015, 2)
            tp = round(macro_ssl * 1.01, 2)

        risk = max(abs(current_price - sl), 0.5)
        reward = max(abs(tp - current_price), 0.5)
        rrr = round(reward / risk, 1) if risk > 0 else 0

        in_zone = (entry_bot <= current_price <= entry_top * 1.015)
        near_zone = (current_price > entry_top and (current_price - entry_top) / current_price < 0.02)
        
        confluence = bool(in_zone and is_discount)
        smc_id = f"SMC_{symbol}_{int(current_price)}"
        
        if confluence:
            smc_text, smc_class = "SS級 共振", "border-yellow-300 text-yellow-300 bg-yellow-900/30"
        elif in_zone:
            smc_text, smc_class = "4H 需求區 (OB)", "border-green-500 text-green-400 bg-green-900/20"
        else:
            smc_text, smc_class = "日線 FVG", "border-blue-500 text-blue-400 bg-blue-900/20"

        trade_status = "🟢 可執行" if in_zone else ("🟡 接近中" if near_zone else "🔴 觀察中")
        status_color = "text-green-400 font-bold" if in_zone else ("text-yellow-400" if near_zone else "text-gray-400")
        inv_text = f"區間 ${entry_bot} - ${entry_top}，嚴格防守 ${sl}。" if in_zone else "等待價格進入戰略區間。"

        # 打包各週期 K 線 (支援 1D, 1H, 15m, 4H 靜態基底精算)
        kline_1d = []
        for idx, row in df_1d.tail(60).iterrows():
            kline_1d.append({"time": idx.strftime("%Y-%m-%d"), "open": round(row['Open'],2), "high": round(row['High'],2), "low": round(row['Low'],2), "close": round(row['Close'],2)})

        kline_1h = []
        if df_1h is not None and not df_1h.empty:
            for idx, row in df_1h.tail(120).iterrows():
                kline_1h.append({"time": int(idx.timestamp()), "open": round(row['Open'],2), "high": round(row['High'],2), "low": round(row['Low'],2), "close": round(row['Close'],2)})

        kline_15m = []
        if df_15m is not None and not df_15m.empty:
            for idx, row in df_15m.tail(150).iterrows():
                kline_15m.append({"time": int(idx.timestamp()), "open": round(row['Open'],2), "high": round(row['High'],2), "low": round(row['Low'],2), "close": round(row['Close'],2)})

        kline_4h = []
        if df_4h is not None and not df_4h.empty:
            for idx, row in df_4h.tail(100).iterrows():
                kline_4h.append({"time": int(idx.timestamp()), "open": round(row['Open'],2), "high": round(row['High'],2), "low": round(row['Low'],2), "close": round(row['Close'],2)})

        summary = {
            "symbol": symbol, "price": current_price, "change": change, "change_pct": change_pct,
            "dist_20ma": dist_20ma, "macro_bsl": macro_bsl, "macro_ssl": macro_ssl, "macro_eq": macro_eq,
            "pd_percent": pd_percent, "direction": direction, "entry_bot": entry_bot, "entry_top": entry_top,
            "sl": sl, "tp": tp, "rrr": rrr, "smc_id": smc_id, "confluence": confluence,
            "smc_text": smc_text, "smc_class": smc_class, "trade_status": trade_status, 
            "status_color": status_color, "inv_text": inv_text
        }
        return summary, kline_1d, kline_1h, kline_15m, kline_4h
    except Exception as e:
        print(f"[{symbol}] SMC 計算出錯: {e}")
        return None, [], [], [], []

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行 70 檔標的精算掃描...")
    
    screener_list = []
    watchlist_list = []
    stocks_dict = {}

    for sym in SYMBOLS:
        print(f"-> 分析 {sym}...", end="", flush=True)
        df_1d, df_1h, df_15m, df_4h = get_market_data(sym)
        if df_1d is None:
            print(" [無數據跳過]")
            continue
            
        data, k1d, k1h, k15m, k4h = calculate_smc(sym, df_1d, df_1h, df_15m, df_4h)
        if data:
            screener_list.append(data)
            watchlist_list.append({"symbol": sym, "price": data["price"], "change": data["change"], "change_pct": data["change_pct"]})
            stocks_dict[sym] = {
                "summary": data, 
                "kline": k1d, 
                "kline_1h": k1h, 
                "kline_15m": k15m, 
                "kline_4h": k4h
            }
            print(f" [完成: ${data['price']}]")
        else:
            print(" [計算失敗]")

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S HKT"),
        "screener_data": screener_list,
        "watchlist_data": watchlist_list,
        "stocks": stocks_dict
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 掃描完成！70 檔數據已成功寫入 data.json")

if __name__ == "__main__":
    main()
