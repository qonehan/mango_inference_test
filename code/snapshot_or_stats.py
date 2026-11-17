"""OpenRouter 모델 성능 페이지의 공개 통계를 주기적으로 저장한다 (프로토콜 v1.0 보조 기록).

저장하는 것 (모델마다, 매 스냅샷):
- 엔드포인트별 최근 30분 창 통계: p50~p99 지연·처리량, 요청 수 (`stats`)
- 제공사별 실효 입력·출력 단가와 캐시 적중률 (`providerSummaries`)
- 원본 HTML(gzip). 페이지 구조가 바뀌어도 나중에 다시 뽑을 수 있게 함

정렬 경로의 순위는 OpenRouter 자체 지표로 정해지므로, 같은 시각의 우리 실측과 비교하는 데 씀.

사용:
  python snapshot_or_stats.py --config config/run1.yaml --out runs/run1/or_stats --every-min 30 --until-utc 2026-11-18T06:00
  python snapshot_or_stats.py --config config/run1.yaml --out /tmp/x --once
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bench_common import load_config

UA = "mango-bench-snapshot/1.0"


def balanced(s: str, i: int) -> str:
    """s[i]가 '{' 또는 '['일 때 짝이 맞는 끝까지의 부분 문자열."""
    open_c = s[i]
    close_c = "}" if open_c == "{" else "]"
    depth, j, in_str, esc = 0, i, False, False
    while j < len(s):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
        j += 1
    return ""


def extract(html: str) -> dict:
    s = html.replace('\\"', '"')
    names = dict(re.findall(r'"id":"([0-9a-f\-]{36})","name":"([^"]+)"', s))
    stats = {}
    for m in re.finditer(r'"stats":\{"endpoint_id"', s):
        txt = balanced(s, m.start() + len('"stats":'))
        try:
            o = json.loads(txt)
        except json.JSONDecodeError:
            continue
        o["endpoint_name"] = names.get(o.get("endpoint_id"))
        stats[o["endpoint_id"]] = o
    summaries = []
    i = s.find('"providerSummaries":[')
    if i >= 0:
        try:
            summaries = json.loads(balanced(s, i + len('"providerSummaries":')))
        except json.JSONDecodeError:
            pass
    return {"endpoint_stats": list(stats.values()), "provider_summaries": summaries}


def snapshot(cfg: dict, out: Path) -> None:
    ts = datetime.now(timezone.utc)
    tag = ts.strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for mk, m in cfg["models"].items():
        url = f"https://openrouter.ai/{m['or_slug']}/performance"
        rec = {"t_utc": ts.timestamp(), "model_key": mk, "url": url}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            (out / "html").mkdir(parents=True, exist_ok=True)
            with gzip.open(out / "html" / f"{tag}_{mk}.html.gz", "wt", encoding="utf-8") as f:
                f.write(html)
            rec.update(extract(html))
            rec["n_endpoints"] = len(rec["endpoint_stats"])
        except Exception as e:  # noqa: BLE001  실패도 기록
            rec["error"] = str(e)[:300]
        rows.append(rec)
    with open(out / "or_stats.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(tag, {r["model_key"]: r.get("n_endpoints", r.get("error")) for r in rows}, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every-min", type=float, default=30)
    ap.add_argument("--until-utc", default="")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    until = datetime.fromisoformat(a.until_utc).replace(tzinfo=timezone.utc).timestamp() if a.until_utc else None
    while True:
        snapshot(cfg, out)
        if a.once or (until and time.time() + a.every_min * 60 > until):
            break
        time.sleep(a.every_min * 60)


if __name__ == "__main__":
    main()
