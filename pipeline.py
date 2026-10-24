#!/usr/bin/env python3
"""청약 다이제스트 일일 파이프라인. 에이전트는 아래 둘만 실행하고 그 사이에 분석 JSON만 쓴다.

  python3 pipeline.py prepare            수집 → 단지 묶기 → 분석 대상(to_analyze) 출력
  python3 pipeline.py finish [--dry-run] 렌더 → 텔레그램 발송(자동 분할) → 이력·캐시·실행일 저장

시크릿은 환경변수에서 직접 읽는다: DATA_GO_KR_KEY, TG_BOT_TOKEN, TG_CHAT_ID (선택 GH_TOKEN)
"""
import argparse
import html
import json
import os
import sys
from datetime import date, timedelta

import collect
import digest
import notify
import state

WORK = os.path.join(state.DIR, "work")
CANDIDATES = os.path.join(WORK, "candidates.json")
ANALYSIS = os.path.join(WORK, "analysis.json")
CACHE_TTL_DAYS = 120          # 이보다 오래된 단지 분석은 다시 한다
_OPINION = {"긍정", "중립", "부정"}
_BUZZ = {"높음", "보통", "낮음"}
_TEXT_FIELDS = ("입지", "학군생활", "여론요약", "총평")


def _dump(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def prepare() -> int:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        print("결과: DATA_GO_KR_KEY 환경변수가 없습니다(루틴 환경변수 설정 확인).")
        return 2
    st = state.load()
    data = collect.collect(key, st.sent)
    today = date.fromisoformat(data["as_of"])
    missed = digest.missed_runs(st.meta.get("last_run"), today)

    groups: dict[str, dict] = {}
    for it in data["new_listings"]:
        k = digest.complex_key(it)
        groups.setdefault(k, {"key": k, "items": []})["items"].append(it)

    fresh_from = (today - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    to_analyze = []
    for g in groups.values():
        c = st.cache.get(g["key"])
        if c and c.get("analyzed_at", "") >= fresh_from:
            g.update(cached=True, analysis=c, analyzed_at=c["analyzed_at"])
            continue
        first = g["items"][0]
        g.update(cached=False, analysis={}, aid=f"A{len(to_analyze) + 1}")
        to_analyze.append({
            "id": g["aid"],
            "단지명": digest.base_name(first.get("단지명")),
            "유형": digest.label(first),
            "지역": first.get("지역"),
            "주소": first.get("주소"),
            "회차": max(digest.round_no(i.get("단지명")) for i in g["items"]),
        })

    _dump(CANDIDATES, {"as_of": data["as_of"], "missed": [d.isoformat() for d in missed],
                       "warnings": data["warnings"], "groups": list(groups.values()),
                       "upcoming": data["upcoming"]})
    if os.path.exists(ANALYSIS):  # 이전 실행 분석이 섞이지 않게
        os.remove(ANALYSIS)

    reused = sum(1 for g in groups.values() if g["cached"])
    print(json.dumps({
        "as_of": data["as_of"],
        "신규": len(data["new_listings"]), "재사용": reused, "임박": len(data["upcoming"]),
        "실행누락": [d.isoformat() for d in missed], "수집경고": data["warnings"],
        "to_analyze": to_analyze,
        "다음": ("work/analysis.json 작성 후 python3 pipeline.py finish" if to_analyze
                 else "분석 불필요 — 바로 python3 pipeline.py finish"),
    }, ensure_ascii=False, indent=1))
    return 0


def _clean(a) -> dict:
    """LLM이 쓴 분석을 정해진 형식으로 정규화. 이상한 값은 버린다."""
    if not isinstance(a, dict):
        return {}
    out = {}
    v = a.get("역세권")
    if isinstance(v, bool):
        out["역세권"] = v
    elif isinstance(v, str) and v.strip().lower() in ("true", "예", "yes", "o"):
        out["역세권"] = True
    elif isinstance(v, str) and v.strip().lower() in ("false", "아니오", "no", "x"):
        out["역세권"] = False
    if a.get("여론") in _OPINION:
        out["여론"] = a["여론"]
    if a.get("화제성") in _BUZZ:
        out["화제성"] = a["화제성"]
    for f in _TEXT_FIELDS:
        if isinstance(a.get(f), str) and a[f].strip():
            out[f] = " ".join(a[f].split())[:90]
    return out


def finish(dry_run: bool) -> int:
    if not os.path.exists(CANDIDATES):
        print("결과: work/candidates.json 없음 — 먼저 python3 pipeline.py prepare")
        return 2
    cand = _load(CANDIDATES)
    as_of = cand["as_of"]
    raw = _load(ANALYSIS) if os.path.exists(ANALYSIS) else {}
    groups, warnings = cand["groups"], list(cand["warnings"])

    new_cache, missing = {}, 0
    for g in groups:
        if g["cached"]:
            continue
        a = _clean(raw.get(g.get("aid"), {}))
        g["analysis"] = a
        if a:
            new_cache[g["key"]] = {**a, "단지명": digest.base_name(g["items"][0].get("단지명")),
                                   "analyzed_at": as_of}
        else:
            missing += 1
    if missing:
        warnings.append(f"분석 누락 {missing}건 — 사실 정보만 표시")

    missed = [date.fromisoformat(d) for d in cand["missed"]]
    text = digest.render(as_of, groups, cand["upcoming"], warnings, missed)
    sent_ids = [it["id"] for g in groups for it in g["items"]]
    n_new = len(sent_ids)
    n_fresh = sum(1 for g in groups if not g["cached"])

    if text is None:
        send_info = "발송 없음(신규·임박 없음)"
    elif dry_run:
        parts = notify.split_message(text)
        print(text)
        print(f"\n--- dry-run: {len(text)}자 → {len(parts)}개 조각 {[len(p) for p in parts]} ---")
        send_info = "dry-run(미발송)"
    else:
        ok, info = notify.send(text)
        if not ok:
            print(f"결과: 텔레그램 발송 실패 — {info}. 이력은 기록하지 않음(다음 실행에서 재발송).")
            return 1
        send_info = f"발송 OK({info})"

    summary = (f"신규 {n_new}건(분석 {n_fresh}단지·재사용 {len(groups) - n_fresh}단지) · "
               f"임박 {len(cand['upcoming'])}건 · 실행누락 {len(missed)}일 · {send_info}")
    if dry_run:
        print(f"결과: {summary} · 이력 미기록(dry-run)")
        return 0

    st = state.load()
    state.update(st, sent_ids, as_of, new_cache)
    ok, why = state.commit_and_push(f"chore: state {as_of} (+{n_new})")
    if not ok:
        notify.send(f"⚠️ <b>청약 다이제스트 이력 저장 실패</b> ({as_of})\n"
                    f"사유: {html.escape(why, quote=False)}\n"
                    "내일 같은 공고가 다시 올 수 있습니다.")
        print(f"결과: {summary} · 이력 저장 실패 — {why} (텔레그램으로 경고 발송함)")
        return 2
    print(f"결과: {summary} · 이력 {why}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    f = sub.add_parser("finish")
    f.add_argument("--dry-run", action="store_true", help="발송·이력 저장 없이 렌더 결과만 출력")
    args = ap.parse_args()
    return prepare() if args.cmd == "prepare" else finish(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
