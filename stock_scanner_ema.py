import streamlit as st
import streamlit.components.v1
from tradingview_screener import Query, col
import pandas as pd
import yfinance as yf
import requests
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="주식 스캐너 EMA", page_icon="📊", layout="wide")

st.title("📊 한국 주식 종목 검색기 — EMA 밴드 스캐너")
st.markdown("""
**검색 조건 (일봉 기준)**
- 📉 **EMA48 < SMA224 < SMA448** — 장기 하락 배열 구간 (저점 매집 구간)
- 🎯 **EMA45 < 종가 < EMA52** — 단기 EMA 밴드 안에 종가 위치
- 💰 **주가 범위** 조건 유지
- 🚫 ETF · 스팩 · 우선주 · 거래정지 · 투자경고 · 관리종목 · 환기종목 자동 제외
""")

# ── 사이드바 설정 ────────────────────────────────────────────────
st.sidebar.header("🔍 검색 설정")

st.sidebar.markdown("💰 **주가 범위 (원)**")
min_price = st.sidebar.number_input("최소 금액", value=2000, step=500, min_value=0)
max_price = st.sidebar.number_input("최대 금액", value=30000, step=1000, min_value=0)

min_vol = st.sidebar.number_input(
    "📦 최소 거래량",
    value=50000, step=10000,
    help="하루 거래량 최소 기준"
)

max_workers = st.sidebar.slider(
    "⚡ 병렬 처리 수 (workers)",
    min_value=5, max_value=30, value=15, step=5,
    help="동시에 검증할 종목 수. 높을수록 빠르지만 네트워크 부하 증가."
)

st.sidebar.divider()
st.sidebar.markdown("**📐 이동평균 파라미터**")
ema_short = st.sidebar.number_input("단기 EMA 하한 (종가 >)", value=45, min_value=1)
ema_long  = st.sidebar.number_input("단기 EMA 상한 (종가 <)", value=52, min_value=1)
ema_mid   = st.sidebar.number_input("중기 EMA (EMA48 기준)", value=48, min_value=1)
sma_mid   = st.sidebar.number_input("중기 SMA (SMA224 기준)", value=224, min_value=1)
sma_long  = st.sidebar.number_input("장기 SMA (SMA448 기준)", value=448, min_value=1)


# ── KRX 종목 정보 + 제재종목 로딩 ──────────────────────────────────
@st.cache_data(ttl=3600)
def load_krx_data():
    """
    KRX 공식 데이터포털 API로 종목 정보 로딩.
    반환: (name_map, exclude_set, sanction_codes)
      - name_map      : {코드6자리: 종목명}
      - exclude_set   : ETF·스팩·우선주 등 제외 코드 집합
      - sanction_codes: 거래정지·투자경고·관리종목·환기 코드 집합
    """
    name_map       = {}
    exclude_set    = set()
    sanction_codes = set()

    # ── 1) 기본 종목 목록 ─────────────────────────────────────────
    try:
        url = "https://kind.krx.co.kr/corpgeneral/corpList.do"
        params  = {"method": "download", "searchType": "13"}
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.encoding = 'euc-kr'
        df = pd.read_html(io.StringIO(response.text))[0]
        df.columns = df.columns.str.strip()

        code_col = next((c for c in df.columns if '종목코드' in c or '코드' in c), None)
        name_col = next((c for c in df.columns if '회사명' in c or '종목명' in c or '기업명' in c), None)

        if code_col and name_col:
            df[code_col] = df[code_col].astype(str).str.zfill(6)
            name_map = dict(zip(df[code_col], df[name_col]))

            for _, row in df.iterrows():
                code = str(row[code_col]).zfill(6)
                name = str(row[name_col])

                # 우선주 제외 (코드 끝자리가 0이 아님)
                if not code.endswith('0'):
                    exclude_set.add(code)
                    continue

                exclude_keywords = ['스팩', 'SPAC', '리츠', 'REIT', '인프라', '환기',
                                     '수익증권', 'ETF', 'ETN', 'ELW']
                if any(kw in name.upper() for kw in exclude_keywords):
                    exclude_set.add(code)

    except Exception as e:
        st.warning(f"KRX 종목 목록 로딩 실패 ({e}). 이름 없이 진행합니다.")

    # ── 2) 투자유의·제재 종목 ─────────────────────────────────────
    sanction_urls = [
        {   # 관리종목
            "url": "https://kind.krx.co.kr/investwarning/managementissue.do",
            "params": {"method": "searchManagementIssueSub", "marketType": "0"},
        },
        {   # 투자경고
            "url": "https://kind.krx.co.kr/investwarning/investwarning.do",
            "params": {"method": "searchInvestWarningSub", "marketType": "0"},
        },
        {   # 거래정지
            "url": "https://kind.krx.co.kr/investwarning/tradesuspend.do",
            "params": {"method": "searchTradeSuspendSub", "marketType": "0"},
        },
        {   # 불성실공시
            "url": "https://kind.krx.co.kr/investwarning/unfaithfuldisclosure.do",
            "params": {"method": "searchUnfaithfulDisclosureSub", "marketType": "0"},
        },
    ]

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"}
    for item in sanction_urls:
        try:
            resp = requests.get(item["url"], params=item["params"],
                                headers=headers, timeout=10)
            resp.encoding = 'euc-kr'
            tables = pd.read_html(io.StringIO(resp.text))
            if not tables:
                continue
            tbl = tables[0]
            tbl.columns = tbl.columns.str.strip()

            code_col = next(
                (c for c in tbl.columns if '종목코드' in c or '단축코드' in c or '코드' in c),
                None
            )
            if code_col is None:
                name_col2 = next((c for c in tbl.columns if '종목명' in c or '회사명' in c), None)
                if name_col2:
                    rev_map = {v: k for k, v in name_map.items()}
                    for nm in tbl[name_col2].dropna():
                        cd = rev_map.get(str(nm).strip())
                        if cd:
                            sanction_codes.add(cd)
                continue

            tbl[code_col] = tbl[code_col].astype(str).str.zfill(6)
            for cd in tbl[code_col]:
                sanction_codes.add(cd)

        except Exception:
            continue

    return name_map, exclude_set, sanction_codes


# ── 재무 데이터 (영업이익 · 부채비율) ─────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_financial_history(code_6: str):
    for suffix in ['.KS', '.KQ']:
        ticker_str = f"{code_6}{suffix}"
        try:
            tk = yf.Ticker(ticker_str)

            inc = tk.quarterly_income_stmt
            op_series = pd.Series(dtype=float)
            if inc is not None and not inc.empty:
                for label in ['Operating Income', 'EBIT', 'Operating Revenue']:
                    if label in inc.index:
                        raw = inc.loc[label].dropna()
                        if not raw.empty:
                            raw.index = pd.to_datetime(raw.index)
                            raw = raw.sort_index()
                            op_series = raw.tail(6) / 1e8
                            op_series.index = [d.strftime('%Y.%m') for d in op_series.index]
                            break

            bal = tk.quarterly_balance_sheet
            debt_series = pd.Series(dtype=float)
            if bal is not None and not bal.empty:
                total_liab = None
                equity     = None
                for label in ['Total Liabilities Net Minority Interest', 'Total Liabilities']:
                    if label in bal.index:
                        total_liab = bal.loc[label].dropna()
                        break
                for label in ['Stockholders Equity', 'Total Equity Gross Minority Interest',
                               'Common Stock Equity']:
                    if label in bal.index:
                        equity = bal.loc[label].dropna()
                        break
                if total_liab is not None and equity is not None:
                    total_liab.index = pd.to_datetime(total_liab.index)
                    equity.index     = pd.to_datetime(equity.index)
                    common_idx = total_liab.index.intersection(equity.index).sort_values()
                    if len(common_idx) > 0:
                        ratio = (total_liab[common_idx] / equity[common_idx] * 100).dropna()
                        ratio = ratio.tail(6)
                        ratio.index = [d.strftime('%Y.%m') for d in ratio.index]
                        debt_series = ratio

            if not op_series.empty or not debt_series.empty:
                return op_series, debt_series

        except Exception:
            continue

    return pd.Series(dtype=float), pd.Series(dtype=float)


# ── 재무 그래프 렌더링 ────────────────────────────────────────────
def render_financial_chart(name: str, code: str, op_series: pd.Series, debt_series: pd.Series):
    has_op   = not op_series.empty
    has_debt = not debt_series.empty

    if not has_op and not has_debt:
        st.warning(f"{name} — 재무 데이터를 가져올 수 없습니다.")
        return

    if has_op and has_debt:
        all_idx = sorted(set(op_series.index) | set(debt_series.index))
    elif has_op:
        all_idx = list(op_series.index)
    else:
        all_idx = list(debt_series.index)

    quarters  = all_idx
    op_vals   = [round(float(op_series[q]),  1) if (has_op   and q in op_series.index)   else None for q in quarters]
    debt_vals = [round(float(debt_series[q]),1) if (has_debt and q in debt_series.index) else None for q in quarters]
    op_colors = ['#c0392b' if (v is not None and v < 0) else '#3a9e5f' for v in op_vals]

    chart_id = f"chart_{code}"
    html = f"""
<div style="background:linear-gradient(160deg,#d4edda 0%,#e8f5e9 40%,#f0faf1 100%);
            border-radius:12px;padding:24px 28px 20px;font-family:'Malgun Gothic',sans-serif;
            position:relative;overflow:hidden;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
    <div style="width:13px;height:13px;background:#2d6a3f;border-radius:2px;"></div>
    <span style="font-size:15px;font-weight:700;color:#1a3a24;">{name} ({code}) — 분기별 재무 추이</span>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:700;margin-bottom:2px;padding:0 4px;">
    <span style="color:#2d7a4a;">억원</span>
    <span style="color:#b05010;">%</span>
  </div>
  <div style="position:relative;height:320px;">
    <canvas id="{chart_id}"></canvas>
  </div>
  <div style="display:flex;justify-content:center;gap:24px;margin-top:12px;font-size:12px;color:#444;">
    <span style="display:flex;align-items:center;gap:5px;">
      <span style="width:14px;height:11px;background:#3a9e5f;border-radius:2px;display:inline-block;"></span>영업이익 (억원)
    </span>
    <span style="display:flex;align-items:center;gap:5px;">
      <span style="width:14px;height:11px;background:#e07010;border-radius:2px;display:inline-block;"></span>부채비율 (%)
    </span>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
(function() {{
  const quarters  = {json.dumps(quarters)};
  const opVals    = {json.dumps(op_vals)};
  const debtVals  = {json.dumps(debt_vals)};
  const opColors  = {json.dumps(op_colors)};
  const ctx = document.getElementById('{chart_id}');
  if (!ctx) return;
  new Chart(ctx, {{
    data: {{
      labels: quarters,
      datasets: [
        {{ type:'bar', label:'영업이익', data:opVals, backgroundColor:opColors,
           borderRadius:4, borderSkipped:false, borderWidth:0, yAxisID:'yLeft', order:1 }},
        {{ type:'bar', label:'부채비율', data:debtVals, backgroundColor:'#e07010',
           borderRadius:4, borderSkipped:'bottom', borderWidth:0, yAxisID:'yRight', order:2 }}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins: {{
        legend: {{ display:false }},
        tooltip: {{
          mode:'index', intersect:false,
          callbacks: {{
            label: c => c.datasetIndex===0
              ? '영업이익: '+(c.raw!==null?c.raw.toLocaleString()+'억원':'-')
              : '부채비율: '+(c.raw!==null?c.raw.toFixed(1)+'%':'-')
          }}
        }}
      }},
      scales: {{
        x: {{ grid:{{display:false}}, ticks:{{color:'#fff',font:{{size:11,weight:'600'}},maxRotation:0,autoSkip:false,padding:4}}, border:{{display:false}} }},
        yLeft: {{ type:'linear', position:'left',
          ticks:{{color:'#2d7a4a',font:{{size:11}},callback:v=>v.toLocaleString()}},
          grid:{{color:c=>c.tick.value===0?'#000000':'rgba(180,200,180,0.35)',lineWidth:c=>c.tick.value===0?2:1}},
          border:{{display:false}} }},
        yRight: {{ type:'linear', position:'right', min:0,
          ticks:{{color:'#b05010',font:{{size:11}},callback:v=>v+'%'}},
          grid:{{display:false}}, border:{{display:false}} }}
      }},
      layout:{{padding:{{top:24,bottom:0}}}}
    }},
    plugins: [{{
      id:'customDraw_{code}',
      afterDatasetsDraw(chart) {{
        const ctx=chart.ctx, meta0=chart.getDatasetMeta(0), meta1=chart.getDatasetMeta(1);
        const yLeft=chart.scales.yLeft;
        ctx.save();
        const zeroY=yLeft.getPixelForValue(0);
        ctx.beginPath(); ctx.moveTo(chart.chartArea.left,zeroY); ctx.lineTo(chart.chartArea.right,zeroY);
        ctx.strokeStyle='#000'; ctx.lineWidth=2; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(chart.chartArea.left,chart.chartArea.top); ctx.lineTo(chart.chartArea.left,chart.chartArea.bottom);
        ctx.strokeStyle='#000'; ctx.lineWidth=1.5; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(chart.chartArea.right,chart.chartArea.top); ctx.lineTo(chart.chartArea.right,chart.chartArea.bottom);
        ctx.strokeStyle='#000'; ctx.lineWidth=1.5; ctx.stroke();
        ctx.font="bold 10px 'Malgun Gothic',sans-serif"; ctx.textAlign='center';
        opVals.forEach((val,i)=>{{ if(val===null)return; const el=meta0.data[i];
          ctx.fillStyle=val<0?'#8a1a10':'#1a5c30';
          ctx.fillText(val.toLocaleString(),el.x,val<0?el.y+14:el.y-7); }});
        debtVals.forEach((val,i)=>{{ if(val===null)return; const el=meta1.data[i];
          ctx.fillStyle='#8a3d00'; ctx.fillText(val.toFixed(1)+'%',el.x,el.y-7); }});
        const xScale=chart.scales.x, yBottom=chart.chartArea.bottom;
        ctx.fillStyle='#555'; ctx.fillRect(chart.chartArea.left,yBottom,chart.chartArea.width,28);
        ctx.font="bold 11px 'Malgun Gothic',sans-serif"; ctx.fillStyle='#fff';
        quarters.forEach((q,i)=>{{ ctx.fillText(q,xScale.getPixelForValue(i),yBottom+19); }});
        ctx.restore();
      }}
    }}]
  }});
}})();
</script>
"""
    st.components.v1.html(html, height=430, scrolling=False)


# ── TradingView 전체 종목 수집 (페이지네이션) ──────────────────────
def run_tv_scanner_full():
    all_rows = []
    offset   = 0
    batch    = 1500

    while True:
        try:
            result = (
                Query()
                .set_markets("korea")
                .select('name', 'close', 'volume', 'change', 'SMA200', 'price_52_week_high')
                .where(col('type') == 'stock')
                .offset(offset)
                .limit(batch)
                .get_scanner_data()
            )
            if result is None:
                break
            count, data = result
            if data is None or data.empty:
                break
            all_rows.append(data)
            fetched = len(data)
            if fetched < batch:
                break
            offset += fetched
            time.sleep(0.5)
        except TypeError:
            break
        except Exception as e:
            st.warning(f"TradingView 수집 중단 (offset={offset}): {e}")
            break

    if not all_rows:
        return pd.DataFrame()
    return pd.concat(all_rows, ignore_index=True)


# ── 주가·거래량 1차 필터 ─────────────────────────────────────────
def apply_price_volume_filter(data: pd.DataFrame, min_price, max_price, min_vol) -> pd.DataFrame:
    mask = (
        (data['close'] >= min_price) &
        (data['close'] <= max_price) &
        (data['volume'] > min_vol)
    )
    return data[mask].copy()


# ── 일봉 EMA/SMA 조건 검증 (단일 종목) ──────────────────────────
def check_ema_conditions(code_6, p_ema_short, p_ema_long, p_ema_mid, p_sma_mid, p_sma_long):
    """
    일봉 기준 조건 검증:
      1) EMA{p_ema_mid} < SMA{p_sma_mid} < SMA{p_sma_long}  (장기 하락 배열)
      2) EMA{p_ema_short} < 종가 < EMA{p_ema_long}           (단기 EMA 밴드 내 위치)
    반환: (pass_all, close, ema_short_val, ema_long_val, ema_mid_val, sma_mid_val, sma_long_val)
    """
    # SMA448 계산에 충분한 데이터 확보 (448 + 여유 50일)
    need_days = p_sma_long + 50

    for suffix in ['.KS', '.KQ']:
        ticker = f"{code_6}{suffix}"
        try:
            df = yf.download(
                ticker,
                period=f"{need_days + 100}d",
                interval="1d",
                auto_adjust=True,
                progress=False
            )
            if df is None or len(df) < need_days:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna(subset=['Close'])
            if len(df) < need_days:
                continue

            close = df['Close']

            # EMA 계산
            ema_s   = float(close.ewm(span=p_ema_short, adjust=False).mean().iloc[-1])  # EMA45
            ema_l   = float(close.ewm(span=p_ema_long,  adjust=False).mean().iloc[-1])  # EMA52
            ema_mid = float(close.ewm(span=p_ema_mid,   adjust=False).mean().iloc[-1])  # EMA48

            # SMA 계산
            sma_mid  = float(close.rolling(p_sma_mid).mean().iloc[-1])   # SMA224
            sma_long = float(close.rolling(p_sma_long).mean().iloc[-1])  # SMA448

            curr_close = float(close.iloc[-1])

            # 조건 1: EMA48 < SMA224 < SMA448
            cond_order = (ema_mid < sma_mid) and (sma_mid < sma_long)

            # 조건 2: EMA45 < 종가 < EMA52
            cond_band  = (ema_s < curr_close) and (curr_close < ema_l)

            pass_all = cond_order and cond_band

            return pass_all, curr_close, ema_s, ema_l, ema_mid, sma_mid, sma_long

        except Exception:
            continue

    return False, None, None, None, None, None, None


# ── 병렬 검증 래퍼 ───────────────────────────────────────────────
def check_one(row_tuple, p_ema_short, p_ema_long, p_ema_mid, p_sma_mid, p_sma_long):
    idx, row = row_tuple
    code   = row['종목코드']
    result = check_ema_conditions(code, p_ema_short, p_ema_long, p_ema_mid, p_sma_mid, p_sma_long)
    return idx, row, result


# ── TradingView 차트 URL ─────────────────────────────────────────
def get_chart_url(ticker_raw):
    symbol = ticker_raw if ":" in str(ticker_raw) else f"KRX:{ticker_raw}"
    return f"https://www.tradingview.com/chart/?symbol={symbol}"


# ── 메인 실행 ────────────────────────────────────────────────────
if st.button("🔍 종목 검색 시작", use_container_width=True):
    if min_price >= max_price:
        st.error("⚠️ 최소 금액이 최대 금액보다 작아야 합니다.")
    else:
        # STEP 1: KRX 종목 정보 + 제재종목 로딩
        with st.spinner("📋 KRX 종목 정보 및 제재종목 로딩 중..."):
            name_map, exclude_set, sanction_codes = load_krx_data()

        all_excluded = exclude_set | sanction_codes
        st.info(
            f"🚫 사전 제외 목록: ETF·스팩·우선주 {len(exclude_set)}개 + "
            f"제재종목(거래정지·경고·관리·환기 등) {len(sanction_codes)}개 = 총 {len(all_excluded)}개"
        )

        # STEP 2: TradingView 전체 수집
        with st.spinner("🔍 TradingView 전체 종목 수집 중 (최대 1500개+)..."):
            data = run_tv_scanner_full()

        if data is None or data.empty:
            st.warning("⚠️ TradingView에서 종목을 가져오지 못했습니다.")
        else:
            total_tv = len(data)

            # STEP 3: 종목코드 추출
            data['종목코드'] = (
                data['name']
                .apply(lambda x: str(x).split(':')[-1])
                .str.zfill(6)
            )

            # STEP 4: 주가·거래량 필터
            data = apply_price_volume_filter(data, min_price, max_price, min_vol)
            after_price = len(data)

            # STEP 5: 제재종목 + ETF·스팩 제외
            data = data[~data['종목코드'].isin(all_excluded)]
            after_sanction = len(data)

            # STEP 6: 종목명 매핑 + ETF 패턴 추가 제거
            data['종목명'] = data['종목코드'].map(name_map)
            data['종목명'] = data.apply(
                lambda r: name_map.get(str(r['name']).split(':')[-1].zfill(6),
                                       str(r['name']).split(':')[-1])
                if pd.isna(r['종목명']) else r['종목명'], axis=1
            )
            etf_pattern = r'ETF|ETN|KODEX|TIGER|RISE|ACE|KBSTAR|HANARO|ARIRANG|SOL|KOSEF'
            data = data[data['종목명'].notna()]
            data = data[~data['종목명'].str.contains(etf_pattern, case=False, na=False)]
            after_etf = len(data)

            st.info(
                f"📊 수집: {total_tv}개 "
                f"→ 주가·거래량 필터: {after_price}개 "
                f"→ 제재·ETF 제외: {after_sanction}개 "
                f"→ ETF패턴 추가제거: {after_etf}개 "
                f"→ **일봉 EMA/SMA 조건 검증 시작** (병렬 {max_workers}workers)"
            )

            # STEP 7: 병렬 일봉 EMA/SMA 검증
            progress_bar = st.progress(0)
            status_text  = st.empty()
            results      = []
            total        = len(data)
            done_count   = 0
            rows_list    = list(data.iterrows())

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        check_one, row_tuple,
                        ema_short, ema_long, ema_mid, sma_mid, sma_long
                    ): row_tuple
                    for row_tuple in rows_list
                }

                for future in as_completed(futures):
                    done_count += 1
                    progress_bar.progress(done_count / total)

                    try:
                        idx, row, (pass_all, curr_close,
                                   ema_s_val, ema_l_val, ema_mid_val,
                                   sma_mid_val, sma_long_val) = future.result()
                    except Exception:
                        status_text.text(f"⚡ [{done_count}/{total}] 검증 중...")
                        continue

                    code = row['종목코드']
                    name = row['종목명']
                    status_text.text(f"⚡ [{done_count}/{total}] {name}({code}) 검증 완료")

                    if pass_all:
                        results.append({
                            '종목명':          name,
                            '종목코드':        code,
                            '현재가(원)':      row['close'],
                            '거래량':          row['volume'],
                            '등락률(%)':       row['change'],
                            f'EMA{ema_short}': round(ema_s_val,   0) if ema_s_val   else None,
                            f'EMA{ema_long}':  round(ema_l_val,   0) if ema_l_val   else None,
                            f'EMA{ema_mid}':   round(ema_mid_val, 0) if ema_mid_val else None,
                            f'SMA{sma_mid}':   round(sma_mid_val, 0) if sma_mid_val else None,
                            f'SMA{sma_long}':  round(sma_long_val,0) if sma_long_val else None,
                            '52주 신고가':     row.get('price_52_week_high', None),
                            'name_raw':        row['name'],
                        })

            progress_bar.empty()
            status_text.empty()

            # STEP 8: 결과 출력
            if not results:
                st.warning("⚠️ 모든 조건을 만족하는 종목이 없습니다. 조건을 완화해 보세요.")
            else:
                st.success(f"✅ 최종 {len(results)}개 종목 발견!")
                result_df = pd.DataFrame(results)

                display_cols = [
                    '종목명', '종목코드', '현재가(원)', '거래량', '등락률(%)',
                    f'EMA{ema_short}', f'EMA{ema_long}', f'EMA{ema_mid}',
                    f'SMA{sma_mid}', f'SMA{sma_long}', '52주 신고가'
                ]
                display_cols = [c for c in display_cols if c in result_df.columns]
                display = result_df[display_cols].copy()

                fmt = {
                    '현재가(원)':         '{:,.0f}',
                    '거래량':             '{:,.0f}',
                    '등락률(%)':          '{:+.2f}',
                    f'EMA{ema_short}':    '{:,.0f}',
                    f'EMA{ema_long}':     '{:,.0f}',
                    f'EMA{ema_mid}':      '{:,.0f}',
                    f'SMA{sma_mid}':      '{:,.0f}',
                    f'SMA{sma_long}':     '{:,.0f}',
                    '52주 신고가':        '{:,.0f}',
                }
                st.dataframe(
                    display.style.format(fmt, na_rep="-"),
                    use_container_width=True,
                    hide_index=True
                )

                # TradingView 바로가기
                st.subheader("📊 트레이딩뷰 차트 바로가기")
                cols_ui = st.columns(5)
                for i, row in enumerate(results):
                    url   = get_chart_url(row['name_raw'])
                    label = row['종목명']
                    with cols_ui[i % 5]:
                        st.link_button(f"📈 {label}", url, use_container_width=True)

                # 재무 그래프 섹션
                st.divider()
                st.subheader("📉 종목별 분기 재무 추이 (영업이익 · 부채비율)")
                st.caption("yfinance 분기별 재무제표 기준 | 영업이익: 억 원 단위 | 부채비율 = 총부채 ÷ 자기자본 × 100")

                tab_labels = [r['종목명'] for r in results[:20]]
                tabs = st.tabs(tab_labels)

                for tab, row in zip(tabs, results[:20]):
                    with tab:
                        code = row['종목코드']
                        name = row['종목명']

                        with st.spinner(f"{name} 재무 데이터 조회 중..."):
                            op_series, debt_series = get_financial_history(code)

                        col1, col2 = st.columns(2)
                        with col1:
                            if not op_series.empty:
                                latest_op = op_series.iloc[-1]
                                delta_op  = op_series.iloc[-1] - op_series.iloc[-2] if len(op_series) >= 2 else None
                                st.metric("최근 분기 영업이익", f"{latest_op:,.0f} 억원",
                                          delta=f"{delta_op:+,.0f} 억원" if delta_op is not None else None,
                                          delta_color="normal")
                            else:
                                st.metric("최근 분기 영업이익", "데이터 없음")

                        with col2:
                            if not debt_series.empty:
                                latest_debt = debt_series.iloc[-1]
                                delta_debt  = debt_series.iloc[-1] - debt_series.iloc[-2] if len(debt_series) >= 2 else None
                                st.metric("최근 분기 부채비율", f"{latest_debt:.1f}%",
                                          delta=f"{delta_debt:+.1f}%" if delta_debt is not None else None,
                                          delta_color="inverse")
                            else:
                                st.metric("최근 분기 부채비율", "데이터 없음")

                        render_financial_chart(name, code, op_series, debt_series)

                        with st.expander("📋 원본 수치 보기"):
                            base_idx = op_series.index.tolist() if not op_series.empty else debt_series.index.tolist()
                            fin_df = pd.DataFrame({
                                '분기':           base_idx,
                                '영업이익(억원)': op_series.values.tolist() if not op_series.empty else [None]*len(base_idx),
                                '부채비율(%)':    debt_series.reindex(base_idx).values.tolist() if not debt_series.empty else [None]*len(base_idx),
                            })
                            st.dataframe(
                                fin_df.style.format({
                                    '영업이익(억원)': lambda v: f"{v:,.0f}" if v is not None else "-",
                                    '부채비율(%)':   lambda v: f"{v:.1f}%" if v is not None else "-",
                                }, na_rep="-"),
                                use_container_width=True,
                                hide_index=True
                            )

st.divider()
st.caption("본 프로그램은 TradingView·KRX·Yahoo Finance 공개 데이터를 활용하며 투자 권유를 목적으로 하지 않습니다.")
