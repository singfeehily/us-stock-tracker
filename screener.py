import json
import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# ==========================================
# 監控股票清單 (包含指數 ETF 與核心自選/科技巨頭)
# ==========================================
SYMBOLS = [
    # 指數 / 板塊 ETF
    "SPY", "QQQ", "DIA", "SMH", "IWM", "SOXL", "VOO",
    # 科技巨頭 / 熱門標的
    "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META"
]

def get_market_data(symbol: str):
    """
    抓取日線與 1H 歷史數據
    (完全不調用 ticker.info，徹底杜絕 SPY/QQQ/DIA 等 ETF 出現 404 Not Found)
    """
    try:
        ticker = yf.Ticker(symbol)
        
        # 1. 日線數據 (用於大級別 SMC 結構計算)
        df_daily = ticker.history(period="3mo", interval="1d")
        if df_daily.empty or len(df_daily) < 15:
            return None, None
            
        # 2. 1H 數據 (用於精細化戰術區間)
        df_1h = ticker.history(period="1mo", interval="1h")
        if df_1h.empty:
            df_1h = df_daily # 備用降級
            
        return df_daily, df_1h
    except Exception as e:
        print(f"[{symbol}] 下載失敗: {e}")
        return None, None

def calculate_smc(symbol: str, df_daily: pd.DataFrame, df_1h: pd.DataFrame):
    """
    SMC 量化計算核心：同時計算數值指標與舊版前端所需的相容字串
    """
    try:
        # 當前即時價格 (取最新 Bar 的收盤價)
        current_price = round(float(df_daily['Close'].iloc[-1]), 2)
        prev_close = round(float(df_daily['Close'].iloc[-2]), 2)
        chg_pct = round(((current_price - prev_close) / prev_close) * 100, 2)
        
        # 1. 計算近期高低點 (近 20 根日 K)
        recent_bars = df_daily.tail(20)
        swing_high = float(recent_bars['High'].max())
        swing_low = float(recent_bars['Low'].min())
        
        # 0.5 均衡位 (Equilibrium)
        eq_level = round((swing_high + swing_low) / 2.0, 2)
        
        # 判斷溢價 (Premium) / 折價 (Discount)
        is_discount = current_price < eq_level
        pd_status = "折價 (Discount)" if is_discount else "溢價 (Premium)"
        
        # 2. 計算做多訂單塊 (Discount OB) 與 做空阻力區
        down_candles = recent_bars[recent_bars['Close'] < recent_bars['Open']]
        if not down_candles.empty:
            last_down = down_candles.iloc[-1]
            entry_bot = round(float(last_down['Low']), 2)
            entry_top = round(float(last_down['High']), 2)
        else:
            entry_bot = round(swing_low * 1.01, 2)
            entry_top = round(swing_low * 1.03, 2)
            
        if entry_bot > entry_top:
            entry_bot, entry_top = entry_top, entry_bot

        # 3. 交易狀態判定
        in_zone = (entry_bot <= current_price <= entry_top * 1.015)
        near_zone = (current_price > entry_top and (current_price - entry_top) / current_price < 0.02)
        
        if in_zone:
            trade_status = "可執行 (已入區)"
        elif near_zone:
            trade_status = "接近中"
        else:
            trade_status = "觀望中"

        # 4. 形態識別
        last_3_1h = df_1h.tail(3)
        has_fvg = False
        if len(last_3_1h) >= 3:
            if last_3_1h['High'].iloc[0] < last_3_1h['Low'].iloc[2]:
                has_fvg = True
                
        if has_fvg and in_zone:
            pattern = "SS級共振 (OB+FVG)"
        elif is_discount:
            pattern = "4H 需求區 (OB)"
        else:
            pattern = "4H 供應區 (OB)"

        # 5. 動態盈虧比 (R:R)
        stop_loss = round(entry_bot * 0.985, 2)
        take_profit = round(swing_high * 0.99, 2)
        risk = max(current_price - stop_loss, 0.5)
        reward = max(take_profit - current_price, 0.5)
        rr_ratio = round(reward / risk, 1)
        rr_display = f"1:{max(rr_ratio, 1.2)}"

        # 6. 生成圖表 K 線格式 (支援字串日期與時間戳)
        kline_data = []
        for idx, row in df_daily.tail(60).iterrows():
            kline_data.append({
                "time": idx.strftime("%Y-%m-%d"),
                "timestamp": int(idx.timestamp()),
                "open": round(float(row['Open']), 2),
                "high": round(float(row['High']), 2),
                "low": round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
                "volume": int(row['Volume'])
            })

        # ========================================================
        # 核心相容文字欄位（覆蓋舊版所有 Key 的變形名稱）
        # ========================================================
        macro_text = f"處於 {pd_status} 區間 | 0.5 均衡位: ${eq_level} (波段低高點: ${swing_low:.2f} - ${swing_high:.2f})"
        trend_text = f"日線波段趨勢排列 | 動能平衡 | 當日漲跌幅: {chg_pct}%"
        smc_text = f"關鍵入場區間: ${entry_bot} - ${entry_top} | 建議止損: ${stop_loss} | 目標位: ${take_profit}"
        insight_text = f"識別形態: {pattern} | 預期盈虧比: {rr_display} | 操作建議: {trade_status}"

        base_dict = {
            "symbol": symbol,
            "current_price": current_price,
            "chg_pct": chg_pct,
            "trade_status": trade_status,
            "pattern": pattern,
            "rr": rr_display,
            "entry_zone": f"${entry_bot} - ${entry_top}",
            "entry_bot": entry_bot,
            "entry_top": entry_top,
            "pd_status": pd_status,
            "eq_level": eq_level,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "swing_low": swing_low,
            "swing_high": swing_high,
            
            # 各種命名的文字欄位相容
            "macro_structure": macro_text,
            "macro": macro_text,
            "trend_momentum": trend_text,
            "trend": trend_text,
            "smc_tactics": smc_text,
            "smc": smc_text,
            "actionable_insight": insight_text,
            "actionable_insights": insight_text,
            "insight": insight_text,
            "insights": insight_text
        }

        return base_dict, kline_data

    except Exception as e:
        print(f"[{symbol}] SMC 計算出錯: {e}")
        return None, None

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行 SMC 量化掃描 (共 {len(SYMBOLS)} 檔標的)...")
    
    screener_results = []
    stock_details = {}
    flat_stocks = {}

    for sym in SYMBOLS:
        print(f"-> 正在分析 {sym}...", end="", flush=True)
        df_d, df_1h = get_market_data(sym)
        if df_d is None:
            print(" [跳過: 無數據]")
            continue
            
        data, kline = calculate_smc(sym, df_d, df_1h)
        if data:
            screener_results.append(data)
            
            # 建立多層級相容結構：
            # 1. 支援 stocks[sym].summary
            # 2. 支援 stocks[sym].macro_structure
            # 3. 支援 data[sym].macro_structure
            merged_info = {
                **data,
                "summary": data,
                "kline": kline
            }
            stock_details[sym] = merged_info
            flat_stocks[sym] = merged_info
            
            print(f" [完成: 現價 ${data['current_price']} | {data['trade_status']}]")
        else:
            print(" [計算失敗]")

    # 構造完整輸出的 JSON
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S HKT"),
        "total_scanned": len(screener_results),
        "screener": screener_results,
        "stocks": stock_details,
        **flat_stocks  # 將股票代號平鋪於 JSON 最頂層，支援根目錄直接讀取
    }

    output_path = "data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 掃描完成！已成功將數據輸出至 {output_path} (共納入 {len(screener_results)} 隻有效股票)")

if __name__ == "__main__":
    main()
