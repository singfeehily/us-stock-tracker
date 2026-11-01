from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from datetime import datetime, timedelta
import threading
import time
import math
import yfinance as yf
import os

app = Flask(__name__)
CORS(app)

# ==============================================================================
# [雙保底機制初始化] 嘗試連接富途 OpenD，若失敗則自動切換至 Yahoo Finance 備援
# ==============================================================================
USE_FUTU = False
quote_ctx = None

try:
    from futu import *
    FUTU_HOST = os.getenv('FUTU_HOST', '127.0.0.1')
    FUTU_PORT = int(os.getenv('FUTU_PORT', 11111))
    quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    print(f"[SYS] 成功連接至 Futu OpenD ({FUTU_HOST}:{FUTU_PORT}) - 優先使用實時通道")
    USE_FUTU = True
except Exception as e:
    print(f"[SYS] 未能連接 Futu OpenD，自動啟用 Yahoo Finance 雲端雙保底備援通道: {e}")
    USE_FUTU = False

futu_lock = threading.Lock()
cache_lock = threading.Lock()
USER_PRIORITY_TS = 0.0

MACRO_4H_CACHE = {}
DAILY_QUOTE_CACHE = {}
DYNAMIC_NAME_CACHE = {}
CACHE_TTL_SECONDS = 180
QUOTE_TTL_SECONDS = 60

MARKET_RADAR_UNIVERSE = [
    'NVDA', 'AMD', 'AVGO', 'TSM', 'MU', 'SMCI', 'ARM', 'MRVL', 'QCOM', 'INTC', 'AMAT', 'LRCX', 'KLAC', 'ASML', 'SNDK', 'WDC', 'STX',
    'AAPL', 'MSFT', 'AMZN', 'META', 'GOOGL', 'TSLA', 'NFLX', 'PLTR', 'CRM', 'NOW', 'SNOW', 'PANW', 'CRWD', 'NET', 'DDOG', 'MDB', 'APP',
    'COIN', 'MSTR', 'MARA', 'RIOT', 'HOOD', 'SOFI', 'AFRM', 'UPST', 'RBLX', 'U', 'RDDT', 'IONQ', 'RGTI', 'SMR', 'OKLO',
    'JPM', 'GS', 'MS', 'BAC', 'V', 'MA', 'LLY', 'UNH', 'XOM', 'CVX', 'CAT', 'BA', 'GE', 'COST', 'WMT',
    'SPY', 'QQQ', 'SMH', 'SOXX', 'IWM', 'DIA', 'TQQQ', 'SOXL', 'MUU', 'SNDU', 'NVDL', 'TSLL', 'AMDL', 'CONL', 'MSTU'
]

RADAR_DISCOVERIES = {}
RADAR_STATUS = {
    "is_running": True,
    "scan_cursor": 0,
    "universe_size": len(MARKET_RADAR_UNIVERSE),
    "last_scan_symbol": "待命啟動中",
    "last_update_time": "--",
    "discovered_count": 0
}

COMPANY_NAME_MAP = {
    '00700': '騰訊控股', '09988': '阿里巴巴', '03690': '美團', '01810': '小米集團',
    '00981': '中芯國際', '00005': '匯豐控股', '00388': '香港交易所', '01299': '友邦保險',
    '00883': '中國海洋石油', '00941': '中國移動', '01928': '金沙中國', '00027': '銀河娛樂',
    '02015': '理想汽車', '09868': '小鵬汽車', '09866': '蔚來', '00175': '吉利汽車',
    '01211': '比亞迪股份', '09618': '京東集團', '01024': '快手', '09999': '網易',
    '02318': '中國平安', '01398': '工商銀行', '03988': '中國銀行', '00939': '建設銀行',
    '06618': '京東健康', '00241': '阿里健康', '02800': '盈富基金', '02828': '恒生中國企業',
    'NVDA': 'NVIDIA', 'AAPL': 'Apple', 'TSLA': 'Tesla', 'MSFT': 'Microsoft',
    'AMZN': 'Amazon', 'META': 'Meta', 'GOOGL': 'Alphabet', 'AMD': 'AMD',
    'TSM': '台積電', 'AVGO': 'Broadcom', 'MU': 'Micron', 'SMCI': 'Super Micro',
    'PLTR': 'Palantir', 'COIN': 'Coinbase', 'MSTR': 'MicroStrategy', 'INTC': 'Intel',
    'QCOM': 'Qualcomm', 'ARM': 'ARM', 'CRWD': 'CrowdStrike', 'PANW': 'Palo Alto',
    'SNDK': 'SanDisk', 'SNDU': 'SanDisk 2X多', 'MUU': 'Micron 2X多',
    'SPY': '標普500 ETF', 'QQQ': '納指100 ETF', 'DIA': '道瓊 ETF', 'IWM': '羅素2000 ETF',
    'SMH': '半導體 ETF', 'SOXX': '半導體 ETF', 'TQQQ': '納指 3X多', 'SQQQ': '納指 3X空',
    'SOXL': '半導體 3X多', 'SOXS': '半導體 3X空', 'NVDL': 'NVDA 2X多', 'TSLL': 'TSLA 2X多'
}


def normalize_symbolInput(raw):
    s = (raw || '').strip().upper()
    if s.endswith('.HK'): s = s[:-3]
    if s.startswith('HK.') or s.startswith('US.'): s = s[3:]
    s = s.strip()
    if s.isdigit(): return s.zfill(5)
    return s


def get_company_name(sym):
    raw = normalize_symbolInput(sym)
    if raw in DYNAMIC_NAME_CACHE and DYNAMIC_NAME_CACHE[raw]:
        return DYNAMIC_NAME_CACHE[raw]
    if raw in COMPANY_NAME_MAP:
        return COMPANY_NAME_MAP[raw]
    return ""


def get_yf_symbol(sym):
    raw = normalize_symbolInput(sym)
    if raw.isdigit():
        return f"{int(raw):04d}.HK"
    return raw


def clean_yf_dataframe(yf_df):
    if yf_df is None or yf_df.empty:
        return pd.DataFrame()
    df = yf_df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    return df


def fetch_batch_accurate_quotes(raw_symbols):
    now_ts = time.time()
    quotes_out = {}
    missing_syms = []

    with cache_lock:
        for s in raw_symbols:
            cached = DAILY_QUOTE_CACHE.get(s)
            if cached and (now_ts - cached['ts'] < QUOTE_TTL_SECONDS) and cached['data'].get('price', 0) > 0:
                quotes_out[s] = cached['data'].copy()
            else:
                missing_syms.append(s)

    if not missing_syms:
        return quotes_out

    # 第一保底：嘗試透過 OpenD 獲取快照
    if USE_FUTU and quote_ctx:
        try:
            futu_syms = [f"HK.{s}" if s.isdigit() else f"US.{s}" for s in missing_syms]
            code_to_raw = {f"HK.{s}" if s.isdigit() else f"US.{s}": s for s in missing_syms}
            with futu_lock:
                ret, snap_data = quote_ctx.get_market_snapshot(futu_syms)
            if ret == RET_OK and not snap_data.empty:
                for _, row in snap_data.iterrows():
                    sym = code_to_raw.get(row.get('code'))
                    if not sym: continue
                    fn = str(row.get('name', '')).strip()
                    if fn and fn != 'nan': DYNAMIC_NAME_CACHE[sym] = fn
                    lp, lc = float(row.get('last_price', 0)), float(row.get('last_close', row.get('prev_close_price', 0)))
                    if lp > 0 and lc > 0:
                        cv, cp = round(lp - lc, 2), round((lp - lc) / lc * 100, 2)
                        qd = {"price": round(lp, 2), "prev_close": round(lc, 2), "change": cv, "change_pct": cp}
                        quotes_out[sym] = qd
                        with cache_lock: DAILY_QUOTE_CACHE[sym] = {"ts": now_ts, "data": qd}
        except Exception as e:
            print(f"[SYS] OpenD snapshot warning, falling back to yfinance: {e}")

    # 第二保底：透過 Yahoo Finance 批次獲取
    still_missing = [s for s in missing_syms if s not in quotes_out]
    if still_missing:
        try:
            yf_tickers = [get_yf_symbol(s) for s in still_missing]
            yf_data = yf.download(yf_tickers, period="5d", interval="1d", group_by='ticker', threads=True, progress=False)
            for s in still_missing:
                yt = get_yf_symbol(s)
                df_s = pd.DataFrame()
                if len(still_missing) == 1:
                    df_s = clean_yf_dataframe(yf_data)
                elif yt in yf_data.columns.levels[0]:
                    df_s = clean_yf_dataframe(yf_data[yt])

                if not df_s.empty and 'Close' in df_s.columns:
                    closes = df_s['Close'].dropna().astype(float).tolist()
                    if len(closes) >= 2:
                        lp, lc = closes[-1], closes[-2]
                        cv, cp = round(lp - lc, 2), round((lp - lc) / lc * 100, 2) if lc > 0 else 0.0
                        qd = {"price": round(lp, 2), "prev_close": round(lc, 2), "change": cv, "change_pct": cp}
                        quotes_out[s] = qd
                        with cache_lock: DAILY_QUOTE_CACHE[s] = {"ts": now_ts, "data": qd}
                    elif len(closes) == 1:
                        lp = closes[0]
                        qd = {"price": round(lp, 2), "prev_close": round(lp, 2), "change": 0.0, "change_pct": 0.0}
                        quotes_out[s] = qd
                        with cache_lock: DAILY_QUOTE_CACHE[s] = {"ts": now_ts, "data": qd}
        except Exception as e:
            print(f"[SYS] yfinance batch quote error: {e}")

    return quotes_out


def compute_order_flow_delta(df):
    if df.empty: return df
    deltas, prev_close = [], None
    for _, row in df.iterrows():
        o, h, l, c, v = float(row['open']), float(row['high']), float(row['low']), float(row['close']), float(row.get('volume', 0))
        if v <= 0 or math.isnan(v):
            net_delta = 0.0
        elif h > l:
            pr = ((c - l) - (h - c)) / (h - l)
            bb = 0.18 if c > o else (-0.18 if c < o else 0.0)
            net_delta = v * max(-1.0, min(1.0, pr * 0.82 + bb))
        else:
            net_delta = v if (prev_close and c > prev_close) else (-v if (prev_close and c < prev_close) else 0.0)
        prev_close = c
        deltas.append(round(net_delta, 0))
    df = df.copy()
    df['delta'] = deltas
    ad = df['delta'].abs()
    ra = ad.rolling(window=20, min_periods=3).mean().fillna(ad.mean() if len(ad) > 0 else 0.0)
    df['is_whale'] = (ad > (ra * 1.8)) & (ad > 0)
    return df


def fetch_standardized_kline(raw_sym, interval, max_bars=1000):
    data = pd.DataFrame()
    futu_sym = f"HK.{raw_sym}" if raw_sym.isdigit() else f"US.{raw_sym}"
    kl_map = {'15m': (KLType.K_15M if USE_FUTU else '15m'), '60m': (KLType.K_60M if USE_FUTU else '60m'), '1h': (KLType.K_60M if USE_FUTU else '60m'), '4h': (KLType.K_60M if USE_FUTU else '60m'), '1d': (KLType.K_DAY if USE_FUTU else '1d')}
    
    # 嘗試保底 1：OpenD
    if USE_FUTU and quote_ctx:
        try:
            now = datetime.now()
            start = (now - timedelta(days=20 if interval == '15m' else 90)).strftime('%Y-%m-%d')
            with futu_lock:
                ret, f_data, _ = quote_ctx.request_history_kline(futu_sym, start=start, end=(now + timedelta(days=2)).strftime('%Y-%m-%d'), ktype=kl_map[interval], max_count=max_bars, autype=AuType.QFQ)
            if ret == RET_OK and not f_data.empty:
                data = f_data[['time_key', 'open', 'high', 'low', 'close', 'volume']].copy()
                data['time_key'] = pd.to_datetime(data['time_key'])
        except Exception:
            pass

    # 嘗試保底 2：Yahoo Finance
    if data.empty:
        try:
            yf_int = '15m' if interval == '15m' else ('60m' if interval in ['1h', '60m', '4h'] else '1d')
            yf_per = '1mo' if interval == '15m' else ('3mo' if interval in ['1h', '60m', '4h'] else '2y')
            tk = yf.Ticker(get_yf_symbol(raw_sym))
            yf_df = clean_yf_dataframe(tk.history(period=yf_per, interval=yf_int))
            if not yf_df.empty and 'Close' in yf_df.columns:
                if yf_df.index.tz is not None:
                    yf_df.index = yf_df.index.tz_convert('Asia/Hong_Kong' if raw_sym.isdigit() else 'America/New_York').tz_localize(None)
                yf_df = yf_df.reset_index()
                tc = 'Datetime' if 'Datetime' in yf_df.columns else ('Date' if 'Date' in yf_df.columns else yf_df.columns[0])
                data = pd.DataFrame({
                    'time_key': pd.to_datetime(yf_df[tc]),
                    'open': yf_df['Open'].astype(float), 'high': yf_df['High'].astype(float),
                    'low': yf_df['Low'].astype(float), 'close': yf_df['Close'].astype(float),
                    'volume': yf_df['Volume'].fillna(0).astype(float)
                })
        except Exception as e:
            print(f"[SYS] yfinance kline error: {e}")

    if data.empty: return pd.DataFrame()
    data = data.drop_duplicates(subset=['time_key'], keep='last').sort_values('time_key')

    if interval == '4h':
        data = data.set_index('time_key')
        data = data.resample('4h', closed='left', label='left').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna(subset=['open', 'close']).reset_index()

    return compute_order_flow_delta(data)


def get_or_compute_4h_macro(raw_sym):
    now_ts = time.time()
    with cache_lock:
        cached = MACRO_4H_CACHE.get(raw_sym)
        if cached and (now_ts - cached['ts'] < CACHE_TTL_SECONDS):
            return cached['data']

    df_4h = fetch_standardized_kline(raw_sym, '4h', max_bars=450)
    if df_4h.empty: return None

    mw = df_4h.tail(60) if len(df_4h) >= 60 else df_4h
    hw, lw = float(mw['high'].max()), float(mw['low'].min())
    eq = (hw + lw) / 2.0
    rw = df_4h.tail(35) if len(df_4h) >= 35 else df_4h
    eb, et = float(rw['low'].quantile(0.14)), float(rw['low'].quantile(0.32))
    if eb >= et: et = eb * 1.015
    sl = round(eb * 0.985, 2)

    macro_data = {"high_val": hw, "low_val": lw, "macro_eq": round(eq, 2), "entry_bot": round(eb, 2), "entry_top": round(et, 2), "sl_price": sl, "df_4h": df_4h}
    with cache_lock:
        MACRO_4H_CACHE[raw_sym] = {"ts": now_ts, "data": macro_data}
    return macro_data


def evaluate_three_in_one(active_df, current_price, macro_info=None):
    if macro_info:
        hw, eq, eb, et, sl = macro_info['high_val'], macro_info['macro_eq'], macro_info['entry_bot'], macro_info['entry_top'], macro_info['sl_price']
    else:
        mw = active_df.tail(60) if len(active_df) >= 60 else active_df
        hw, lw = float(mw['high'].max()), float(mw['low'].min())
        eq = round((hw + lw) / 2.0, 2)
        rw = active_df.tail(35) if len(active_df) >= 35 else active_df
        eb, et = round(float(rw['low'].quantile(0.14)), 2), round(float(rw['low'].quantile(0.32)), 2)
        if eb >= et: et = eb * 1.015
        sl = round(eb * 0.985, 2)

    tp = round(max(hw * 0.99, current_price * 1.06), 2)
    risk, reward = max(0.05, abs(current_price - sl)), max(0.1, abs(tp - current_price))
    rrr = round(min(6.5, max(1.8, reward / risk)), 1)

    ld = float(active_df['delta'].iloc[-1]) if 'delta' in active_df.columns and len(active_df) > 0 else 0.0
    pd_val = float(active_df['delta'].iloc[-2]) if 'delta' in active_df.columns and len(active_df) > 1 else 0.0
    iw = bool(active_df['is_whale'].iloc[-1]) if 'is_whale' in active_df.columns and len(active_df) > 0 else False

    disc = current_price <= eq
    broken = current_price < sl
    in_ob = (not broken) and (current_price <= (et * 1.015))

    if broken:
        txt, wh, whn, ts, slvl, d = "[跌破防守止損]", "已跌穿 4H OB 防守底線", "空頭賣壓貫穿防線", "[觀察中] 結構跌破", "waiting", "short"
    elif in_ob and (ld > 0 or pd_val > 0):
        txt, wh, whn, ts, slvl, d = "[機構承接進場]", "落入 4H 機構需求區 (OB)", "正向 Delta 爆量承接" if iw else "Delta 買盤翻正", "[可執行] 機構承接", "executable", "long"
    elif disc and ld > 0:
        txt, wh, whn, ts, slvl, d = "[誘空反彈]", "處於 0.5 折價區", "低檔買盤介入", "[可執行] 折價區做多", "executable", "long"
    elif disc and ld <= 0:
        txt, wh, whn, ts, slvl, d = "[獵殺啟動]", "逼近 4H 訂單塊支撐", "流動性掃損中", "[接近中] 戰術埋伏", "approaching", "long"
    elif not disc and ld < 0 and current_price >= (hw * 0.96):
        txt, wh, whn, ts, slvl, d = "[嚴禁追高]", "處於極端溢價區", "負向 Delta 派發", "[觀察中] 嚴禁追高", "waiting", "short"
    else:
        txt, wh, whn, ts, slvl, d = "[衝高獲利]", "高於 0.5 均衡線", "動能放緩", "[觀察中] 靜待回踩", "waiting", "short"

    return {"macro_eq": eq, "entry_bot": eb, "entry_top": et, "sl_price": sl, "tp_price": tp, "rrr": rrr, "net_delta": ld, "is_whale": iw, "smc_text": txt, "where_status": wh, "when_status": whn, "trade_status": ts, "status_level": slvl, "direction": d}


@app.route('/api/quote')
def quote():
    symbols_param = request.args.get('symbols', '')
    interval = request.args.get('interval', '4h').lower().strip()
    need_chart = request.args.get('chart', '0') == '1'
    need_metrics = request.args.get('metrics', '1') == '1'

    input_keys = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
    raw_symbols = [normalize_symbolInput(k) for k in input_keys]
    raw_symbols = list(set([s for s in raw_symbols if s]))

    if not raw_symbols: return jsonify({})

    accurate_quotes = fetch_batch_accurate_quotes(raw_symbols)
    result = {s: accurate_quotes.get(s, {}).copy() for s in raw_symbols}

    for sym in raw_symbols:
        try:
            macro_info = get_or_compute_4h_macro(sym)
            data = macro_info['df_4h'] if (macro_info and not macro_info['df_4h'].empty and (not need_chart or interval == '4h')) else fetch_standardized_kline(sym, interval, max_bars=1200)

            if not data.empty:
                if sym not in result or result[sym].get("price", 0) <= 0:
                    lp = float(data.iloc[-1]['close'])
                    result[sym] = {"price": round(lp, 2), "prev_close": round(lp, 2), "change": 0.0, "change_pct": 0.0}

                cp = result[sym].get("price", float(data.iloc[-1]['close']))
                tio = evaluate_three_in_one(data, cp, macro_info=macro_info)
                result[sym].update(tio)
                result[sym]["radar_hit"] = False

                if need_chart:
                    dedup = {}
                    for _, row in data.iterrows():
                        try:
                            t = int(row['time_key'].timestamp())
                            c = float(row['close'])
                            if math.isnan(c) or c <= 0: continue
                            dedup[t] = {
                                "time": t, "open": round(float(row['open']), 2), "high": round(float(row['high']), 2),
                                "low": round(float(row['low']), 2), "close": round(c, 2),
                                "volume": round(float(row.get('volume', 0)), 0),
                                "delta": round(float(row.get('delta', 0)), 0), "whale": bool(row.get('is_whale', False))
                            }
                        except Exception: pass
                    sc = [dedup[k] for k in sorted(dedup.keys())]
                    if sc and cp > 0:
                        sc[-1]["close"] = round(cp, 2)
                        sc[-1]["high"] = round(max(sc[-1]["high"], cp), 2)
                        sc[-1]["low"] = round(min(sc[-1]["low"], cp), 2)
                    result[sym]["chart"] = sc
            else:
                if need_chart: result[sym]["chart"] = []
        except Exception as e:
            print(f"[SYS] Quote processing error for {sym}: {e}")
            if need_chart: result[sym]["chart"] = []

        if "price" not in result[sym]:
            result[sym].update({"price": 0.0, "prev_close": 0.0, "change": 0.0, "change_pct": 0.0})

    for s in list(result.keys()):
        result[s]['symbol'] = s
        result[s]['company_name'] = get_company_name(s)

    return jsonify(result)


@app.route('/api/radar')
def radar():
    return jsonify({"status": RADAR_STATUS, "discoveries": RADAR_DISCOVERIES})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"[SYS] Cloud Pro 雙保底數據引擎已啟動 (Port {port})...")
    app.run(host='0.0.0.0', port=port, debug=False)
