"""
Portfolio — 지수기반 종목분석 자동 업데이트
================================================
GitHub Actions에서 자동 실행되는 스크립트.

환경변수 (GitHub Secrets에서 설정):
  NOTION_TOKEN  : 노션 Integration 토큰
  GITHUB_TOKEN  : GitHub PAT (repo 권한) — Actions에서 자동 제공
  GITHUB_REPO   : 레포 이름 (예: username/swing-portfolio-charts)
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
import matplotlib.font_manager as fm
import matplotlib.ticker as mtick
from notion_client import Client

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# 1. 환경변수에서 설정값 로드
# ─────────────────────────────────────────────
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ["GITHUB_REPO"]
GITHUB_BRANCH  = "main"
GITHUB_FOLDER  = "charts"

# 노션 페이지 / DB ID
PAGE_PORTFOLIO = "37e2fdd1299881b58b19c4d63105e234"
DB_관심종목    = "1fdfe95bd1064b258719fa25e3361f77"

notion = Client(auth=NOTION_TOKEN)

def github_raw_url(filename: str) -> str:
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FOLDER}/{filename}"

print("=" * 55)
print("  📈 SWING Portfolio 자동 업데이트 시작")
print(f"  실행 시각: {datetime.today().strftime('%Y-%m-%d %H:%M')}")
print("=" * 55)

# ─────────────────────────────────────────────
# 2. 한국어 폰트 설정
# ─────────────────────────────────────────────
fm.fontManager.__init__()
cjk = [f.fname for f in fm.fontManager.ttflist
       if any(k in f.name for k in ["Nanum", "NotoSansCJK", "Malgun", "AppleGothic"])]
if cjk:
    plt.rcParams["font.family"] = fm.FontProperties(fname=cjk[0]).get_name()
    print(f"[폰트] {cjk[0]}")
plt.rcParams["axes.unicode_minus"] = False

# ─────────────────────────────────────────────
# 3. 관심종목 & 기준지수 정의
# ─────────────────────────────────────────────
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

INDEX_TICKERS = {
    "코스피200": "^KS200",
    "S&P500":   "^GSPC",
    "나스닥100": "^NDX",
}
INDEX_COLORS = {
    "코스피200": "#4A90E2",
    "S&P500":   "#52C41A",
    "나스닥100": "#9B59B6",
}
STOCK_PALETTE = [
    "#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF", "#FF922B",
    "#CC5DE8", "#20C997", "#F06595", "#74C0FC", "#A9E34B",
]
CHART_FILES = {
    "코스피200": "chart_kospi200.png",
    "S&P500":   "chart_snp500.png",
    "나스닥100": "chart_nasdaq100.png",
}

# ─────────────────────────────────────────────
# 4. 주가 데이터 수집
# ─────────────────────────────────────────────
def get_monthly_returns(ticker_yf: str, months: int = 6) -> pd.Series:
    end   = datetime.today()
    start = end - relativedelta(months=months + 1)
    try:
        df = yf.download(
            ticker_yf,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1mo", progress=False, auto_adjust=True
        )
        if df.empty:
            return pd.Series(dtype=float)
        close = df["Close"].squeeze()
        ret = close.pct_change().dropna().tail(months)
        ret.index = pd.to_datetime(ret.index).strftime("%Y-%m")
        return ret
    except Exception as e:
        print(f"  [경고] {ticker_yf}: {e}")
        return pd.Series(dtype=float)

def total_ret(s: pd.Series) -> float:
    return float(np.prod(1 + s) - 1) if not s.empty else 0.0

print("\n📡 기준지수 데이터 수집 중...")
idx_monthly, idx_6m = {}, {}
for idx_name, idx_ticker in INDEX_TICKERS.items():
    ret = get_monthly_returns(idx_ticker)
    idx_monthly[idx_name] = ret
    idx_6m[idx_name]      = total_ret(ret)
    print(f"  {idx_name}: 6M={idx_6m[idx_name]:+.1%}  ({len(ret)}개월)")

print("\n📡 관심종목 데이터 수집 중...")
stk_monthly, stk_6m = {}, {}
for name, ticker, idx_name, ticker_yf in WATCHLIST:
    ret = get_monthly_returns(ticker_yf)
    stk_monthly[ticker] = ret
    stk_6m[ticker]      = total_ret(ret)
    print(f"  {name} ({ticker_yf}): 6M={stk_6m[ticker]:+.1%}")

# ─────────────────────────────────────────────
# 5. 판정 계산
# ─────────────────────────────────────────────
def judge(stock_ret: float, index_ret: float) -> str:
    diff = stock_ret - index_ret
    if diff < -0.10:  return "🔴 손절검토"
    elif diff >= 0:   return "✅ 지수초과"
    else:             return "⚠️ 지수추종"

results = []
for name, ticker, idx_name, _ in WATCHLIST:
    sr = stk_6m.get(ticker, 0.0)
    ir = idx_6m.get(idx_name, 0.0)
    d  = sr - ir
    results.append({
        "종목이름":       name,
        "티커":          ticker,
        "기준지수":       idx_name,
        "6개월수익률":    sr,
        "지수6개월수익률": ir,
        "지수대비수익률":  d,
        "판정":          judge(sr, ir),
    })

df_result = pd.DataFrame(results)

print("\n=== 판정 결과 ===")
for _, r in df_result.iterrows():
    print(f"  {r['종목이름']:<12} {r['기준지수']:<8} "
          f"종목:{r['6개월수익률']:+.1%}  지수:{r['지수6개월수익률']:+.1%}  "
          f"대비:{r['지수대비수익률']:+.1%}  {r['판정']}")

# ─────────────────────────────────────────────
# 6. 차트 생성
# ─────────────────────────────────────────────
def make_chart(idx_name: str, save_path: str) -> bool:
    grp = df_result[df_result["기준지수"] == idx_name].reset_index(drop=True)
    if grp.empty:
        return False

    idx_ret = idx_monthly[idx_name]
    all_m   = set(idx_ret.index)
    for _, row in grp.iterrows():
        all_m |= set(stk_monthly.get(row["티커"], pd.Series()).index)
    months = sorted(all_m)[-6:]

    fig, ax = plt.subplots(figsize=(13, 6.8), facecolor="#0F1117")
    ax.set_facecolor("#0F1117")

    # 기준지수 라인
    idx_vals = [idx_ret.get(m, np.nan) * 100 for m in months]
    ax.plot(months, idx_vals,
            color=INDEX_COLORS[idx_name], lw=3.2, ls="--", marker="D", ms=8,
            label=f"▶ {idx_name} (기준지수)  6M:{idx_6m[idx_name]:+.1%}", zorder=10)

    # 손절 경계선 (기준지수 -10%p)
    valid = [v for v in idx_vals if not np.isnan(v)]
    floor = (min(valid) - 15) if valid else -20
    base  = [v - 10 if not np.isnan(v) else np.nan for v in idx_vals]
    ax.fill_between(months, base, floor, alpha=0.14, color="#FF4444",
                    label="손절검토 구간 (기준지수 -10%p)")
    ax.plot(months, base, color="#FF4444", lw=1.3, ls=":", alpha=0.75)

    # 종목별 라인
    for i, (_, row) in enumerate(grp.iterrows()):
        tk   = row["티커"]
        name = row["종목이름"]
        vd   = row["판정"]
        s    = stk_monthly.get(tk, pd.Series())
        vals = [s.get(m, np.nan) * 100 for m in months]
        c    = STOCK_PALETTE[i % len(STOCK_PALETTE)]
        ax.plot(months, vals, color=c, lw=2.0, marker="o", ms=5, alpha=0.92,
                label=f"{name} ({tk})  {vd}  6M:{row['6개월수익률']:+.1%}  대비:{row['지수대비수익률']:+.1%}")

    ax.axhline(0, color="#555", lw=0.9)
    ax.set_title(f"{idx_name} 기준 — 관심종목 월별 수익률 비교 (최근 6개월)",
                 fontsize=15, fontweight="bold", color="white", pad=18)
    ax.set_xlabel("월", color="#9CA3AF", fontsize=11)
    ax.set_ylabel("월별 수익률 (%)", color="#9CA3AF", fontsize=11)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.1f%%"))
    ax.tick_params(colors="#9CA3AF", labelsize=9.5)
    for sp in ax.spines.values():
        sp.set_edgecolor("#374151")
    ax.grid(axis="y", color="#1F2937", lw=0.7, ls="--")
    ax.grid(axis="x", color="#1F2937", lw=0.4, ls=":")

    legend = ax.legend(
        loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9.2,
        frameon=True, facecolor="#1C1F26", edgecolor="#374151",
        labelcolor="white", borderpad=0.9, labelspacing=0.6
    )
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

print("\n🎨 차트 생성 중...")
CHART_PATHS = {}
for idx_name, filename in CHART_FILES.items():
    path = f"/tmp/{filename}"
    if make_chart(idx_name, path):
        CHART_PATHS[idx_name] = path
        print(f"  ✅ {idx_name} 차트 생성 완료")

# ─────────────────────────────────────────────
# 7. GitHub에 차트 이미지 업로드
# ─────────────────────────────────────────────
def upload_github(local_path: str, filename: str) -> str | None:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FOLDER}/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
    }

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    # 기존 파일 SHA 조회 (덮어쓰기용)
    sha  = None
    resp = requests.get(api_url, headers=headers, timeout=15)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload = {
        "message": f"차트 업데이트: {filename} ({datetime.today().strftime('%Y-%m-%d %H:%M')})",
        "content": content_b64,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        raw_url = github_raw_url(filename)
        print(f"  ✅ {filename} → {raw_url}")
        return raw_url
    else:
        print(f"  ❌ 업로드 실패: {resp.status_code} / {resp.json().get('message')}")
        return None

print("\n📤 GitHub 업로드 중...")
GITHUB_URLS = {}
for idx_name, local_path in CHART_PATHS.items():
    url = upload_github(local_path, CHART_FILES[idx_name])
    if url:
        GITHUB_URLS[idx_name] = url

# ─────────────────────────────────────────────
# 8. 노션 관심종목 DB 업데이트
# ─────────────────────────────────────────────
def query_all(db_id: str) -> list:
    results, cursor = [], None
    while True:
        payload = {"database_id": db_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = notion.databases.query(**payload)
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return results

print("\n📝 노션 관심종목 DB 업데이트 중...")

existing       = query_all(DB_관심종목)
ticker_to_page = {}
for row in existing:
    rt = row["properties"].get("티커", {})
    tk = rt["rich_text"][0]["plain_text"] if rt.get("rich_text") else ""
    if tk:
        ticker_to_page[tk] = row["id"]

now_str = datetime.today().strftime("%Y-%m-%d %H:%M")

for _, r in df_result.iterrows():
    ticker  = r["티커"]
    page_id = ticker_to_page.get(ticker)
    if not page_id:
        print(f"  [경고] {ticker} 페이지를 찾을 수 없음")
        continue
    notion.pages.update(
        page_id=page_id,
        properties={
            "6개월수익률":    {"number": round(r["6개월수익률"], 4)},
            "지수6개월수익률": {"number": round(r["지수6개월수익률"], 4)},
            "지수대비수익률":  {"number": round(r["지수대비수익률"], 4)},
            "판정":          {"select": {"name": r["판정"]}},
            "최근업데이트":   {"rich_text": [{"text": {"content": now_str}}]},
        }
    )
    print(f"  ✏️  {r['종목이름']} ({ticker}) → {r['판정']}")

# ─────────────────────────────────────────────
# 9. 노션 페이지에 분석표 + 차트 삽입
#    블록 삭제 없이 append만 수행
# ─────────────────────────────────────────────
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

def update_image_block(block_id: str, url: str) -> bool:
    resp = requests.patch(
        f"{BASE_URL}/blocks/{block_id}",
        headers=NOTION_HEADERS,
        json={"image": {"type": "external", "external": {"url": url}}},
        timeout=20,
    )
    return resp.status_code == 200

# ── 블록 빌더 ──
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

def b_table(df_r: pd.DataFrame) -> dict:
    def pct(v): return f"{v:+.1%}"
    def cell(text, bold=False, color="default", code=False):
        return [{"type":"text","text":{"content":text},
                 "annotations":{"bold":bold,"color":color,"code":code}}]
    headers = ["종목이름","티커","기준지수","종목 6M수익률","지수 6M수익률","지수대비","판정"]
    rows = [{"type":"table_row","table_row":{
        "cells":[cell(h, bold=True) for h in headers]}}]
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

print("\n🏗️  노션 페이지 업데이트 중...")
UPDATE_TS = datetime.today().strftime("%Y-%m-%d %H:%M")

# 기존 "지수기반 종목분석" 섹션 탐색
blocks     = get_blocks(PAGE_PORTFOLIO)
section_id = None
img_block_ids = {}   # {idx_name: block_id}

for i, b in enumerate(blocks):
    btype = b.get("type", "")
    if btype in ("heading_2", "heading_3"):
        rich = b[btype].get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)
        if "지수기반 종목분석" in text:
            section_id = b["id"]
        # 차트 헤딩 찾기
        for idx_name in INDEX_TICKERS:
            if idx_name in text and "차트" in text:
                # 다음 블록이 이미지면 ID 저장
                if i + 1 < len(blocks) and blocks[i+1].get("type") == "image":
                    img_block_ids[idx_name] = blocks[i+1]["id"]

# 이미지 블록이 이미 있으면 URL만 업데이트 (블록 재삽입 불필요)
updated_imgs = []
for idx_name, block_id in img_block_ids.items():
    if idx_name in GITHUB_URLS:
        if update_image_block(block_id, GITHUB_URLS[idx_name]):
            updated_imgs.append(idx_name)
            print(f"  🔄 {idx_name} 차트 이미지 URL 갱신")

# 아직 삽입되지 않은 섹션이면 새로 append
if not section_id:
    new_blocks = [
        b_divider(),
        b_h2("🔬 지수기반 종목분석"),
        b_callout(
            f"기준지수 대비 6개월 월별 수익률 비교 분석  |  마지막 업데이트: {UPDATE_TS}\n"
            "판정 기준:  지수대비 -10%p 이하 → 🔴 손절검토  /  0%p~-10%p → ⚠️ 지수추종  /  +0%p 초과 → ✅ 지수초과",
            "🔬"
        ),
        b_h3("📋 지수 분석 대상 리스트"),
        b_table(df_result),
    ]
    for idx_name in ["코스피200", "S&P500", "나스닥100"]:
        new_blocks.append(b_h3(f"📈 {idx_name} 기준 — 월별 수익률 비교 차트"))
        if idx_name in GITHUB_URLS:
            new_blocks.append(b_image(GITHUB_URLS[idx_name]))

    ok = append_blocks(PAGE_PORTFOLIO, new_blocks)
    print(f"  {'✅ 신규 섹션 삽입 완료' if ok else '❌ 삽입 실패'}")
else:
    print(f"  ✅ 기존 섹션 이미지 갱신 완료 ({len(updated_imgs)}장)")

# ─────────────────────────────────────────────
# 10. 완료
# ─────────────────────────────────────────────
print("\n" + "=" * 55)
print("  🎉 모든 업데이트 완료!")
print(f"  노션: https://app.notion.com/p/{PAGE_PORTFOLIO}")
print("=" * 55)
