"""상태 저장소: 발송 이력·마지막 실행일·단지 분석 캐시. repo의 state/ 에 커밋해 실행 간에 유지한다.

- state/sent.json            {"sent": [공고id, ...]}       중복 알림 방지
- state/meta.json            {"last_run": "YYYY-MM-DD"}    실행 누락 감지
- state/analysis_cache.json  {단지키: {분석..., analyzed_at}} 같은 단지 N차 재공급 시 재사용
"""
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(DIR, "state")
_SENT = os.path.join(STATE_DIR, "sent.json")
_META = os.path.join(STATE_DIR, "meta.json")
_CACHE = os.path.join(STATE_DIR, "analysis_cache.json")
_MAX_SENT = 2000
_CACHE_KEEP_DAYS = 365


@dataclass
class State:
    sent_list: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)

    @property
    def sent(self) -> set[str]:
        return set(self.sent_list)


def _read(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: str, data) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def load() -> State:
    return State(
        sent_list=_read(_SENT, {}).get("sent", []),
        meta=_read(_META, {}),
        cache=_read(_CACHE, {}),
    )


def update(st: State, new_ids: list[str], as_of: str, new_cache: dict) -> None:
    """메모리상 상태에 이번 실행 결과를 반영하고 파일로 저장(커밋은 별도)."""
    seen = st.sent
    for i in new_ids:
        if i not in seen:
            st.sent_list.append(i)
            seen.add(i)
    st.sent_list = st.sent_list[-_MAX_SENT:]
    st.meta["last_run"] = as_of
    st.cache.update(new_cache)
    cutoff = (date.fromisoformat(as_of) - timedelta(days=_CACHE_KEEP_DAYS)).isoformat()
    st.cache = {k: v for k, v in st.cache.items() if v.get("analyzed_at", as_of) >= cutoff}
    _write(_SENT, {"sent": st.sent_list})
    _write(_META, st.meta)
    _write(_CACHE, st.cache)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", *args), cwd=DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def commit_and_push(message: str) -> tuple[bool, str]:
    """state/ 변경을 커밋·푸시. (성공여부, 사람이 읽을 사유) 반환. 시크릿은 출력하지 않는다."""
    _git("config", "user.email", "routine@local")
    _git("config", "user.name", "mini-alarm-routine")
    _git("add", "state")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return True, "변경 없음"
    c = _git("commit", "-m", message)
    if c.returncode != 0:
        return False, "커밋 실패"

    token = os.environ.get("GH_TOKEN", "").strip()
    if token:  # 레거시: PAT로 푸시. 없으면 실행 환경(GitHub App) 인증을 그대로 쓴다.
        repo = os.environ.get("GH_REPO", "tjfone-dotcom/mini-alarm-routine").strip()
        _git("remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{repo}.git")

    p = _git("push", "origin", "HEAD:main")
    if p.returncode != 0:  # 원격이 앞서 있으면 한 번만 rebase 후 재시도
        _git("pull", "--rebase", "origin", "main")
        p = _git("push", "origin", "HEAD:main")
    if p.returncode == 0:
        return True, "푸시 완료"
    lines = [ln.strip() for ln in (p.stderr or p.stdout or "").splitlines() if ln.strip()]
    # 'remote:' 안내(예: GitHub App 미설치)가 가장 유용하고, 없으면 fatal/error, 그다음 마지막 줄
    pick = ([ln for ln in lines if ln.startswith("remote:")]
            or [ln for ln in lines if ln.startswith(("fatal:", "error:"))] or lines[-1:])
    reason = pick[0] if pick else "알 수 없는 오류"
    if token:
        reason = reason.replace(token, "***")
    return False, reason[:200]
