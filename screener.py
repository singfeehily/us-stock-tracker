import json
import datetime
import pandas as pd
import yfinance as yf

def load_tickers():
    try:
        with open('tickers.txt', 'r') as f:
            tickers = [line.strip().upper() for line in f if line.strip()]
        return tickers
    except FileNotFoundError:
        # 預設清單以防檔案不存在
        return ['AAPL', 'NVDA', 'TSLA', 'PLTR', 'SMCI']

def calculate_metrics():
    tickers = load_tickers()
    output_data = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "watchlist_data": [],
        "screener_data": []
    }
    
    print(l(f"開始下載並分析 {len(tickers)} 隻股票數據..."))
    
    for symbol in tickers:
        try:
            # 下載過去 100 天的日線數據
            stock = yf.Ticker(symbol)
            df = stock.history(period="100d")
            
            if df.empty or len(df) < 20:
                continue
                
            current_price = float(df['Close'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            change = current_price - prev_close
            change_pct = (change / prev_close) * 100
            
            # 計算 20 日移動平均線 (20MA)
            df['MA20'] = df['Close'].rolling(window=20).mean()
            ma20 = float(df['MA20'].iloc[-1])
            dist_20ma = ((current_price - ma20) / ma20) * 100
            
            # 計算相對成交量 (RVOL)：今日成交量 / 過去 5 日平均成交量
            df['Vol5_Avg'] = df['Volume'].rolling(window=5).mean()
            avg_vol = float(df['Vol5_Avg'].iloc[-2]) # 前5日均量
            today_vol = float(df['Volume'].iloc[-1])
            rvol = round(today_vol / avg_vol, 1) if avg_vol > 0 else 1.0
            
            # 簡單判定標籤與 SMC 形態 (示範邏輯)
            if rvol >= 2.0 and dist_20ma < 3.0:
                label = "[強烈買入]"
                label_class = "bg-green-500/20 text-green-400 border border-green-500/50"
                smc_text = "回踩 15m FVG"
                smc_id = "fvg"
                smc_class = "bg-purple-900/60 text-purple-300 border-purple-700/50"
            else:
                label = "[多頭強勢]"
                label_class = "bg-blue-500/20 text-blue-400 border border-blue-500/50"
                smc_text = "流動性掃掠"
                smc_id = "sweep"
                smc_class = "bg-red-900/60 text-red-300 border-red-700/50"

            stock_info = {
                "symbol": symbol,
                "price": round(current_price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "rvol": rvol,
                "dist_20ma": round(dist_20ma, 1),
                "label": label,
                "label_class": label_class,
                "smc_text": smc_text,
                "smc_id": smc_id,
                "smc_class": smc_class,
                "entry_top": round(current_price * 0.98, 2),
                "entry_bot": round(current_price * 0.95, 2),
                "sl": round(current_price * 0.92, 2)
            }
            
            output_data["screener_data"].append(stock_info)
            output_data["watchlist_data"].append(stock_info)
            
        except Exception as e:
            print(f"處理 {symbol} 時發生錯誤: {e}")
            
    # 寫入 data.json 供前端讀取
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    print("data.json 生成成功！")

if __name__ == "__main__":
    calculate_metrics()
