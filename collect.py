#!/usr/bin/env python3
"""청약홈 OpenAPI 수집 (결정론적). pipeline.py가 import해서 쓴다.

- new_listings: 모집공고일이 최근 new_days일 이내이고 아직 안 보낸 건(분양·무순위·임의공급,
                접수 미마감). 주택형별 분양가까지 붙인다.
- upcoming:     접수시작이 오늘~+soon_days일 이내이거나 진행 중인 건. new_listings와 중복 제외.
- warnings:     엔드포인트 수집 실패 등. 놓친 건은 7일 윈도우 덕에 다음 실행에서 복구된다.

단독 실행(디버그용): DATA_GO_KR_KEY=xxx python3 collect.py
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
_CAPITAL = {"서울", "경기", "인천"}

# (상세 엔드포인트, 유형, 모집공고일 형식). 임의공급만 날짜가 YYYYMMDD.
# 무순위 엔드포인트는 무순위(04)와 불법행위 재공급(06)을 함께 준다.
_ENDPOINTS = (
    ("getAPTLttotPblancDetail", "분양", "dash"),
    ("getRemndrLttotPblancDetail", "무순위", "dash"),
    ("getOPTLttotPblancDetail", "임의공급", "compact"),
)
# 유형별 주택형(분양가) 엔드포인트
_MDL_OP = {
    "분양": "getAPTLttotPblancMdl",
    "무순위": "getRemndrLttotPblancMdl",
    "임의공급": "getOPTLttotPblancMdl",
}


def today_kst() -> date:
    return datetime.now(KST).date()


def _get(operation: str, key: str, cond: dict | None = None, per_page: int = 100) -> list[dict]:
    out, page = [], 1
    while True:
        params = {"page": page, "perPage": per_page, "serviceKey": key}
        if cond:
            params.update(cond)
        url = f"{_BASE}/{operation}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        data = None
        for attempt in range(3):  # data.go.kr 간헐적 4xx/5xx 대비
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
        if len(rows) < per_page or page >= 30:
            break
        page += 1
    return out


def _d(s) -> str | None:
    """날짜 정규화: 'YYYYMMDD'(임의공급) / 'YYYY-MM-DD'(분양·무순위) → 'YYYY-MM-DD'."""
    if not s:
        return None
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _norm(row: dict, kind: str) -> dict:
    if kind == "분양":
        bgn, end = row.get("RCEPT_BGNDE"), row.get("RCEPT_ENDDE")
    else:  # 무순위 / 임의공급
        bgn = row.get("SUBSCRPT_RCEPT_BGNDE") or row.get("GNRL_RCEPT_BGNDE")
        end = row.get("SUBSCRPT_RCEPT_ENDDE") or row.get("GNRL_RCEPT_ENDDE")
    return {
        "id": f"{row.get('HOUSE_MANAGE_NO')}_{row.get('PBLANC_NO')}",
        "유형": kind,
        "세부유형": row.get("HOUSE_SECD_NM"),  # 무순위/불법행위 재공급/임의공급/APT 등
        "단지명": row.get("HOUSE_NM"),
        "지역": row.get("SUBSCRPT_AREA_CODE_NM"),
        "주소": row.get("HSSPLY_ADRES"),
        "주택구분": row.get("HOUSE_DTL_SECD_NM") or row.get("HOUSE_SECD_NM"),
        "모집공고일": _d(row.get("RCRIT_PBLANC_DE")),
        "접수시작": _d(bgn),
        "접수종료": _d(end),
        "공급세대수": row.get("TOT_SUPLY_HSHLDCO"),
        "입주예정월": row.get("MVN_PREARNGE_YM"),
        "공고URL": row.get("PBLANC_URL"),
        "_house_manage_no": row.get("HOUSE_MANAGE_NO"),
    }


def _prices(kind: str, house_manage_no, key: str) -> list[dict]:
    """주택형별 분양가(최고가, 만원 → 억). 줍줍 금액은 콤마 포함일 수 있고,
    임의공급 Mdl엔 공급면적이 없을 수 있어 방어적으로 처리."""
    op = _MDL_OP.get(kind)
    if not op or not house_manage_no:
        return []
    try:
        rows = _get(op, key, {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no})
    except Exception:
        return []
    out = []
    for r in rows:
        ty = str(r.get("HOUSE_TY") or "").strip()
        try:
            exclusive = round(float(ty.split(".")[0])) if ty else None
        except ValueError:
            exclusive = None
        amt = r.get("LTTOT_TOP_AMOUNT")
        eok = None
        if amt not in (None, ""):
            try:
                eok = round(int(str(amt).replace(",", "").strip()) / 10000, 2)
            except ValueError:
                eok = None
        out.append({"전용": exclusive, "분양가억": eok, "일반공급세대": r.get("SUPLY_HSHLDCO")})
    out.sort(key=lambda x: (x["전용"] is None, x["전용"] or 0))
    return out


def collect(key: str, sent_ids: set[str], today: date | None = None,
            new_days: int = 7, soon_days: int = 3, lookback: int = 60) -> dict:
    today = today or today_kst()
    today_s = today.isoformat()
    new_from = (today - timedelta(days=new_days)).isoformat()
    soon_to = (today + timedelta(days=soon_days)).isoformat()
    fetch_from = (today - timedelta(days=lookback)).isoformat()

    rows, warnings = [], []
    for op, kind, fmt in _ENDPOINTS:
        frm = fetch_from if fmt == "dash" else fetch_from.replace("-", "")
        try:
            for r in _get(op, key, {"cond[RCRIT_PBLANC_DE::GTE]": frm}):
                if r.get("SUBSCRPT_AREA_CODE_NM") in _CAPITAL:
                    rows.append(_norm(r, kind))
        except Exception as e:
            warnings.append(f"{kind} 수집 실패({type(e).__name__}) — 다음 실행에서 자동 복구")

    rows = [x for x in rows if not x["접수종료"] or x["접수종료"] >= today_s]  # 접수 미마감만

    new_listings, seen_new = [], set()
    for x in rows:
        if (x["모집공고일"] or "") >= new_from and x["id"] not in sent_ids:
            x["평형분양가"] = _prices(x["유형"], x["_house_manage_no"], key)
            new_listings.append(x)
            seen_new.add((x["단지명"], x["지역"]))
    for x in rows:
        x.pop("_house_manage_no", None)

    upcoming = []
    for x in rows:
        if (x["단지명"], x["지역"]) in seen_new:
            continue
        bgn, end = x["접수시작"] or "", x["접수종료"] or ""
        imminent = bool(bgn) and today_s <= bgn <= soon_to
        ongoing = bool(bgn) and bool(end) and bgn <= today_s <= end
        if imminent or ongoing:
            upcoming.append({
                "유형": x["유형"], "단지명": x["단지명"], "지역": x["지역"],
                "접수시작": x["접수시작"], "접수종료": x["접수종료"],
                "공고URL": x["공고URL"], "상태": "진행중" if ongoing else "임박",
            })
    upcoming.sort(key=lambda x: x["접수시작"] or "")

    return {"as_of": today_s, "new_listings": new_listings,
            "upcoming": upcoming, "warnings": warnings}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import state
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("ERROR: DATA_GO_KR_KEY 환경변수가 필요합니다.")
    print(json.dumps(collect(k, state.load().sent), ensure_ascii=False, indent=1))
