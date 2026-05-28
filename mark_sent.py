#!/usr/bin/env python3
"""발송한 신규 공고 id를 state/sent.json에 기록하고 repo에 커밋·푸시.

다이제스트를 성공적으로 발송한 뒤에만 호출한다. 이력 저장으로 다음 실행 때 중복 알림을 막는다.

사용법: GH_TOKEN=xxx python mark_sent.py <id1> <id2> ...
환경변수:
    GH_TOKEN  GitHub PAT (Contents: write 권한, mini-alarm-routine)
    GH_REPO   (선택) 기본 tjfone-dotcom/mini-alarm-routine
"""
import json
import os
import subprocess
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE = os.path.join(_DIR, "state", "sent.json")
_MAX_KEEP = 2000  # 무한 증가 방지


def _run(*args: str) -> int:
    return subprocess.run(args, cwd=_DIR).returncode


def main() -> None:
    ids = [a for a in sys.argv[1:] if a]
    if not ids:
        print("기록할 id 없음 — 종료")
        return

    os.makedirs(os.path.dirname(_STATE), exist_ok=True)
    data = {"sent": []}
    if os.path.exists(_STATE):
        try:
            with open(_STATE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    sent = data.get("sent", [])
    seen = set(sent)
    for i in ids:
        if i not in seen:
            sent.append(i)
            seen.add(i)
    data["sent"] = sent[-_MAX_KEEP:]
    with open(_STATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GH_REPO", "tjfone-dotcom/mini-alarm-routine").strip()
    if not token:
        print("WARN: GH_TOKEN 없음 — 로컬 sent.json만 갱신, 푸시 생략", file=sys.stderr)
        return

    _run("git", "config", "user.email", "routine@local")
    _run("git", "config", "user.name", "mini-alarm-routine")
    _run("git", "add", "state/sent.json")
    rc = _run("git", "commit", "-m", "chore: update sent.json")
    if rc != 0:
        print("커밋할 변경 없음(이미 기록됨) — 푸시 생략")
        return
    _run("git", "remote", "set-url", "origin",
         f"https://x-access-token:{token}@github.com/{repo}.git")
    if _run("git", "push", "origin", "HEAD:main") == 0:
        print(f"sent.json 푸시 완료 ({len(ids)}건 기록)")
    else:
        print("푸시 실패 — 다음 실행에서 재시도(일시적 중복 가능)", file=sys.stderr)


if __name__ == "__main__":
    main()
