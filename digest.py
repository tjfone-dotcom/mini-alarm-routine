"""다이제스트 조립 — 순수 함수(네트워크·파일 I/O 없음).

- complex_key / round_no : 같은 단지의 N차 재공급을 하나로 묶는다
- score                  : 별점 산식(고정 규칙, LLM은 사실 판단만 제공)
- missed_runs            : 직전 실행 이후 빠진 평일 감지
- render                 : 텔레그램 HTML 다이제스트(값은 전부 이스케이프)
"""
import html
import re
from datetime import date, timedelta

JJUP = {"무순위", "임의공급"}  # 줍줍(불법행위 재공급은 무순위 엔드포인트에 포함)
_ROUND = re.compile(r"\((\d+)차\)")
_WEEKDAY = "월화수목금토일"
MAX_UPCOMING = 15


# ---------- 단지 묶기 ----------

def round_no(name: str) -> int:
    """'리아츠 더 인천(19차)' → 19. '(N차)'가 없으면 1. '…송도 4차(2차)'의 '4차'는 단지명이라 무시."""
    found = _ROUND.findall(name or "")
    return max(int(n) for n in found) if found else 1


def base_name(name: str) -> str:
    return _ROUND.sub("", name or "").strip()


def complex_key(item: dict) -> str:
    compact = re.sub(r"\s+", "", base_name(item.get("단지명")))
    return f"{item.get('지역')}|{compact}"


# ---------- 별점 ----------

def score(item: dict, a: dict, rnd: int) -> tuple[int, list[str]]:
    """기본 3점에서 가감, 1~5로 제한. 근거 항목 목록도 반환."""
    s, why = 3, []

    def add(n: int, label: str) -> None:
        nonlocal s
        s += n
        why.append(f"{label}{'+' if n > 0 else '−'}{abs(n)}")

    if item.get("지역") == "서울":
        add(1, "서울")
    if item.get("유형") in JJUP:
        add(1, "줍줍")
    else:
        n = item.get("공급세대수") or 0
        if n >= 1000:
            add(1, "대단지")
        elif 0 < n < 100:
            add(-1, "소규모")
    if a.get("역세권") is True:
        add(1, "역세권")
    if a.get("여론") == "긍정":
        add(1, "여론")
    elif a.get("여론") == "부정":
        add(-1, "여론")
    if rnd >= 6:
        add(-2, "반복재공급")
    elif rnd >= 3:
        add(-1, "반복재공급")
    return max(1, min(5, s)), why


def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


# ---------- 실행 누락 ----------

def missed_runs(last_run: str | None, today: date) -> list[date]:
    """last_run 다음 날부터 today 전날까지의 평일(=실행됐어야 하는 날)."""
    if not last_run:
        return []
    d = date.fromisoformat(last_run) + timedelta(days=1)
    out = []
    while d < today:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------- 포맷 ----------

def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=False)


def _md(s: str | None) -> str:
    return f"{s[5:7]}/{s[8:10]}" if s and len(s) >= 10 else "?"


def period(bgn: str | None, end: str | None) -> str:
    if bgn and end and bgn != end:
        tail = end[8:10] if bgn[:7] == end[:7] else _md(end)
        return f"{_md(bgn)}~{tail}"
    return _md(bgn or end)


def _ym(s) -> str:
    s = str(s or "")
    return f"{s[:4]}.{s[4:6]}" if len(s) == 6 else "?"


def prices(rows: list[dict], max_groups: int = 5) -> str:
    """전용면적별 최고분양가 범위: '전용59 5.78억 · 전용84 7.20~7.46억'."""
    by = {}
    for r in rows or []:
        if r.get("전용") is not None and r.get("분양가억") is not None:
            by.setdefault(r["전용"], []).append(r["분양가억"])
    parts = []
    for ex in sorted(by)[:max_groups]:
        lo, hi = min(by[ex]), max(by[ex])
        parts.append(f"전용{ex} {lo:.2f}억" if lo == hi else f"전용{ex} {lo:.2f}~{hi:.2f}억")
    if len(by) > max_groups:
        parts.append(f"외 {len(by) - max_groups}개 평형")
    return " · ".join(parts)


def label(item: dict) -> str:
    kind, sub = item.get("유형"), item.get("세부유형")
    if kind == "무순위" and sub == "불법행위 재공급":
        return "불법행위재공급"
    if kind == "분양" and sub in ("민간사전청약", "신혼희망타운"):
        return f"분양·{sub}"
    return kind or "?"


def _sigungu(item: dict) -> str:
    """주소에서 시군구 정도만: '경기도 김포시 사우동 …' → '김포시'."""
    toks = (item.get("주소") or "").split()
    for t in toks[1:3]:
        if t.endswith(("시", "군", "구")):
            return t
    return ""


def _where(item: dict) -> str:
    sg = _sigungu(item)
    return f"{item.get('지역')} {sg}".strip()


def _link(item: dict, text: str = "공고") -> str:
    url = item.get("공고URL")
    return f'<a href="{html.escape(url, quote=True)}">{e(text)}</a>' if url else ""


def _supply(item: dict) -> str:
    n = item.get("공급세대수")
    if not n:
        return ""
    return f"{n}세대 줍줍" if item.get("유형") in JJUP else f"총 {n}세대"


def _round_tag(rnd: int) -> str:
    return f" · {rnd}차" if rnd > 1 else ""


# ---------- 렌더 ----------

def full_block(item: dict, a: dict, rnd: int) -> str:
    st, why = score(item, a, rnd)
    lines = [f"<b>[{e(label(item))}] {e(base_name(item.get('단지명')))}</b> ({e(_where(item))}){_round_tag(rnd)}"]
    sched = f"• 일정: 접수 {period(item.get('접수시작'), item.get('접수종료'))}"
    if item.get("입주예정월"):
        sched += f" · 입주 {_ym(item['입주예정월'])}"
    lines.append(sched)
    kind_detail = e(item.get("주택구분")) if item.get("유형") == "분양" else ""  # 민영/국민
    size = " · ".join(x for x in (_supply(item), kind_detail) if x)
    if size:
        lines.append(f"• 규모: {size}")
    p = prices(item.get("평형분양가"))
    if p:
        lines.append(f"• 분양가(최고가): {e(p)}")
    lines.append(f"• 입지: {e(a.get('입지') or '-')}")
    lines.append(f"• 학군·생활: {e(a.get('학군생활') or '-')}")
    lines.append(f"• 여론: {e(a.get('여론') or '?')} · 화제성 {e(a.get('화제성') or '?')}"
                 f" — {e(a.get('여론요약') or '-')}")
    basis = f" ({' · '.join(why)})" if why else ""
    lines.append(f"• 총평: {e(a.get('총평') or '-')} {stars(st)}{e(basis)}")
    lines.append(f"🔗 {_link(item, '청약홈 공고')}")
    return "\n".join(lines)


def compact_line(item: dict, a: dict, rnd: int, note: str) -> str:
    st, why = score(item, a, rnd)
    bits = [f"접수 {period(item.get('접수시작'), item.get('접수종료'))}"]
    if _supply(item):
        bits.append(_supply(item))
    p = prices(item.get("평형분양가"), max_groups=2)
    if p:
        bits.append(e(p))
    penalty = [w for w in why if w.startswith("반복재공급")]
    tail = f"{stars(st)}" + (f" ({e(penalty[0])})" if penalty else "")
    return (f"• [{e(label(item))}] {e(base_name(item.get('단지명')))}{_round_tag(rnd)} "
            f"({e(_where(item))}) — {' · '.join(bits)} · {tail} · {e(note)} {_link(item)}")


def render(as_of: str, groups: list[dict], upcoming: list[dict],
           warnings: list[str], missed: list[date]) -> str | None:
    """groups: [{key, items:[...], analysis:{...}, cached:bool, analyzed_at}].
    보낼 내용이 전혀 없으면 None."""
    total_new = sum(len(g["items"]) for g in groups)
    if not total_new and not upcoming and not warnings and not missed:
        return None

    d = date.fromisoformat(as_of)
    blocks = [f"🏠 <b>수도권 청약 다이제스트</b> ({d.month}/{d.day} {_WEEKDAY[d.weekday()]})"]

    notes = []
    if missed:
        days = ", ".join(f"{m.month}/{m.day}({_WEEKDAY[m.weekday()]})" for m in missed)
        notes.append(f"⚠️ 실행 누락: {days} — 사용량 한도 등으로 실행되지 않았습니다. "
                     "그 사이 공고는 아래에 포함됩니다(7일 윈도우).")
    notes += [f"⚠️ {e(w)}" for w in warnings]
    if notes:
        blocks.append("\n".join(notes))

    if not total_new:
        blocks.append("신규 공고: 없음")
    else:
        fresh = [g for g in groups if not g["cached"]]
        reused = [g for g in groups if g["cached"]]

        def g_score(g):
            it = g["items"][0]
            return score(it, g["analysis"], round_no(it.get("단지명")))[0]

        fresh.sort(key=lambda g: (-g_score(g), g["items"][0].get("접수시작") or ""))
        blocks.append(f"<b>━ 신규 공고 {total_new}건 ━</b>")
        for g in fresh:
            first, rest = g["items"][0], g["items"][1:]
            text = full_block(first, g["analysis"], round_no(first.get("단지명")))
            for it in rest:  # 같은 날 같은 단지의 다른 공고(회차·유형)
                text += "\n" + compact_line(it, g["analysis"], round_no(it.get("단지명")), "같은 단지")
            blocks.append(text)
        if reused:
            lines = ["<b>↻ 재공급·재등장</b> (이전 분석 재사용)"]
            for g in sorted(reused, key=lambda g: g["items"][0].get("접수시작") or ""):
                when = g.get("analyzed_at") or ""
                note = f"{int(when[5:7])}/{int(when[8:10])} 평가" if len(when) >= 10 else "이전 평가"
                for it in g["items"]:
                    lines.append(compact_line(it, g["analysis"], round_no(it.get("단지명")), note))
            blocks.append("\n".join(lines))

    if upcoming:
        lines = [f"⏰ <b>접수 임박·진행 {len(upcoming)}건</b>"]
        for u in upcoming[:MAX_UPCOMING]:
            lines.append(f"• [{e(u.get('유형'))}] {e(u.get('단지명'))} ({e(u.get('지역'))}) — "
                         f"접수 {period(u.get('접수시작'), u.get('접수종료'))} [{e(u.get('상태'))}] {_link(u)}")
        if len(upcoming) > MAX_UPCOMING:
            lines.append(f"… 외 {len(upcoming) - MAX_UPCOMING}건")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
