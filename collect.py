#!/usr/bin/env python3
"""청약홈 분양/무순위 → 수도권 신규 후보를 JSON으로 출력 (결정론적 수집).

엔드포인트/필드를 고정해 매 실행 재탐색을 없앤다. 에이전트는 이 출력(JSON)만
받아 정성평가 + 다이제스트 + 발송에 집중한다.

사용법:
    DATA_GO_KR_KEY=xxx python collect.py [--days 3]
환경변수:
    DATA_GO_KR_KEY  공공데이터포털 서비스키
출력:
    stdout에 JSON {"as_of": "YYYY-MM-DD", "items": [...]}  (UTF-8)
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")  # 로컬 콘솔 인코딩 무관하게
except Exception:
    pass

_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
_KST = timezone(timedelta(hours=9))
_CAPITAL = {"서울", "경기", "인천"}  # SUBSCRPT_AREA_CODE_NM 값


def _fetch(operation: str, key: str, date_from: str) -> list[dict]:
    """공고일(RCRIT_PBLANC_DE) >= date_from 인 건을 페이지네이션으로 수집."""
    out, page = [], 1
    while True:
        params = urllib.parse.urlencode({
            "page": page,
            "perPage": 100,
            "serviceKey": key,
            "cond[RCRIT_PBLANC_DE::GTE]": date_from,
        })
        url = f"{_BASE}/{operation}?{params}"
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("data") or []
        out.extend(rows)
        if len(rows) < 100:
            break
        page += 1
        if page > 20:  # 안전장치
            break
    return out


def _norm(row: dict, kind: str) -> dict:
    """공통 스키마로 정규화. 분양/무순위 접수일 필드가 달라 분기."""
    if kind == "분양":
        rcept_bgn = row.get("RCEPT_BGNDE")
        rcept_end = row.get("RCEPT_ENDDE")
    else:  # 무순위/잔여세대
        rcept_bgn = row.get("SUBSCRPT_RCEPT_BGNDE") or row.get("GNRL_RCEPT_BGNDE")
        rcept_end = row.get("SUBSCRPT_RCEPT_ENDDE") or row.get("GNRL_RCEPT_ENDDE")
    return {
        "유형": kind,
        "단지명": row.get("HOUSE_NM"),
        "지역": row.get("SUBSCRPT_AREA_CODE_NM"),
        "주소": row.get("HSSPLY_ADRES"),
        "주택구분": row.get("HOUSE_DTL_SECD_NM") or row.get("HOUSE_SECD_NM"),
        "모집공고일": row.get("RCRIT_PBLANC_DE"),
        "접수시작": rcept_bgn,
        "접수종료": rcept_end,
        "공급세대수": row.get("TOT_SUPLY_HSHLDCO"),
        "입주예정월": row.get("MVN_PREARNGE_YM"),
        "공고URL": row.get("PBLANC_URL"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="공고일 기준 최근 N일")
    args = ap.parse_args()

    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        print("ERROR: DATA_GO_KR_KEY 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(2)

    today = datetime.now(_KST).date()
    date_from = (today - timedelta(days=args.days)).isoformat()

    items = []
    for op, kind in (("getAPTLttotPblancDetail", "분양"),
                     ("getRemndrLttotPblancDetail", "무순위")):
        try:
            for row in _fetch(op, key, date_from):
                if row.get("SUBSCRPT_AREA_CODE_NM") in _CAPITAL:
                    items.append(_norm(row, kind))
        except Exception as e:
            print(f"WARN: {op} 수집 실패: {e}", file=sys.stderr)

    # 접수종료가 이미 지난 건 제외(아직 청약 가능/임박만)
    today_s = today.isoformat()
    items = [x for x in items if not x["접수종료"] or x["접수종료"] >= today_s]

    print(json.dumps({"as_of": today_s, "count": len(items), "items": items},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
