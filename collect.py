#!/usr/bin/env python3
"""청약홈 수집 → 두 버킷으로 정규화 JSON 출력 (결정론적).

- new_listings:  모집공고일이 최근 --new-days 일 이내(분양+무순위, 접수 미마감).
                 분양 건은 주택형별 분양가까지 enrich → "분양 정보 + 분석"용.
- upcoming:      접수시작이 오늘~+--soon-days 일 이내이거나 진행 중인 건(분양+무순위).
                 new_listings 와 중복 제외 → "접수 임박/진행 단순 알림"용.

엔드포인트·필드는 고정. 에이전트는 이 출력만 받아 정해진 템플릿으로 렌더링한다.

사용법: DATA_GO_KR_KEY=xxx python collect.py
환경변수: DATA_GO_KR_KEY
출력: stdout JSON (UTF-8)
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
_KST = timezone(timedelta(hours=9))
_CAPITAL = {"서울", "경기", "인천"}


def _get(operation: str, key: str, cond: dict | None = None, per_page: int = 100) -> list[dict]:
    import time
    out, page = [], 1
    while True:
        params = {"page": page, "perPage": per_page, "serviceKey": key}
        if cond:
            params.update(cond)
        url = f"{_BASE}/{operation}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        # data.go.kr 간헐적 4xx/5xx 대비 재시도
        data = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
        rows = (data or {}).get("data") or []
        out.extend(rows)
        if len(rows) < per_page:
            break
        page += 1
        if page > 30:
            break
    return out


def _norm(row: dict, kind: str) -> dict:
    if kind == "분양":
        bgn, end = row.get("RCEPT_BGNDE"), row.get("RCEPT_ENDDE")
    else:
        bgn = row.get("SUBSCRPT_RCEPT_BGNDE") or row.get("GNRL_RCEPT_BGNDE")
        end = row.get("SUBSCRPT_RCEPT_ENDDE") or row.get("GNRL_RCEPT_ENDDE")
    return {
        "id": f"{row.get('HOUSE_MANAGE_NO')}_{row.get('PBLANC_NO')}",
        "유형": kind,
        "단지명": row.get("HOUSE_NM"),
        "지역": row.get("SUBSCRPT_AREA_CODE_NM"),
        "주소": row.get("HSSPLY_ADRES"),
        "주택구분": row.get("HOUSE_DTL_SECD_NM") or row.get("HOUSE_SECD_NM"),
        "모집공고일": row.get("RCRIT_PBLANC_DE"),
        "접수시작": bgn,
        "접수종료": end,
        "공급세대수": row.get("TOT_SUPLY_HSHLDCO"),
        "입주예정월": row.get("MVN_PREARNGE_YM"),
        "공고URL": row.get("PBLANC_URL"),
        "_house_manage_no": row.get("HOUSE_MANAGE_NO"),
    }


def _prices(house_manage_no: str, key: str) -> list[dict]:
    """주택형별 분양가(최고가, 만원) → 전용면적/공급면적/분양가억 요약."""
    if not house_manage_no:
        return []
    rows = _get("getAPTLttotPblancMdl", key,
                {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no})
    out = []
    for r in rows:
        ty = str(r.get("HOUSE_TY") or "")
        try:
            exclusive = round(float(ty.split(".")[0])) if ty else None
        except ValueError:
            exclusive = None
        amt = r.get("LTTOT_TOP_AMOUNT")
        try:
            eok = round(int(amt) / 10000, 2) if amt not in (None, "") else None
        except ValueError:
            eok = None
        out.append({
            "전용": exclusive,
            "공급면적": r.get("SUPLY_AR"),
            "분양가억": eok,
            "일반공급세대": r.get("SUPLY_HSHLDCO"),
        })
    # 전용면적 오름차순
    out.sort(key=lambda x: (x["전용"] is None, x["전용"] or 0))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-days", type=int, default=7,
                    help="신규 후보=모집공고일 최근 N일. 발송이력(state/sent.json) 대조로 중복 제거")
    ap.add_argument("--soon-days", type=int, default=3, help="임박=접수시작 향후 N일")
    ap.add_argument("--lookback", type=int, default=60, help="공고 조회 lookback(일)")
    args = ap.parse_args()

    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        print("ERROR: DATA_GO_KR_KEY 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(2)

    today = datetime.now(_KST).date()
    today_s = today.isoformat()
    new_from = (today - timedelta(days=args.new_days)).isoformat()
    soon_to = (today + timedelta(days=args.soon_days)).isoformat()
    fetch_from = (today - timedelta(days=args.lookback)).isoformat()

    # 공고 lookback 윈도우로 수도권 분양/무순위 모두 수집
    rows = []
    for op, kind in (("getAPTLttotPblancDetail", "분양"),
                     ("getRemndrLttotPblancDetail", "무순위")):
        try:
            for r in _get(op, key, {"cond[RCRIT_PBLANC_DE::GTE]": fetch_from}):
                if r.get("SUBSCRPT_AREA_CODE_NM") in _CAPITAL:
                    rows.append(_norm(r, kind))
        except Exception as e:
            print(f"WARN: {op} 수집 실패: {e}", file=sys.stderr)

    # 접수 미마감만
    rows = [x for x in rows if not x["접수종료"] or x["접수종료"] >= today_s]

    # 이미 보낸 공고 id (state/sent.json) 로드 → 중복 제거
    sent_ids = set()
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "sent.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                sent_ids = set(json.load(f).get("sent", []))
        except (json.JSONDecodeError, OSError):
            pass

    new_listings, upcoming = [], []
    seen_new = set()
    for x in rows:
        pub = x["모집공고일"] or ""
        if pub >= new_from and x["id"] not in sent_ids:   # 신규 후보 - 미발송
            if x["유형"] == "분양":
                x["평형분양가"] = _prices(x["_house_manage_no"], key)
            x.pop("_house_manage_no", None)
            new_listings.append(x)
            seen_new.add((x["단지명"], x["지역"]))

    for x in rows:
        key2 = (x["단지명"], x["지역"])
        if key2 in seen_new:
            continue
        bgn = x["접수시작"] or ""
        end = x["접수종료"] or ""
        imminent = bool(bgn) and today_s <= bgn <= soon_to        # 곧 시작
        ongoing = bool(bgn) and bool(end) and bgn <= today_s <= end  # 진행 중
        if imminent or ongoing:
            upcoming.append({
                "유형": x["유형"], "단지명": x["단지명"], "지역": x["지역"],
                "접수시작": x["접수시작"], "접수종료": x["접수종료"],
                "공고URL": x["공고URL"],
                "상태": "진행중" if ongoing else "임박",
            })

    upcoming.sort(key=lambda x: x["접수시작"] or "")
    print(json.dumps({
        "as_of": today_s,
        "new_count": len(new_listings),
        "upcoming_count": len(upcoming),
        "new_listings": new_listings,
        "upcoming": upcoming,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
