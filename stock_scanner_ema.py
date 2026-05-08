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
 
st.set_page_config(page_title="주식 스캐너 SMA", page_icon="📊", layout="wide")
 
# ★ 타이틀 글자 2포인트 작게
st.markdown(
    "<h1 style='font-size:28px;'>📊 한국 주식 종목 검색기 — SMA 밴드 스캐너</h1>",
    unsafe_allow_html=True
)
 
# ★ EMA → SMA112로 변경, 순서도 sma112 기준으로 재정렬
VALID_KEYS = {'sma112', 'sma_short', 'sma_mid', 'sma_long'}

# 세션 상태 초기화 (구버전 ema_mid 키가 남아있으면 완전히 리셋)
if ('ma_order' not in st.session_state
        or not set(st.session_state.ma_order).issubset(VALID_KEYS)):
    st.session_state.ma_order = ['sma112', 'sma_short', 'sma_mid', 'sma_long']

if ('close_dir' not in st.session_state
        or not set(st.session_state.close_dir.keys()).issubset(VALID_KEYS)):
    st.session_state.close_dir = {
        'sma112':    'below',   # 기본: 종가 < SMA112 (종가가 아래)
        'sma_short': 'above',   # 기본: SMA60 < 종가
        'sma_mid':   'above',   # 기본: SMA224 < 종가
        'sma_long':  'above',   # 기본: SMA448 < 종가
    }

if ('ma_params' not in st.session_state
        or not set(st.session_state.ma_params.keys()).issubset(VALID_KEYS)):
    st.session_state.ma_params = {
        'sma112':    112,
        'sma_short': 60,
        'sma_mid':   224,
        'sma_long':  448,
    }
 
MA_LABELS = {
    'sma112':    'SMA112',
    'sma_short': '단기 SMA',
    'sma_mid':   '중기 SMA',
    'sma_long':  '장기 SMA',
}
 
MA_TYPES = {
    'sma112':    'SMA',
    'sma_short': 'SMA',
    'sma_mid':   'SMA',
    'sma_long':  'SMA',
}
 
 
def ma_display_name(key):
    return f"{MA_TYPES[key]}{st.session_state.ma_params[key]}"
 
 
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
st.sidebar.caption("버튼 클릭으로 종가 조건 설정 (재클릭 시 해제)")
 
NUMS = ['①', '②', '③', '④']
 
# ma_order 순서대로 사이드바 표시 (순서 변경 시 자동 반영)
for order_idx, key in enumerate(st.session_state.ma_order):
    ma_type   = MA_TYPES[key]
    num_icon  = NUMS[order_idx]
    period_val = st.session_state.ma_params[key]

    st.sidebar.markdown(
        f"<div style='font-size:12px;font-weight:700;color:#1e6f3e;margin-top:10px'>"
        f"{num_icon} {ma_type}</div>",
        unsafe_allow_html=True
    )

    val = st.sidebar.number_input(
        f"{ma_type} 기간",
        value=period_val,
        min_value=1,
        key=f"num_{key}",
        label_visibility="collapsed"
    )
    st.session_state.ma_params[key] = val

    cur_dir = st.session_state.close_dir[key]
    ma_name = f"{ma_type}{val}"

    # ── 3열: [종가<SMA] [SMA명(중앙)] [SMA<종가] ──────────────────
    c_left, c_mid, c_right = st.sidebar.columns([2, 2, 2])

    with c_left:
        active_below = cur_dir == 'below'
        lbl = f"{'🔴' if active_below else '⬜'} -종가"
        if st.button(lbl, key=f"btn_below_{key}", use_container_width=True,
                     help=f"종가 < {ma_name} 조건 설정"):
            st.session_state.close_dir[key] = None if active_below else 'below'
            st.rerun()

    with c_mid:
        # 중앙: SMA명 표시 (클릭 시 조건 해제)
        mid_label = f"**{ma_name}**" if cur_dir is None else ma_name
        st.markdown(
            f"<div style='text-align:center;padding:6px 0 2px;"
            f"font-size:12px;font-weight:700;color:#1a3a24;'>{ma_name}</div>",
            unsafe_allow_html=True
        )
        if cur_dir is not None:
            if st.button("✖ 해제", key=f"btn_clear_{key}", use_container_width=True):
                st.session_state.close_dir[key] = None
                st.rerun()

    with c_right:
        active_above = cur_dir == 'above'
        lbl = f"{'🟢' if active_above else '⬜'} +종가"
        if st.button(lbl, key=f"btn_above_{key}", use_container_width=True,
                     help=f"{ma_name} < 종가 조건 설정"):
            st.session_state.close_dir[key] = None if active_above else 'above'
            st.rerun()

    # 현재 조건 표시
    if cur_dir == 'below':
        st.sidebar.caption(f"  ↳ 조건: 종가 < {ma_name}")
    elif cur_dir == 'above':
        st.sidebar.caption(f"  ↳ 조건: {ma_name} < 종가")
 
# ── 사이드바 ↑↓ 순서 변경 ────────────────────────────────────────
st.sidebar.divider()
st.sidebar.markdown("**🔢 배열 순서 조정 (↑ ↓)**")
st.sidebar.caption("또는 아래 드래그&드롭 위젯 사용")
 
for i, key in enumerate(st.session_state.ma_order):
    name  = ma_display_name(key)
    label = MA_LABELS[key]
    col_name, col_up, col_dn = st.sidebar.columns([3, 1, 1])
    with col_name:
        st.markdown(f"<div style='padding-top:5px;font-size:12px'><b>{NUMS[i]} {name}</b> {label}</div>",
                    unsafe_allow_html=True)
    with col_up:
        if i > 0:
            if st.button("↑", key=f"up_{key}_{i}", use_container_width=True):
                lst = st.session_state.ma_order
                lst[i], lst[i-1] = lst[i-1], lst[i]
                st.rerun()
    with col_dn:
        if i < len(st.session_state.ma_order) - 1:
            if st.button("↓", key=f"dn_{key}_{i}", use_container_width=True):
                lst = st.session_state.ma_order
                lst[i], lst[i+1] = lst[i+1], lst[i]
                st.rerun()
 
# ── 사이드바 조건 요약 ────────────────────────────────────────────
st.sidebar.divider()
st.sidebar.markdown("**📋 현재 조건 요약**")
 
order     = st.session_state.ma_order
params    = st.session_state.ma_params
close_dir = st.session_state.close_dir
 
order_parts = []
for i in range(len(order) - 1):
    a, b = order[i], order[i+1]
    order_parts.append(f"{MA_TYPES[a]}{params[a]} < {MA_TYPES[b]}{params[b]}")
st.sidebar.caption("배열: " + " · ".join(order_parts))
 
close_parts = []
for key in order:
    d = close_dir[key]
    name = f"{MA_TYPES[key]}{params[key]}"
    if d == 'above':
        close_parts.append(f"{name} < 종가")
    elif d == 'below':
        close_parts.append(f"종가 < {name}")
st.sidebar.caption("종가: " + (" · ".join(close_parts) if close_parts else "조건 없음"))
 
 
# ── 메인 화면 — 드래그&드롭 순서 위젯 ──────────────────────────
st.markdown("<h3 style='font-size:20px;'>📐 이동평균 배열 순서 — 드래그&드롭</h3>", unsafe_allow_html=True)
st.caption("카드를 드래그해서 순서를 바꾸고 **[✅ 적용]** 버튼을 눌러주세요. 순서대로 MA① < MA② < MA③ < MA④ 조건이 적용됩니다.")
 
order_data = [
    {
        "key":   k,
        "label": MA_LABELS[k],
        "name":  f"{MA_TYPES[k]}{params[k]}",
        "dir":   close_dir[k],
        "type":  MA_TYPES[k],
    }
    for k in st.session_state.ma_order
]
 
drag_html = f"""
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
 
#drag-wrap {{
  font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
  background: transparent;
}}
 
#cards-row {{
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 8px 0 4px;
  overflow-x: auto;
}}
 
.card-slot {{
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}}
 
.ma-card {{
  flex: 1;
  min-width: 0;
  max-width: 90px;
  background: white;
  border: 2px solid #c8e6d0;
  border-radius: 10px;
  padding: 12px 3px 8px;
  cursor: grab;
  user-select: none;
  transition: box-shadow 0.18s, transform 0.18s, border-color 0.18s, opacity 0.18s;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  min-height: 90px;
  position: relative;
  overflow: hidden;
}}
.ma-card:hover {{
  border-color: #1e6f3e;
  box-shadow: 0 4px 16px rgba(30,111,62,0.13);
}}
.ma-card:active {{ cursor: grabbing; }}
.ma-card.dragging {{
  opacity: 0.35;
  transform: scale(0.95);
  box-shadow: none;
  border-color: #aaa;
}}
.ma-card.drag-over {{
  border-color: #1e6f3e;
  box-shadow: 0 0 0 4px rgba(30,111,62,0.2);
  transform: scale(1.04);
}}
 
.card-num {{
  position: absolute;
  top: 7px;
  left: 9px;
  font-size: 11px;
  font-weight: 800;
  color: #1e6f3e;
  background: #e8f5e9;
  border-radius: 20px;
  padding: 1px 7px;
}}
 
.card-type-badge {{
  position: absolute;
  top: 7px;
  right: 9px;
  font-size: 10px;
  font-weight: 700;
  color: #888;
  background: #f0f0f0;
  border-radius: 20px;
  padding: 1px 6px;
}}
 
.card-name {{
  font-size: 15px;
  font-weight: 900;
  color: #1a3a24;
  margin-top: 14px;
  letter-spacing: -0.5px;
  word-break: break-all;
  text-align: center;
}}
.card-label {{
  font-size: 11px;
  color: #777;
}}
.card-dir {{
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 20px;
  margin-top: 2px;
  text-align: center;
  word-break: keep-all;
}}
.dir-above {{ background: #e8f5e9; color: #1e6f3e; }}
.dir-below {{ background: #fdecea; color: #c0392b; }}
.dir-none  {{ background: #f5f5f5; color: #bbb; }}
 
.arrow-sep {{
  font-size: 16px;
  font-weight: 900;
  color: #1e6f3e;
  padding: 0 2px;
  flex-shrink: 0;
  align-self: center;
  line-height: 1;
  margin-top: 8px;
}}
 
#btn-row {{
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}}
 
#apply-btn {{
  background: #1e6f3e;
  color: white;
  border: none;
  padding: 9px 28px;
  border-radius: 9px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
  font-family: inherit;
}}
#apply-btn:hover  {{ background: #165a32; transform: translateY(-1px); }}
#apply-btn:active {{ transform: translateY(0); }}
#apply-btn.success {{ background: #0d4a25; }}
 
#status-msg {{
  font-size: 12px;
  color: #1e6f3e;
  font-weight: 600;
  min-height: 18px;
}}
</style>
 
<div id="drag-wrap">
  <div id="cards-row"></div>
  <div id="btn-row">
    <button id="apply-btn" onclick="applyOrder()">✅ 적용</button>
    <span id="status-msg"></span>
  </div>
</div>
 
<script>
const NUMS = ['①','②','③','④'];
let currentOrder = {json.dumps(order_data)};
 
function dirClass(dir) {{
  return dir === 'above' ? 'dir-above' : dir === 'below' ? 'dir-below' : 'dir-none';
}}
function dirText(dir, name) {{
  return dir === 'above' ? name + ' < 종가' : dir === 'below' ? '종가 < ' + name : '종가 조건 없음';
}}
 
function renderCards() {{
  const row = document.getElementById('cards-row');
  row.innerHTML = '';
 
  currentOrder.forEach((item, idx) => {{
    if (idx > 0) {{
      const arrow = document.createElement('div');
      arrow.className = 'arrow-sep';
      arrow.textContent = '<';
      row.appendChild(arrow);
    }}
 
    const slot = document.createElement('div');
    slot.className = 'card-slot';
 
    const card = document.createElement('div');
    card.className = 'ma-card';
    card.draggable = true;
    card.dataset.idx = idx;
 
    card.innerHTML = `
      <div class="card-num">${{NUMS[idx]}}</div>
      <div class="card-type-badge">${{item.type}}</div>
      <div class="card-name">${{item.name}}</div>
      <div class="card-label">${{item.label}}</div>
      <div class="card-dir ${{dirClass(item.dir)}}">${{dirText(item.dir, item.name)}}</div>
    `;
 
    card.addEventListener('dragstart', e => {{
      e.dataTransfer.setData('text/plain', String(idx));
      e.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => card.classList.add('dragging'));
    }});
    card.addEventListener('dragend', () => {{
      card.classList.remove('dragging');
      document.querySelectorAll('.ma-card').forEach(c => c.classList.remove('drag-over'));
    }});
    card.addEventListener('dragover', e => {{
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      document.querySelectorAll('.ma-card').forEach(c => c.classList.remove('drag-over'));
      card.classList.add('drag-over');
    }});
    card.addEventListener('dragleave', e => {{
      if (!card.contains(e.relatedTarget)) card.classList.remove('drag-over');
    }});
    card.addEventListener('drop', e => {{
      e.preventDefault();
      card.classList.remove('drag-over');
      const fromIdx = parseInt(e.dataTransfer.getData('text/plain'));
      const toIdx   = parseInt(card.dataset.idx);
      if (fromIdx === toIdx) return;
      const [moved] = currentOrder.splice(fromIdx, 1);
      currentOrder.splice(toIdx, 0, moved);
      renderCards();
      document.getElementById('status-msg').textContent = '순서 변경됨 — [✅ 적용] 버튼을 눌러주세요';
      document.getElementById('status-msg').style.color = '#e07010';
    }});
 
    slot.appendChild(card);
    row.appendChild(slot);
  }});
}}
 
function applyOrder() {{
  const keys    = currentOrder.map(i => i.key).join(',');
  const nameStr = currentOrder.map(i => i.name).join(' < ');
  const msg     = document.getElementById('status-msg');
  const btn     = document.getElementById('apply-btn');
 
  window.parent.postMessage({{
    isStreamlitMessage: true,
    type: 'streamlit:setComponentValue',
    value: keys
  }}, '*');
 
  btn.classList.add('success');
  btn.textContent = '✅ 적용됨!';
  msg.style.color = '#1e6f3e';
  msg.textContent = '배열: ' + nameStr;
 
  setTimeout(() => {{
    btn.classList.remove('success');
    btn.textContent = '✅ 적용';
  }}, 2500);
}}
 
renderCards();
</script>
"""
 
drag_result = st.components.v1.html(drag_html, height=220, scrolling=False)
 
# ── 현재 조건 요약 (메인 화면) ──────────────────────────────────
order     = st.session_state.ma_order
params    = st.session_state.ma_params
close_dir = st.session_state.close_dir
 
order_str_parts = []
for i in range(len(order) - 1):
    a, b = order[i], order[i+1]
    order_str_parts.append(f"**{MA_TYPES[a]}{params[a]}** < **{MA_TYPES[b]}{params[b]}**")
 
close_cond_parts = []
for key in order:
    d    = close_dir[key]
    name = f"{MA_TYPES[key]}{params[key]}"
    if d == 'above':
        close_cond_parts.append(f"**{name} < 종가**")
    elif d == 'below':
        close_cond_parts.append(f"**종가 < {name}**")
 
st.markdown(f"""
**검색 조건 (일봉 기준)**
- 📊 **배열**: {' · '.join(order_str_parts)}
- 🎯 **종가**: {' · '.join(close_cond_parts) if close_cond_parts else '없음'}
- 💰 **주가**: {min_price:,}원 ~ {max_price:,}원 · 거래량 {min_vol:,} 이상
- 🚫 ETF · 스팩 · 우선주 · 거래정지 · 투자경고 · 관리종목 · 환기종목 자동 제외
""")
 
 
# ── KRX 데이터 로딩 ──────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_krx_data():
    name_map       = {}
    exclude_set    = set()
    sanction_codes = set()
 
    try:
        url        = "https://kind.krx.co.kr/corpgeneral/corpList.do"
        params_req = {"method": "download", "searchType": "13"}
        headers    = {"User-Agent": "Mozilla/5.0"}
        response   = requests.get(url, params=params_req, headers=headers, timeout=10)
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
                if not code.endswith('0'):
                    exclude_set.add(code)
                    continue
                exclude_keywords = ['스팩', 'SPAC', '리츠', 'REIT', '인프라', '환기',
                                    '수익증권', 'ETF', 'ETN', 'ELW']
                if any(kw in name.upper() for kw in exclude_keywords):
                    exclude_set.add(code)
 
    except Exception as e:
        st.warning(f"KRX 종목 목록 로딩 실패 ({e}).")
 
    sanction_urls = [
        {"url": "https://kind.krx.co.kr/investwarning/managementissue.do",
         "params": {"method": "searchManagementIssueSub", "marketType": "0"}},
        {"url": "https://kind.krx.co.kr/investwarning/investwarning.do",
         "params": {"method": "searchInvestWarningSub", "marketType": "0"}},
        {"url": "https://kind.krx.co.kr/investwarning/tradesuspend.do",
         "params": {"method": "searchTradeSuspendSub", "marketType": "0"}},
        {"url": "https://kind.krx.co.kr/investwarning/unfaithfuldisclosure.do",
         "params": {"method": "searchUnfaithfulDisclosureSub", "marketType": "0"}},
    ]
 
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kind.krx.co.kr/"}
    for item in sanction_urls:
        try:
            resp = requests.get(item["url"], params=item["params"], headers=headers, timeout=10)
            resp.encoding = 'euc-kr'
            tables = pd.read_html(io.StringIO(resp.text))
            if not tables:
                continue
            tbl = tables[0]
            tbl.columns = tbl.columns.str.strip()
            code_col = next(
                (c for c in tbl.columns if '종목코드' in c or '단축코드' in c or '코드' in c), None
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
 
 
# ── 재무 데이터 ─────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_financial_history(code_6: str):
    for suffix in ['.KS', '.KQ']:
        ticker_str = f"{code_6}{suffix}"
        try:
            tk  = yf.Ticker(ticker_str)
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
                for label in ['Stockholders Equity', 'Total Equity Gross Minority Interest', 'Common Stock Equity']:
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


# ── 업종 정보 ────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_sector_info(code_6: str):
    for suffix in ['.KS', '.KQ']:
        ticker_str = f"{code_6}{suffix}"
        try:
            tk = yf.Ticker(ticker_str)
            info = tk.info
            sector   = info.get('sector', None)
            industry = info.get('industry', None)
            employees = info.get('fullTimeEmployees', None)
            summary  = info.get('longBusinessSummary', None)
            if sector or industry:
                return {
                    'sector':    sector or '-',
                    'industry':  industry or '-',
                    'employees': employees,
                    'summary':   summary,
                }
        except Exception:
            continue
    return None
 
 
# ── 재무 그래프 ──────────────────────────────────────────────────
def render_financial_chart(name: str, code: str, op_series: pd.Series, debt_series: pd.Series):
    has_op   = not op_series.empty
    has_debt = not debt_series.empty
    if not has_op and not has_debt:
        st.warning(f"{name} — 재무 데이터를 가져올 수 없습니다.")
        return
 
    all_idx   = sorted(set(op_series.index if has_op else []) | set(debt_series.index if has_debt else []))
    quarters  = all_idx
    op_vals   = [round(float(op_series[q]),   1) if (has_op   and q in op_series.index)   else None for q in quarters]
    debt_vals_raw = [round(float(debt_series[q]), 1) if (has_debt and q in debt_series.index) else None for q in quarters]

    chart_id  = f"chart_{code}"
 
    html = f"""
<div style="background:linear-gradient(160deg,#d4edda 0%,#e8f5e9 40%,#f0faf1 100%);
            border-radius:12px;padding:20px 20px 16px;font-family:'Malgun Gothic',sans-serif;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
    <div style="width:13px;height:13px;background:#2d6a3f;border-radius:2px;"></div>
    <span style="font-size:14px;font-weight:700;color:#1a3a24;">{name} ({code}) — 분기별 재무 추이</span>
  </div>

  <!-- 억원 / % 좌우 표시 -->
  <div style="display:flex;justify-content:space-between;
              font-size:11px;font-weight:700;margin-bottom:2px;padding:0 4px;">
    <span style="color:#2d7a4a;">억원</span>
    <span style="color:#b05010;">%</span>
  </div>

  <div style="position:relative;height:290px;"><canvas id="{chart_id}"></canvas></div>

  <!-- ★ 클릭 정보 패널 (툴팁 대신 차트 아래 고정) -->
  <div id="info_{chart_id}"
       style="min-height:36px;margin:8px 0 6px;padding:8px 14px;
              background:rgba(30,111,62,0.08);border-radius:8px;
              font-size:13px;font-weight:600;color:#1a3a24;
              border-left:3px solid #2d7a4a;display:flex;
              align-items:center;flex-wrap:wrap;gap:8px;">
    <span style="color:#888;font-weight:400;font-size:12px;">막대를 탭/클릭하면 수치가 표시됩니다</span>
  </div>

  <!-- ★ 하단 범례: 세로 배치 -->
  <div style="display:flex;flex-direction:column;gap:4px;margin-top:6px;font-size:11px;color:#444;">
    <span><span style="width:12px;height:10px;background:#3a9e5f;border-radius:2px;
                       display:inline-block;margin-right:5px;vertical-align:middle;"></span>영업이익 (+억원)</span>
    <span><span style="width:12px;height:10px;background:#c0392b;border-radius:2px;
                       display:inline-block;margin-right:5px;vertical-align:middle;"></span>영업이익 (-억원)</span>
    <span><span style="width:12px;height:10px;background:#e07010;border-radius:2px;
                       display:inline-block;margin-right:5px;vertical-align:middle;"></span>부채비율 (%)</span>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
(function() {{
  const quarters={json.dumps(quarters)};
  const opVals={json.dumps(op_vals)};
  const debtValsRaw={json.dumps(debt_vals_raw)};
  const ctx=document.getElementById('{chart_id}');
  const infoPanel=document.getElementById('info_{chart_id}');
  if(!ctx)return;

  function showInfo(i) {{
    const q = quarters[i];
    const op = opVals[i];
    const dt = debtValsRaw[i];
    let html = `<span style="color:#2d7a4a;font-weight:700;">${{q}}</span>`;
    if(op !== null) {{
      const opColor = op >= 0 ? '#1a5c30' : '#c0392b';
      html += `&nbsp;&nbsp;<span style="color:${{opColor}};">영업이익: ${{op.toLocaleString()}}억원</span>`;
    }}
    if(dt !== null) {{
      html += `&nbsp;&nbsp;<span style="color:#8a3d00;">부채비율: ${{dt.toFixed(1)}}%</span>`;
    }}
    infoPanel.innerHTML = html;
  }}

  const chartInst = new Chart(ctx,{{
    data:{{
      labels:quarters,
      datasets:[{{
        type:'bar',
        data:opVals,
        backgroundColor:'transparent',
        borderWidth:0,
        yAxisID:'yLeft'
      }}]
    }},
    options:{{
      responsive:true,
      maintainAspectRatio:false,
      plugins:{{
        legend:{{display:false}},
        tooltip:{{enabled:false}}   // ★ 툴팁 완전 비활성화
      }},
      scales:{{
        x:{{
          grid:{{display:false}},
          ticks:{{color:'transparent',font:{{size:11}}}},
          border:{{display:false}}
        }},
        yLeft:{{
          type:'linear',position:'left',
          ticks:{{color:'#2d7a4a',font:{{size:11}},callback:v=>v.toLocaleString()}},
          grid:{{
            color:c=>c.tick.value===0?'rgba(0,0,0,0.6)':'rgba(180,200,180,0.35)',
            lineWidth:c=>c.tick.value===0?2:1
          }},
          border:{{display:false}}
        }}
      }},
      layout:{{padding:{{top:28,bottom:32}}}},
      interaction:{{mode:'index',intersect:false}},
      // ★ 클릭/터치 이벤트로 infoPanel 업데이트
      onClick(e, elements) {{
        if(elements && elements.length > 0) {{
          showInfo(elements[0].index);
        }}
      }}
    }},
    plugins:[{{
      id:'cd_{code}',
      afterDraw(chart){{
        const ctx=chart.ctx;
        const xScale=chart.scales.x;
        const yLeft=chart.scales.yLeft;
        ctx.save();

        const zeroY   = yLeft.getPixelForValue(0);
        const chartH  = chart.chartArea.bottom - chart.chartArea.top;
        const maxDebt = Math.max(...debtValsRaw.filter(v=>v!==null), 1);
        const debtPPU = (chartH * 0.38) / maxDebt;

        const dummyMeta = chart.getDatasetMeta(0);
        const fullBarW  = dummyMeta.data.length > 0 ? dummyMeta.data[0].width : 24;
        const gap = 2;

        function drawBar(cx, w, top, bot, color) {{
          const h = Math.abs(bot - top);
          if(h < 1) return;
          const r = Math.min(4, h / 2);
          const yTop = Math.min(top, bot);
          const yBot = Math.max(top, bot);
          ctx.beginPath();
          if(top > bot) {{
            ctx.moveTo(cx - w/2, yBot);
            ctx.lineTo(cx + w/2, yBot);
            ctx.lineTo(cx + w/2, yTop + r);
            ctx.quadraticCurveTo(cx + w/2, yTop, cx + w/2 - r, yTop);
            ctx.lineTo(cx - w/2 + r, yTop);
            ctx.quadraticCurveTo(cx - w/2, yTop, cx - w/2, yTop + r);
            ctx.lineTo(cx - w/2, yBot);
          }} else {{
            ctx.moveTo(cx - w/2, yTop);
            ctx.lineTo(cx + w/2, yTop);
            ctx.lineTo(cx + w/2, yBot - r);
            ctx.quadraticCurveTo(cx + w/2, yBot, cx + w/2 - r, yBot);
            ctx.lineTo(cx - w/2 + r, yBot);
            ctx.quadraticCurveTo(cx - w/2, yBot, cx - w/2, yBot - r);
            ctx.lineTo(cx - w/2, yTop);
          }}
          ctx.closePath();
          ctx.fillStyle = color;
          ctx.fill();
        }}

        quarters.forEach((q, i) => {{
          const opVal  = opVals[i];
          const dbtVal = debtValsRaw[i];
          const xC     = xScale.getPixelForValue(i);

          const overlap = (opVal !== null && opVal < 0 && dbtVal !== null);

          const opW    = overlap ? fullBarW/2 - gap/2 : fullBarW * 0.72;
          const dbtW   = overlap ? fullBarW/2 - gap/2 : fullBarW * 0.72;
          const opCX   = overlap ? xC - fullBarW/4 - gap/2 : xC;
          const dbtCX  = overlap ? xC + fullBarW/4 + gap/2 : xC;

          if(opVal !== null) {{
            const opColor = opVal >= 0 ? '#3a9e5f' : '#c0392b';
            const opTop   = opVal >= 0 ? yLeft.getPixelForValue(opVal) : zeroY;
            const opBot   = opVal >= 0 ? zeroY : yLeft.getPixelForValue(opVal);
            drawBar(opCX, opW, opTop, opBot, opColor);
          }}

          if(dbtVal !== null) {{
            const dbtBot = zeroY + dbtVal * debtPPU;
            drawBar(dbtCX, dbtW, zeroY, dbtBot, 'rgba(224,112,16,0.88)');
          }}
        }});

        // ★ 수치 레이블: 스마트폰 가림 방지를 위해 막대 내부에 표시
        ctx.textAlign = 'center';

        quarters.forEach((q, i) => {{
          const opVal  = opVals[i];
          const dbtVal = debtValsRaw[i];
          const xC     = xScale.getPixelForValue(i);
          const overlap = (opVal !== null && opVal < 0 && dbtVal !== null);
          const gap2    = 2;
          const opCX    = overlap ? xC - fullBarW/4 - gap2/2 : xC;
          const dbtCX   = overlap ? xC + fullBarW/4 + gap2/2 : xC;

          // 영업이익 레이블: 막대 위쪽 (막대 높이가 충분할 때만)
          if(opVal !== null) {{
            const barTop = opVal >= 0 ? yLeft.getPixelForValue(opVal) : zeroY;
            const barBot = opVal >= 0 ? zeroY : yLeft.getPixelForValue(opVal);
            const barH   = Math.abs(barBot - barTop);
            ctx.font = "bold 10px 'Malgun Gothic',sans-serif";
            if(barH > 22) {{
              // 막대 안쪽 상단에 표시
              ctx.fillStyle = opVal >= 0 ? '#ffffff' : '#ffffff';
              const labelY = opVal >= 0 ? barTop + 14 : barBot - 6;
              ctx.fillText(opVal.toLocaleString(), opCX, labelY);
            }} else {{
              // 막대가 작으면 위/아래 바깥에 표시
              ctx.fillStyle = opVal < 0 ? '#8a1a10' : '#1a5c30';
              const labelY = opVal >= 0 ? barTop - 5 : barBot + 13;
              ctx.fillText(opVal.toLocaleString(), opCX, labelY);
            }}
          }}

          // 부채비율 레이블: 막대 안쪽 하단에 표시
          if(dbtVal !== null) {{
            const dbtBot = zeroY + dbtVal * debtPPU;
            const barH   = Math.abs(dbtBot - zeroY);
            ctx.font = "bold 10px 'Malgun Gothic',sans-serif";
            if(barH > 22) {{
              ctx.fillStyle = '#ffffff';
              ctx.fillText(dbtVal.toFixed(1)+'%', dbtCX, dbtBot - 5);
            }} else {{
              ctx.fillStyle = '#8a3d00';
              ctx.fillText(dbtVal.toFixed(1)+'%', dbtCX, dbtBot + 13);
            }}
          }}
        }});

        ctx.beginPath();ctx.moveTo(chart.chartArea.left,zeroY);ctx.lineTo(chart.chartArea.right,zeroY);
        ctx.strokeStyle='rgba(0,0,0,0.75)';ctx.lineWidth=2;ctx.stroke();

        ctx.beginPath();ctx.moveTo(chart.chartArea.left,chart.chartArea.top);ctx.lineTo(chart.chartArea.left,chart.chartArea.bottom);
        ctx.strokeStyle='rgba(0,0,0,0.5)';ctx.lineWidth=1.5;ctx.stroke();
        ctx.beginPath();ctx.moveTo(chart.chartArea.right,chart.chartArea.top);ctx.lineTo(chart.chartArea.right,chart.chartArea.bottom);
        ctx.strokeStyle='rgba(0,0,0,0.5)';ctx.lineWidth=1.5;ctx.stroke();

        const yBottom=chart.chartArea.bottom;
        // 라벨 배경박스: chartArea 바로 아래 2px 간격, 높이 24px
        ctx.fillStyle='#445544';
        ctx.fillRect(chart.chartArea.left, yBottom + 2, chart.chartArea.width, 24);
        ctx.font="bold 10px 'Malgun Gothic',sans-serif";
        ctx.textAlign='center';
        ctx.fillStyle='#fff';
        quarters.forEach((q,i)=>{{ctx.fillText(q,xScale.getPixelForValue(i),yBottom+17);}});

        ctx.restore();
      }}
    }}]
  }});

  // ★ 터치(모바일) 이벤트도 클릭으로 처리
  const canvasEl = document.getElementById('{chart_id}');
  canvasEl.addEventListener('touchstart', function(e) {{
    e.preventDefault();
    const touch = e.touches[0];
    const nativeEvent = {{
      clientX: touch.clientX,
      clientY: touch.clientY,
      target:  canvasEl,
    }};
    const elements = chartInst.getElementsAtEventForMode(
      nativeEvent, 'index', {{intersect: false}}, true
    );
    if(elements && elements.length > 0) {{
      showInfo(elements[0].index);
    }}
  }}, {{passive: false}});

}})();
</script>"""
    # 높이: 차트290 + 상하패딩 + 정보패널36 + 범례90 + 여유
    st.components.v1.html(html, height=520, scrolling=False)
 
 
# ── TradingView 수집 ─────────────────────────────────────────────
def run_tv_scanner_full():
    all_rows, offset, batch = [], 0, 1500
    while True:
        try:
            result = (
                Query()
                .set_markets("korea")
                .select('name', 'close', 'volume', 'change', 'SMA200', 'price_52_week_high')
                .where(col('type') == 'stock')
                .offset(offset).limit(batch)
                .get_scanner_data()
            )
            if result is None: break
            count, data = result
            if data is None or data.empty: break
            all_rows.append(data)
            fetched = len(data)
            if fetched < batch: break
            offset += fetched
            time.sleep(0.5)
        except TypeError: break
        except Exception as e:
            st.warning(f"TradingView 수집 중단 (offset={offset}): {e}")
            break
    return pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
 
 
def apply_price_volume_filter(data, min_price, max_price, min_vol):
    mask = (data['close'] >= min_price) & (data['close'] <= max_price) & (data['volume'] > min_vol)
    return data[mask].copy()
 
 
# ── SMA 조건 검증 (EMA→SMA로 변경) ────────────────────────────────
def check_ema_conditions(code_6, ma_order, ma_params, close_dir):
    sma_keys   = [k for k in ma_order if MA_TYPES[k] == 'SMA']
    max_period = max(ma_params[k] for k in sma_keys) if sma_keys else 60
    need_days  = max_period + 50
 
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
 
            close      = df['Close']
            curr_close = float(close.iloc[-1])
 
            ma_vals = {}
            for key in ma_order:
                period = ma_params[key]
                # ★ 모두 SMA로 계산
                ma_vals[key] = float(close.rolling(period).mean().iloc[-1])
 
            cond_order = all(
                ma_vals[ma_order[i]] < ma_vals[ma_order[i+1]]
                for i in range(len(ma_order) - 1)
            )
 
            cond_close = True
            for key in ma_order:
                d = close_dir[key]
                if d == 'above':
                    cond_close = cond_close and (ma_vals[key] < curr_close)
                elif d == 'below':
                    cond_close = cond_close and (ma_vals[key] > curr_close)
 
            return cond_order and cond_close, curr_close, ma_vals
 
        except Exception:
            continue
 
    return False, None, {}
 
 
def check_one(row_tuple, ma_order, ma_params, close_dir):
    idx, row = row_tuple
    code     = row['종목코드']
    result   = check_ema_conditions(code, ma_order, ma_params, close_dir)
    return idx, row, result
 
 
def get_chart_url(ticker_raw):
    symbol = ticker_raw if ":" in str(ticker_raw) else f"KRX:{ticker_raw}"
    return f"https://www.tradingview.com/chart/?symbol={symbol}"
 
 
# ── 메인 실행 ────────────────────────────────────────────────────
if st.button("🔍 종목 검색 시작", use_container_width=True):
    if min_price >= max_price:
        st.error("⚠️ 최소 금액이 최대 금액보다 작아야 합니다.")
    else:
        ma_order_snap  = list(st.session_state.ma_order)
        ma_params_snap = dict(st.session_state.ma_params)
        close_dir_snap = dict(st.session_state.close_dir)
 
        with st.spinner("📋 KRX 종목 정보 및 제재종목 로딩 중..."):
            name_map, exclude_set, sanction_codes = load_krx_data()
 
        all_excluded = exclude_set | sanction_codes
        st.info(f"🚫 제외: ETF·스팩·우선주 {len(exclude_set)}개 + 제재종목 {len(sanction_codes)}개 = {len(all_excluded)}개")
 
        with st.spinner("🔍 TradingView 전체 종목 수집 중..."):
            data = run_tv_scanner_full()
 
        if data is None or data.empty:
            st.warning("⚠️ TradingView에서 종목을 가져오지 못했습니다.")
        else:
            total_tv = len(data)
            data['종목코드'] = data['name'].apply(lambda x: str(x).split(':')[-1]).str.zfill(6)
            data = apply_price_volume_filter(data, min_price, max_price, min_vol)
            after_price = len(data)
            data = data[~data['종목코드'].isin(all_excluded)]
            after_sanction = len(data)
            data['종목명'] = data['종목코드'].map(name_map)
            data['종목명'] = data.apply(
                lambda r: name_map.get(str(r['name']).split(':')[-1].zfill(6), str(r['name']).split(':')[-1])
                if pd.isna(r['종목명']) else r['종목명'], axis=1
            )
            etf_pattern = r'ETF|ETN|KODEX|TIGER|RISE|ACE|KBSTAR|HANARO|ARIRANG|SOL|KOSEF'
            data = data[data['종목명'].notna()]
            data = data[~data['종목명'].str.contains(etf_pattern, case=False, na=False)]
            after_etf = len(data)
 
            st.info(
                f"📊 수집: {total_tv}개 → 주가·거래량: {after_price}개 "
                f"→ 제재·ETF 제외: {after_sanction}개 → ETF패턴: {after_etf}개 "
                f"→ **조건 검증 시작** (병렬 {max_workers}workers)"
            )
 
            progress_bar = st.progress(0)
            status_text  = st.empty()
            results      = []
            total        = len(data)
            done_count   = 0
 
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(check_one, row_tuple, ma_order_snap, ma_params_snap, close_dir_snap): row_tuple
                    for row_tuple in data.iterrows()
                }
                for future in as_completed(futures):
                    done_count += 1
                    progress_bar.progress(done_count / total)
                    try:
                        idx, row, (pass_all, curr_close, ma_vals) = future.result()
                    except Exception:
                        status_text.text(f"⚡ [{done_count}/{total}] 검증 중...")
                        continue
 
                    code = row['종목코드']
                    name = row['종목명']
                    status_text.text(f"⚡ [{done_count}/{total}] {name}({code}) 검증 완료")
 
                    if pass_all:
                        entry = {
                            '종목명':      name,
                            '종목코드':    code,
                            '현재가(원)':  row['close'],
                            '거래량':      row['volume'],
                            '등락률(%)':   row['change'],
                            '52주 신고가': row.get('price_52_week_high', None),
                            'name_raw':    row['name'],
                        }
                        for key in ma_order_snap:
                            col_name = f"{MA_TYPES[key]}{ma_params_snap[key]}"
                            entry[col_name] = round(ma_vals[key], 0) if key in ma_vals else None
                        results.append(entry)
 
            progress_bar.empty()
            status_text.empty()
 
            if not results:
                st.warning("⚠️ 모든 조건을 만족하는 종목이 없습니다. 조건을 완화해 보세요.")
            else:
                st.success(f"✅ 최종 {len(results)}개 종목 발견!")
                result_df = pd.DataFrame(results)
 
                ma_cols      = [f"{MA_TYPES[k]}{ma_params_snap[k]}" for k in ma_order_snap]
                display_cols = ['종목명', '종목코드', '현재가(원)', '거래량', '등락률(%)'] + ma_cols + ['52주 신고가']
                display_cols = [c for c in display_cols if c in result_df.columns]
                display      = result_df[display_cols].copy()
 
                fmt = {'현재가(원)': '{:,.0f}', '거래량': '{:,.0f}', '등락률(%)': '{:+.2f}', '52주 신고가': '{:,.0f}'}
                for c in ma_cols:
                    fmt[c] = '{:,.0f}'
                st.dataframe(display.style.format(fmt, na_rep="-"), use_container_width=True, hide_index=True)

                # ★ 순서 변경: 재무 추이 먼저, 트레이딩뷰 차트 바로가기는 아래로
                st.divider()
                st.subheader("📉 종목별 분기 재무 추이 (영업이익 · 부채비율)")
                st.caption("yfinance 분기별 재무제표 기준 | 영업이익: 억 원 | 부채비율 = 총부채 ÷ 자기자본 × 100")
 
                tabs = st.tabs([r['종목명'] for r in results[:20]])
                for tab, row in zip(tabs, results[:20]):
                    with tab:
                        code = row['종목코드']
                        name = row['종목명']
                        with st.spinner(f"{name} 재무 데이터 조회 중..."):
                            op_series, debt_series = get_financial_history(code)
 
                        c1, c2 = st.columns(2)
                        with c1:
                            if not op_series.empty:
                                latest_op = op_series.iloc[-1]
                                delta_op  = op_series.iloc[-1] - op_series.iloc[-2] if len(op_series) >= 2 else None
                                st.metric("최근 분기 영업이익", f"{latest_op:,.0f} 억원",
                                          delta=f"{delta_op:+,.0f} 억원" if delta_op is not None else None,
                                          delta_color="normal")
                            else:
                                st.metric("최근 분기 영업이익", "데이터 없음")
                        with c2:
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
                                    '부채비율(%)':    lambda v: f"{v:.1f}%" if v is not None else "-",
                                }, na_rep="-"),
                                use_container_width=True, hide_index=True
                            )

                            st.markdown("---")
                            st.markdown("**🏭 업종 정보**")
                            with st.spinner("업종 정보 조회 중..."):
                                sector_info = get_sector_info(code)
                            if sector_info:
                                col_s1, col_s2, col_s3 = st.columns(3)
                                with col_s1:
                                    st.markdown(f"**섹터**  \n{sector_info['sector']}")
                                with col_s2:
                                    st.markdown(f"**업종**  \n{sector_info['industry']}")
                                with col_s3:
                                    emp = sector_info['employees']
                                    st.markdown(f"**임직원 수**  \n{f'{emp:,}명' if emp else '-'}")
                                if sector_info.get('summary'):
                                    summary_text = sector_info['summary']
                                    if len(summary_text) > 300:
                                        summary_text = summary_text[:300] + "..."
                                    st.caption(summary_text)
                            else:
                                st.caption("업종 정보를 가져올 수 없습니다.")

                # ★ 트레이딩뷰 차트 바로가기를 재무 추이 아래로 이동
                st.divider()
                st.subheader("📊 트레이딩뷰 차트 바로가기")
                cols_ui = st.columns(5)
                for i, row in enumerate(results):
                    with cols_ui[i % 5]:
                        st.link_button(f"📈 {row['종목명']}", get_chart_url(row['name_raw']), use_container_width=True)
 
st.divider()
st.caption("본 프로그램은 TradingView·KRX·Yahoo Finance 공개 데이터를 활용하며 투자 권유를 목적으로 하지 않습니다.")
