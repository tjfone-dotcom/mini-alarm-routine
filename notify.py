#!/usr/bin/env python3
"""텔레그램 발송기 (소유자 본인 알림). 표준 라이브러리만 사용.

- 4096자 제한: 빈 줄(블록) 경계에서 자동 분할하고 [i/n] 표시
- HTML 파싱 오류 시 태그를 걷어낸 일반 텍스트로 재발송

환경변수: TG_BOT_TOKEN, TG_CHAT_ID
단독 실행: echo "<메시지>" | python3 notify.py
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 3800  # 4096에서 [i/n] 표시·여유분을 뺀 값


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """블록(빈 줄) → 줄 → 글자 순으로 잘라 limit 이하 조각들로 나눈다."""
    def pieces(t: str, sep: str) -> list[str]:
        return [p for p in t.split(sep)] if sep else [t[i:i + limit] for i in range(0, len(t), limit)]

    def pack(parts: list[str], sep: str) -> list[str]:
        out, cur = [], ""
        for p in parts:
            cand = f"{cur}{sep}{p}" if cur else p
            if len(cand) <= limit:
                cur = cand
                continue
            if cur:
                out.append(cur)
            if len(p) <= limit:
                cur = p
            else:  # 한 조각이 너무 길면 더 잘게
                finer = "\n" if sep == "\n\n" else ""
                sub = pack(pieces(p, finer), finer)
                out.extend(sub[:-1])
                cur = sub[-1]
        if cur:
            out.append(cur)
        return out

    text = text.strip()
    if len(text) <= limit:
        return [text]
    return pack(pieces(text, "\n\n"), "\n\n")


def _strip_html(t: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t))


def _post(token: str, chat_id: str, text: str, as_html: bool) -> tuple[bool, str]:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if as_html:
        payload["parse_mode"] = "HTML"
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=urllib.parse.urlencode(payload).encode())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__
    return bool(body.get("ok")), str(body.get("description", ""))


def send(text: str) -> tuple[bool, str]:
    """분할 발송. (전부 성공 여부, 요약) 반환."""
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "TG_BOT_TOKEN / TG_CHAT_ID 환경변수 없음"
    parts = split_message(text)
    n = len(parts)
    fallback = False
    for i, part in enumerate(parts, 1):
        body = f"[{i}/{n}] {part}" if n > 1 else part
        ok, desc = _post(token, chat_id, body, as_html=True)
        if not ok and "parse" in desc.lower():  # HTML 오류 → 일반 텍스트로 재시도
            ok, desc = _post(token, chat_id, _strip_html(body), as_html=False)
            fallback = True
        if not ok:
            return False, f"{i}/{n}번째 조각 발송 실패: {desc}"
        if i < n:
            time.sleep(0.5)
    note = f"{n}분할" if n > 1 else "1건"
    return True, note + (" · HTML 오류로 일반 텍스트 발송" if fallback else "")


if __name__ == "__main__":
    msg = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
    if not msg:
        sys.exit("ERROR: 보낼 메시지가 비어 있습니다(stdin).")
    ok, info = send(msg)
    print(("텔레그램 발송 성공 " if ok else "텔레그램 발송 실패: ") + info)
    sys.exit(0 if ok else 1)
