"""
SWING Portfolio — GitHub Actions 자동 업데이트
================================================
수행 작업:
  1. 매매일지 DB → 보유주식 DB 업데이트
  2. 보유주식 DB → 총자산 DB 업데이트
  3. 보유주식 분류별 파이차트 생성 → GitHub 업로드 → 노션 삽입
  4. 관심종목 6개월 월별수익률 분석 → 차트 3장 → GitHub 업로드 → 노션 삽입
  5. 노션 페이지 업데이트 시각 갱신

환경변수 (GitHub Secrets):
  NOTION_TOKEN : 노션 Integration 토큰
  GITHUB_TOKEN : 자동 제공 (Actions)
  GITHUB_REPO  : 자동 제공 (Actions) — github.repository
"""

import os, base64, warnings
from datetime import datetime
from dateutil.relativedelta import relativedelta

import requests
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
import matplotlib.ticker as mtick
from notion_client import Client

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# 0. 환경변수 로드
# ─────────────────────────────────────────────
NOTION_TOKEN  = os.environ["NOTION_TOKEN"]
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = "main"
GITHUB_FOLDER = "charts"

# 노션 ID
PAGE_PORTFOLIO = "37e2fdd1299881b58b19c4d63105e234"
DB_총자산      = "b8c9d6d53be24cab88b62dfde3edd658"
DB_보유주식    = "041399d92e4a46a8a0419208fcea51b5"
DB_매매일지    = "569831e32ee34e78a2743a35e655a93f"
DB_관심종목    = "1fdfe95bd1064b258719fa25e3361f77"

notion   = Client(auth=NOTION_TOKEN)
NOW_STR  = datetime.today().strftime("%Y-%m-%d %H:%M")
TODAY    = datetime.today().strftime("%Y-%m-%d")

print("=" * 55)
print("  📈 SWING Portfolio 자동 업데이트 시작")
print(f"  실행 시각: {NOW_STR}")
print("=" * 55)

# ─────────────────────────────────────────────
# 1. 공통 유틸
# ─────────────────────────────────────────────
def setup_font():
    fm.fontManager.__init__()
    cjk = [f.fname for f in fm.fontManager.ttflist
           if any(k in f.name for k in ["Nanum", "NotoSansCJK", "Malgun", "AppleGothic"])]
    if cjk:
        plt.rcParams["font.family"] = fm.FontProperties(fname=cjk[0]).get_name()
        print(f"[폰트] {cjk[0]}")
    plt.rcParams["axes.unicode_minus"] = False

setup_font()

def query_all(db_id: str, filter_obj: dict = None) -> list:
    results, cursor = [], None
    while True:
        payload = {"database_id": db_id, "page_size": 100}
        if cursor:      payload["start_cursor"] = cursor
        if filter_obj:  payload["filter"] = filter_obj
        resp = notion.databases.query(**payload)
        results.extend(resp["results"])
        if not resp.get("has_more"): break
        cursor = resp["next_cursor"]
    return results

def get_prop_text(prop: dict) -> str:
    t = prop.get("type", "")
    if t == "title":     return prop["title"][0]["plain_text"] if prop["title"] else ""
    if t == "rich_text": return prop["rich_text"][0]["plain_text"] if prop["rich_text"] else ""
    return ""

def get_prop_num(prop: dict) -> float:
    return prop.get("number") or 0.0

def get_prop_select(prop: dict) -> str:
    return prop["select"]["name"] if prop.get("select") else ""

# GitHub 업로드
def github_raw_url(filename: str) -> str:
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FOLDER}/{filename}"

def upload_github(local_path: str, filename: str) -> str | None:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FOLDER}/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
    }
    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    sha  = None
    resp = requests.get(api_url, headers=headers, timeout=15)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload = {
        "message": f"차트 업데이트: {filename} ({NOW_STR})",
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        url = github_raw_url(filename)
        print(f"  ✅ GitHub 업로드: {filename}")
        return url
    else:
        print(f"  ❌ 업로드 실패: {resp.status_code} / {resp.json().get('message')}")
        return None

# 노션 API 헬퍼
NOTION_HEADERS = {
    "Authorization":  f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type":   "application/json",
}
BASE_URL = "https://api.notion.com/v1"

def get_blocks(page_id: str) -> list:
    resp = requests.get(
        f"{BASE_URL}/blocks/{page_id}/children?page_size=100",
        headers=NOTION_HEADERS, timeout=20
    )
    return resp.json().get("results", [])

def append_blocks(page_id: str, children: list) -> bool:
    resp = requests.patch(
        f"{BASE_URL}/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": children},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [오류] {resp.status_code}: {resp.text[:300]}")
    return resp.status_code == 200

def update_block(block_id: str, payload: dict) -> bool:
    resp = requests.patch(
        f"{BASE_URL}/blocks/{block_id}",
        headers=NOTION_HEADERS,
        json=payload, timeout=20,
    )
    return resp.status_code == 200

def delete_block(block_id: str) -> bool:
    resp = requests.delete(
        f"{BASE_URL}/blocks/{block_id}",
        headers=NOTION_HEADERS, timeout=20,
    )
    return resp.status_code == 200

# 블록 빌더
def b_divider(): return {"object":"block","type":"divider","divider":{}}
def b_h2(text): return {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":text}}]}}
def b_h3(text): return {"object":"block","type":"heading_3","heading_3":{"rich_text":[{"type":"text","text":{"content":text}}]}}
def b_callout(text, emoji="📌"):
    return {"object":"block","type":"callout","callout":{
        "rich_text":[{"type":"text","text":{"content":text}}],
        "icon":{"type":"emoji","emoji":emoji}}}
def b_image(url):
    return {"object":"block","type":"image",
            "image":{"type":"external","external":{"url":url}}}
def b_para(text, color="default"):
    return {"object":"block","type":"paragraph","paragraph":{
        "rich_text":[{"type":"text","text":{"content":text},"annotations":{"color":color}}]}}

# ─────────────────────────────────────────────
# 2. 매매일지 → 보유주식 업데이트
# ─────────────────────────────────────────────
print("\n── 1단계: 매매일지 → 보유주식 업데이트 ──")

trades = query_all(DB_매매일지)
holdings: dict[str, dict] = {}

for t in sorted(trades, key=lambda x: get_prop_text(x["properties"]["날짜"])):
    p      = t["properties"]
    ticker = get_prop_text(p["티커"])
    name   = get_prop_text(p["종목이름"])
    action = get_prop_select(p["매수매도"])
    qty    = int(get_prop_num(p["수량"]))
    price  = get_prop_num(p["단가"])
    cat    = get_prop_select(p["분류"])

    if ticker not in holdings:
        holdings[ticker] = {"name": name, "qty": 0, "cost": 0.0, "cat": cat}

    if action == "매수":
        prev_qty  = holdings[ticker]["qty"]
        prev_cost = holdings[ticker]["cost"]
        new_qty   = prev_qty + qty
        holdings[ticker]["cost"] = (
            (prev_cost * prev_qty + price * qty) / new_qty if new_qty else 0
        )
        holdings[ticker]["qty"] = new_qty
    elif action == "매도":
        holdings[ticker]["qty"] = max(0, holdings[ticker]["qty"] - qty)

# 기존 보유주식 레코드
existing_holdings = query_all(DB_보유주식)
holding_map = {}
for row in existing_holdings:
    tk = get_prop_text(row["properties"]["티커"])
    holding_map[tk] = row["id"]

for ticker, h in holdings.items():
    avg_price = round(h["cost"])
    qty       = h["qty"]
    eval_amt  = avg_price * qty
    profit    = 0
    profit_r  = 0.0

    props = {
        "종목이름": {"title": [{"text": {"content": h["name"]}}]},
        "티커":     {"rich_text": [{"text": {"content": ticker}}]},
        "보유수량": {"number": qty},
        "매입가":   {"number": avg_price},
        "평가금액": {"number": eval_amt},
        "수익":     {"number": profit},
        "수익률":   {"number": profit_r},
        "분류":     {"select": {"name": h["cat"]}},
    }

    if ticker in holding_map:
        notion.pages.update(page_id=holding_map[ticker], properties=props)
        print(f"  ✏️  보유주식 업데이트: {h['name']} ({ticker})")
    else:
        notion.pages.create(parent={"database_id": DB_보유주식}, properties=props)
        print(f"  ➕ 보유주식 추가: {h['name']} ({ticker})")

# ─────────────────────────────────────────────
# 3. 보유주식 → 총자산 업데이트
# ─────────────────────────────────────────────
print("\n── 2단계: 보유주식 → 총자산 업데이트 ──")

rows_holding = query_all(DB_보유주식)
total_eval   = sum(get_prop_num(r["properties"]["평가금액"]) for r in rows_holding)
total_profit = sum(get_prop_num(r["properties"]["수익"])     for r in rows_holding)
total_cost   = sum(
    get_prop_num(r["properties"]["매입가"]) * get_prop_num(r["properties"]["보유수량"])
    for r in rows_holding
)
profit_rate  = (total_profit / total_cost) if total_cost else 0.0

notion.pages.create(
    parent={"database_id": DB_총자산},
    properties={
        "작성일자":   {"title": [{"text": {"content": TODAY}}]},
        "총평가금액": {"number": round(total_eval)},
        "총수익":     {"number": round(total_profit)},
        "총수익률":   {"number": round(profit_rate, 4)},
    }
)
print(f"  총평가금액: {total_eval:,.0f}원 | 총수익: {total_profit:,.0f}원 | 수익률: {profit_rate:.2%}")

# ─────────────────────────────────────────────
# 4. 보유주식 분류별 파이차트
# ─────────────────────────────────────────────
print("\n── 3단계: 보유주식 분류별 파이차트 ──")

COLOR_MAP = {
    "국내종목":     ("#4A90E2", "#2E6CC7"),
    "국내ETF":      ("#52C41A", "#389E0D"),
    "국내ETF-해외": ("#722ED1", "#531DAB"),
    "해외종목":     ("#FA8C16", "#D46B08"),
    "해외ETF":      ("#F5222D", "#CF1322"),
}

cat_sum: dict[str, float] = {}
for row in rows_holding:
    cat    = get_prop_select(row["properties"]["분류"])
    amount = get_prop_num(row["properties"]["평가금액"])
    cat_sum[cat] = cat_sum.get(cat, 0) + amount

cat_sum = {k: v for k, v in sorted(cat_sum.items(), key=lambda x: -x[1]) if v > 0}
total_pie = sum(cat_sum.values())

labels      = list(cat_sum.keys())
values      = list(cat_sum.values())
ratios      = [v / total_pie * 100 for v in values]
colors      = [COLOR_MAP.get(l, ("#999", "#666"))[0] for l in labels]
edge_colors = [COLOR_MAP.get(l, ("#999", "#666"))[1] for l in labels]

fig, ax = plt.subplots(figsize=(9, 6.5), facecolor="#0F1117")
ax.set_facecolor("#0F1117")

wedges, _, autotexts = ax.pie(
    values, labels=None,
    autopct=lambda p: f"{p:.1f}%",
    startangle=90, colors=colors,
    wedgeprops=dict(width=0.58, edgecolor="#0F1117", linewidth=2.5),
    pctdistance=0.75,
)
for at in autotexts:
    at.set_fontsize(13); at.set_fontweight("bold"); at.set_color("white")

ax.text(0,  0.08, "총 평가금액", ha="center", va="center", fontsize=11, color="#9CA3AF")
ax.text(0, -0.12, f"{total_pie:,.0f}원", ha="center", va="center",
        fontsize=14, fontweight="bold", color="white")
ax.set_title("보유주식 분류별 비율", fontsize=17, fontweight="bold", color="white", pad=22)

legend_items = [
    mpatches.Patch(facecolor=colors[i], edgecolor=edge_colors[i], linewidth=1.5,
                   label=f"{labels[i]}  |  {values[i]:,.0f}원  ({ratios[i]:.1f}%)")
    for i in range(len(labels))
]
ax.legend(handles=legend_items, loc="lower center", bbox_to_anchor=(0.5, -0.13),
          ncol=1, fontsize=11, frameon=True, facecolor="#1C1F26", edgecolor="#374151",
          labelcolor="white", handlelength=1.5, handleheight=1.2,
          borderpad=0.8, labelspacing=0.6)
ax.add_patch(plt.Circle((0, 0), 0.21, color="#1C1F26", zorder=10))

fig.tight_layout(rect=[0, 0.05, 1, 1])
PIE_PATH = "/tmp/chart_pie.png"
fig.savefig(PIE_PATH, dpi=180, bbox_inches="tight", facecolor="#0F1117", edgecolor="none")
plt.close()
print(f"  파이차트 생성 완료")

PIE_URL = upload_github(PIE_PATH, "chart_pie.png")

# ─────────────────────────────────────────────
# 5. 관심종목 지수기반 분석 차트
# ─────────────────────────────────────────────
print("\n── 4단계: 관심종목 지수기반 분석 ──")

WATCHLIST = [
    ("테슬라",        "TSLA",   "나스닥100", "TSLA"),
    ("구글(알파벳)",  "GOOG",   "나스닥100", "GOOG"),
    ("엔비디아",      "NVDA",   "나스닥100", "NVDA"),
    ("SK하이닉스",   "000660",  "코스피200", "000660.KS"),
    ("현대자동차",   "005380",  "코스피200", "005380.KS"),
    ("삼성전자",     "005930",  "코스피200", "005930.KS"),
    ("삼성전자우",   "005935",  "코스피200", "005935.KS"),
    ("삼성전기",     "009150",  "코스피200", "009150.KS"),
    ("타이거200",    "102110",  "코스피200", "102110.KS"),
    ("월마트",       "WMT",     "S&P500",   "WMT"),
    ("존슨앤드존슨", "JNJ",     "S&P500",   "JNJ"),
    ("코카콜라",     "KO",      "S&P500",   "KO"),
]
INDEX_TICKERS = {"코스피200": "^KS200", "S&P500": "^GSPC", "나스닥100": "^NDX"}
INDEX_COLORS  = {"코스피200": "#4A90E2", "S&P500": "#52C41A", "나스닥100": "#9B59B6"}
STOCK_PALETTE = ["#FF6B6B","#FFD93D","#6BCB77","#4D96FF","#FF922B",
                 "#CC5DE8","#20C997","#F06595","#74C0FC","#A9E34B"]
CHART_FILES   = {
    "코스피200": "chart_kospi200.png",
    "S&P500":   "chart_snp500.png",
    "나스닥100": "chart_nasdaq100.png",
}

def get_monthly_returns(ticker_yf: str, months: int = 6) -> pd.Series:
    end   = datetime.today()
    start = end - relativedelta(months=months + 1)
    try:
        df = yf.download(ticker_yf, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         interval="1mo", progress=False, auto_adjust=True)
        if df.empty: return pd.Series(dtype=float)
        close = df["Close"].squeeze()
        ret   = close.pct_change().dropna().tail(months)
        ret.index = pd.to_datetime(ret.index).strftime("%Y-%m")
        return ret
    except Exception as e:
        print(f"  [경고] {ticker_yf}: {e}")
        return pd.Series(dtype=float)

def total_ret(s: pd.Series) -> float:
    return float(np.prod(1 + s) - 1) if not s.empty else 0.0

def judge(sr: float, ir: float) -> str:
    d = sr - ir
    if d < -0.10: return "🔴 손절검토"
    elif d >= 0:  return "✅ 지수초과"
    else:         return "⚠️ 지수추종"

print("  기준지수 데이터 수집 중...")
idx_monthly, idx_6m = {}, {}
for idx_name, idx_ticker in INDEX_TICKERS.items():
    ret = get_monthly_returns(idx_ticker)
    idx_monthly[idx_name] = ret
    idx_6m[idx_name]      = total_ret(ret)
    print(f"    {idx_name}: 6M={idx_6m[idx_name]:+.1%}")

print("  관심종목 데이터 수집 중...")
stk_monthly, stk_6m = {}, {}
for name, ticker, _, ticker_yf in WATCHLIST:
    ret = get_monthly_returns(ticker_yf)
    stk_monthly[ticker] = ret
    stk_6m[ticker]      = total_ret(ret)
    print(f"    {name}: 6M={stk_6m[ticker]:+.1%}")

results = []
for name, ticker, idx_name, _ in WATCHLIST:
    sr = stk_6m.get(ticker, 0.0)
    ir = idx_6m.get(idx_name, 0.0)
    results.append({
        "종목이름": name, "티커": ticker, "기준지수": idx_name,
        "6개월수익률": sr, "지수6개월수익률": ir,
        "지수대비수익률": sr - ir, "판정": judge(sr, ir),
    })
df_result = pd.DataFrame(results)

def make_index_chart(idx_name: str, save_path: str) -> bool:
    grp = df_result[df_result["기준지수"] == idx_name].reset_index(drop=True)
    if grp.empty: return False

    idx_ret = idx_monthly[idx_name]
    all_m   = set(idx_ret.index)
    for _, row in grp.iterrows():
        all_m |= set(stk_monthly.get(row["티커"], pd.Series()).index)
    months = sorted(all_m)[-6:]

    fig, ax = plt.subplots(figsize=(13, 6.8), facecolor="#0F1117")
    ax.set_facecolor("#0F1117")

    idx_vals = [idx_ret.get(m, np.nan) * 100 for m in months]
    ax.plot(months, idx_vals, color=INDEX_COLORS[idx_name], lw=3.2, ls="--",
            marker="D", ms=8,
            label=f"▶ {idx_name} (기준지수)  6M:{idx_6m[idx_name]:+.1%}", zorder=10)

    valid = [v for v in idx_vals if not np.isnan(v)]
    floor = (min(valid) - 15) if valid else -20
    base  = [v - 10 if not np.isnan(v) else np.nan for v in idx_vals]
    ax.fill_between(months, base, floor, alpha=0.14, color="#FF4444",
                    label="손절검토 구간 (기준지수 -10%p)")
    ax.plot(months, base, color="#FF4444", lw=1.3, ls=":", alpha=0.75)

    for i, (_, row) in enumerate(grp.iterrows()):
        tk   = row["티커"]
        s    = stk_monthly.get(tk, pd.Series())
        vals = [s.get(m, np.nan) * 100 for m in months]
        ax.plot(months, vals, color=STOCK_PALETTE[i % len(STOCK_PALETTE)],
                lw=2.0, marker="o", ms=5, alpha=0.92,
                label=f"{row['종목이름']} ({tk})  {row['판정']}  "
                      f"6M:{row['6개월수익률']:+.1%}  대비:{row['지수대비수익률']:+.1%}")

    ax.axhline(0, color="#555", lw=0.9)
    ax.set_title(f"{idx_name} 기준 — 관심종목 월별 수익률 비교 (최근 6개월)",
                 fontsize=15, fontweight="bold", color="white", pad=18)
    ax.set_xlabel("월", color="#9CA3AF", fontsize=11)
    ax.set_ylabel("월별 수익률 (%)", color="#9CA3AF", fontsize=11)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    ax.tick_params(colors="#9CA3AF", labelsize=9.5)
    for sp in ax.spines.values(): sp.set_edgecolor("#374151")
    ax.grid(axis="y", color="#1F2937", lw=0.7, ls="--")
    ax.grid(axis="x", color="#1F2937", lw=0.4, ls=":")

    legend = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9.2,
                       frameon=True, facecolor="#1C1F26", edgecolor="#374151",
                       labelcolor="white", borderpad=0.9, labelspacing=0.6)
    for t in legend.get_texts():
        tx = t.get_text()
        if "🔴" in tx:   t.set_color("#FF6B6B")
        elif "✅" in tx: t.set_color("#6BCB77")
        elif "⚠️" in tx: t.set_color("#FFD93D")
        elif "▶" in tx:  t.set_color(INDEX_COLORS[idx_name])

    fig.tight_layout(rect=[0, 0, 0.75, 1])
    fig.savefig(save_path, dpi=160, bbox_inches="tight",
                facecolor="#0F1117", edgecolor="none")
    plt.close()
    return True

print("  차트 생성 중...")
CHART_PATHS, GITHUB_URLS = {}, {}
for idx_name, filename in CHART_FILES.items():
    path = f"/tmp/{filename}"
    if make_index_chart(idx_name, path):
        CHART_PATHS[idx_name] = path
        url = upload_github(path, filename)
        if url: GITHUB_URLS[idx_name] = url

# 관심종목 DB 업데이트
print("  관심종목 DB 업데이트 중...")
existing_wl    = query_all(DB_관심종목)
ticker_to_page = {}
for row in existing_wl:
    rt = row["properties"].get("티커", {})
    tk = rt["rich_text"][0]["plain_text"] if rt.get("rich_text") else ""
    if tk: ticker_to_page[tk] = row["id"]

for _, r in df_result.iterrows():
    page_id = ticker_to_page.get(r["티커"])
    if not page_id: continue
    notion.pages.update(page_id=page_id, properties={
        "6개월수익률":    {"number": round(r["6개월수익률"], 4)},
        "지수6개월수익률": {"number": round(r["지수6개월수익률"], 4)},
        "지수대비수익률":  {"number": round(r["지수대비수익률"], 4)},
        "판정":          {"select": {"name": r["판정"]}},
        "최근업데이트":   {"rich_text": [{"text": {"content": NOW_STR}}]},
    })
    print(f"    {r['종목이름']} ({r['티커']}) → {r['판정']}")

# ─────────────────────────────────────────────
# 6. 노션 페이지 업데이트
# ─────────────────────────────────────────────
print("\n── 5단계: 노션 페이지 업데이트 ──")

def b_index_table(df_r: pd.DataFrame) -> dict:
    def pct(v): return f"{v:+.1%}"
    def cell(text, bold=False, color="default", code=False):
        return [{"type":"text","text":{"content":text},
                 "annotations":{"bold":bold,"color":color,"code":code}}]
    headers = ["종목이름","티커","기준지수","종목 6M수익률","지수 6M수익률","지수대비","판정"]
    rows = [{"type":"table_row","table_row":{"cells":[cell(h, bold=True) for h in headers]}}]
    for _, r in df_r.iterrows():
        vd = r["판정"]
        vc = "green" if "✅" in vd else ("red" if "🔴" in vd else "yellow")
        dc = "green" if r["지수대비수익률"] >= 0 else "red"
        rows.append({"type":"table_row","table_row":{"cells":[
            cell(r["종목이름"]),
            cell(r["티커"], code=True),
            cell(r["기준지수"]),
            cell(pct(r["6개월수익률"])),
            cell(pct(r["지수6개월수익률"])),
            cell(pct(r["지수대비수익률"]), bold=True, color=dc),
            cell(vd, color=vc),
        ]}})
    return {"object":"block","type":"table","table":{
        "table_width":7,"has_column_header":True,"has_row_header":False,
        "children":rows}}

# 현재 페이지 블록 전체 조회
all_blocks = get_blocks(PAGE_PORTFOLIO)

# 블록 ID 탐색
ts_block_id       = None   # 업데이트 시각 callout
pie_block_id      = None   # 파이차트 이미지
idx_section_id    = None   # 지수기반 종목분석 헤딩
idx_ts_block_id   = None   # 지수분석 업데이트 시각 callout
idx_img_ids       = {}     # {idx_name: block_id}

for i, b in enumerate(all_blocks):
    btype = b.get("type", "")
    # callout 텍스트 확인
    if btype == "callout":
        rich = b["callout"].get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)
        if "마지막 업데이트" in text and "지수" not in text:
            ts_block_id = b["id"]
        if "마지막 업데이트" in text and "지수" in text:
            idx_ts_block_id = b["id"]
    # 이미지 블록
    if btype == "image":
        url = b["image"].get("external", {}).get("url", "")
        if "chart_pie" in url:
            pie_block_id = b["id"]
        for idx_name, filename in CHART_FILES.items():
            if filename.replace(".png", "") in url:
                idx_img_ids[idx_name] = b["id"]
    # 헤딩
    if btype in ("heading_2", "heading_3"):
        rich = b[btype].get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)
        if "지수기반 종목분석" in text:
            idx_section_id = b["id"]

# ── 업데이트 시각 callout 갱신 또는 추가 ──
ts_text = f"🕐 마지막 업데이트: {NOW_STR} (KST)  |  GitHub Actions 자동 실행"

if ts_block_id:
    update_block(ts_block_id, {"callout": {
        "rich_text": [{"type":"text","text":{"content":ts_text}}],
        "icon": {"type":"emoji","emoji":"🕐"}
    }})
    print("  🕐 업데이트 시각 갱신")
else:
    # 페이지 맨 위에 시각 표시 추가
    append_blocks(PAGE_PORTFOLIO, [b_callout(ts_text, "🕐")])
    print("  🕐 업데이트 시각 블록 추가")

# ── 파이차트 이미지 갱신 또는 추가 ──
if PIE_URL:
    if pie_block_id:
        update_block(pie_block_id, {
            "image": {"type": "external", "external": {"url": PIE_URL}}
        })
        print("  🔄 파이차트 이미지 갱신")
    else:
        # 보유주식 섹션 찾아서 아래에 추가
        pie_blocks = [
            b_h3("📈 분류별 비율"),
            b_image(PIE_URL),
        ]
        append_blocks(PAGE_PORTFOLIO, pie_blocks)
        print("  ➕ 파이차트 섹션 추가")

# ── 지수기반 종목분석 섹션 갱신 또는 신규 삽입 ──
idx_ts_text = (
    f"기준지수 대비 6개월 월별 수익률 비교 분석\n"
    f"판정 기준: 지수대비 -10%p 이하 → 🔴 손절검토  /  0%p~-10%p → ⚠️ 지수추종  /  +0%p 초과 → ✅ 지수초과\n"
    f"🕐 마지막 업데이트: {NOW_STR} (KST)"
)

if idx_section_id:
    # callout 시각만 업데이트
    if idx_ts_block_id:
        update_block(idx_ts_block_id, {"callout": {
            "rich_text": [{"type":"text","text":{"content":idx_ts_text}}],
            "icon": {"type":"emoji","emoji":"🔬"}
        }})
        print("  🔄 지수분석 업데이트 시각 갱신")
    # 차트 이미지 URL 갱신
    for idx_name, block_id in idx_img_ids.items():
        if idx_name in GITHUB_URLS:
            update_block(block_id, {
                "image": {"type": "external", "external": {"url": GITHUB_URLS[idx_name]}}
            })
            print(f"  🔄 {idx_name} 차트 이미지 갱신")
else:
    # 섹션 전체 신규 삽입
    new_blocks = [
        b_divider(),
        b_h2("🔬 지수기반 종목분석"),
        b_callout(idx_ts_text, "🔬"),
        b_h3("📋 지수 분석 대상 리스트"),
        b_index_table(df_result),
    ]
    for idx_name in ["코스피200", "S&P500", "나스닥100"]:
        new_blocks.append(b_h3(f"📈 {idx_name} 기준 — 월별 수익률 비교 차트"))
        if idx_name in GITHUB_URLS:
            new_blocks.append(b_image(GITHUB_URLS[idx_name]))
    ok = append_blocks(PAGE_PORTFOLIO, new_blocks)
    print(f"  {'✅ 지수기반 종목분석 섹션 신규 삽입' if ok else '❌ 삽입 실패'}")

# ─────────────────────────────────────────────
# 7. 완료
# ─────────────────────────────────────────────
print("\n" + "=" * 55)
print("  🎉 모든 업데이트 완료!")
print(f"  노션: https://app.notion.com/p/{PAGE_PORTFOLIO}")
print("=" * 55)
