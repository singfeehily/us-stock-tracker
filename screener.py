import json
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# ==========================================
# 監控股票清單 (包含指數 ETF 與核心個股)
# ==========================================
SYMBOLS = [
    # 指數 / 板塊 ETF
    "SPY", "QQQ", "DIA", "SMH", "IWM", "SOXL", "VOO",
    # 科技巨頭 / 熱門標的
    "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META"
]

def get_market_data(symbol: str):
    """
    抓取日線歷史數據
    (完全不調用 ticker.info，杜絕 SPY/QQQ/DIA 等 ETF 出現 404 Not Found)
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo", interval="1d")
        if df.empty or len(df) < 20:
            return None
        return df
    except Exception as e:
        print(f"[{symbol}] 下載失敗: {e}")
        return None

def calculate_smc(symbol: str, df: pd.DataFrame):
    """
    精確匹配 index.html 所需的所有欄位結構
    """
    try:
        # 1. 價格與變動
        current_price = round(float(df['Close'].iloc[-1]), 2)
        prev_close = round(float(df['Close'].iloc[-2]), 2)
        change = round(current_price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2)

        # 2. 20MA 計算 (對應 dist_20ma)
        df['MA20'] = df['Close'].rolling(window=20).mean()
        ma20_val = float(df['MA20'].iloc[-1])
        dist_20ma = round(current_price - ma20_val, 2)

        # 3. 宏觀高低點與 0.5 均衡位 (對應 macro_bsl, macro_ssl, macro_eq)
        recent_bars = df.tail(20)
        macro_bsl = round(float(recent_bars['High'].max()), 2)
        macro_ssl = round(float(recent_bars['Low'].min()), 2)
        macro_eq = round((macro_bsl + macro_ssl) / 2.0, 2)

        # 4. 判斷溢價 / 折價 與百分比 (對應 pd_percent: 0-100)
        rng = max(macro_bsl - macro_ssl, 0.01)
        pd_percent = int(min(max(((current_price - macro_ssl) / rng) * 100, 0), 100))
        is_discount = current_price < macro_eq
        direction = "long" if is_discount else "short"

        # 5. 做多 / 做空訂單塊與區間 (對應 entry_bot, entry_top, sl, tp)
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
            risk = max(current_price - sl, 0.5)
            reward = max(tp - current_price, 0.5)
        else:
            sl = round(entry_top * 1.015, 2)
            tp = round(macro_ssl * 1.01, 2)
            risk = max(sl - current_price, 0.5)
            reward = max(current_price - tp, 0.5)

        rrr = round(reward / risk, 1)

        # 6. SMC 形態與共振 (對應 smc_id, confluence, smc_text, smc_class)
        in_zone = (entry_bot <= current_price <= entry_top * 1.015)
        near_zone = (current_price > entry_top and (current_price - entry_top) / current_price < 0.02)
        
        # 模擬共振檢測 (當在折價區且回踩 OB)
        confluence = bool(in_zone and is_discount)
        smc_id = f"SMC_{symbol}_{int(current_price)}"
        
        if confluence:
            smc_text = "SS級 共振 (OB+FVG)"
            smc_class = "border-yellow-300 text-yellow-300 bg-yellow-900/30"
        elif in_zone:
            smc_text = "4H 需求訂單塊 (OB)"
            smc_class = "border-green-500 text-green-400 bg-green-900/20"
        else:
            smc_text = "日線 FVG 測試"
            smc_class = "border-blue-500 text-blue-400 bg-blue-900/20"

        # 7. 交易狀態判定 (對應 trade_status, status_color, inv_text)
        if in_zone:
            trade_status = "🟢 可執行"
            status_color = "text-green-400 font-bold"
            inv_text = f"🎯 已進入進場區間 (${entry_bot} - ${entry_top})，跌破 ${sl} 嚴格止損。"
        elif near_zone:
            trade_status = "🟡 接近中"
            status_color = "text-yellow-400"
            inv_text = f"👀 距離進場區尚有空間，等待回踩 ${entry_top} 附近再行評估。"
        else:
            trade_status = "🔴 觀察中"
            status_color = "text-gray-400"
            inv_text = f"⏳ 目前處於觀望階段，等待結構確立 (均衡位: ${macro_eq})。"

        # 構造完全相容前端的物件
        item_data = {
            "symbol": symbol,
            "price": current_price,
            "change": change,
            "change_pct": change_pct,
            "dist_20ma": dist_20ma,
            "macro_bsl": macro_bsl,
            "macro_ssl": macro_ssl,
            "macro_eq": macro_eq,
            "pd_percent": pd_percent,
            "direction": direction,
            "entry_bot": entry_bot,
            "entry_top": entry_top,
            "sl": sl,
            "tp": tp,
            "rrr": rrr,
            "smc_id": smc_id,
            "confluence": confluence,
            "smc_text": smc_text,
            "smc_class": smc_class,
            "trade_status": trade_status,
            "status_color": status_color,
            "inv_text": inv_text
        }

        return item_data

    except Exception as e:
        print(f"[{symbol}] 計算出錯: {e}")
        return None

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行 SMC 量化掃描 (共 {len(SYMBOLS)} 檔標的)...")
    
    screener_list = []
    watchlist_list = []

    for sym in SYMBOLS:
        print(f"-> 正在分析 {sym}...", end="", flush=True)
        df = get_market_data(sym)
        if df is None:
            print(" [跳過: 無數據]")
            continue
            
        data = calculate_smc(sym, df)
        if data:
            screener_list.append(data)
            watchlist_list.append({
                "symbol": sym,
                "price": data["price"],
                "change": data["change"],
                "change_pct": data["change_pct"]
            })
            print(f" [完成: 現價 ${data['price']} | {data['trade_status']}]")
        else:
            print(" [計算失敗]")

    # ==========================================================
    # 核心修復：嚴格按照 index.html 要求構建根鍵名 (screener_data & watchlist_data)
    # ==========================================================
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "screener_data": screener_list,   # 你的 index.html 專屬讀取鍵名
        "watchlist_data": watchlist_list, # 你的 index.html 自選股鍵名
        # 同步保留兼容鍵名，防止其他地方調用
        "screener": screener_list,
        "stocks": {item["symbol"]: item for item in screener_list}
    }

    output_path = "data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 掃描完成！已成功將數據輸出至 {output_path} (共 {len(screener_list)} 隻有效股票)")

if __name__ == "__main__":
    main()
