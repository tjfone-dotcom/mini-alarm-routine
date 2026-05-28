#!/usr/bin/env python3
"""청약 알림 — 텔레그램 발송기 (사용자 본인 알림 시스템).

이 스크립트는 저장소 소유자(사용자) 본인의 텔레그램 봇으로, 본인에게 청약 다이제스트를
전달하기 위한 도구다. 표준 라이브러리만 사용한다(원격 환경에 pip 설치 불필요).

사용법:
    echo "<메시지>" | TG_BOT_TOKEN=xxx TG_CHAT_ID=yyy python notify.py

환경변수:
    TG_BOT_TOKEN  텔레그램 봇 토큰
    TG_CHAT_ID    수신 chat id
메시지 본문은 stdin으로 전달(HTML parse_mode).
"""
import json
import os
import sys
import urllib.parse
import urllib.request


def main() -> None:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("ERROR: TG_BOT_TOKEN / TG_CHAT_ID 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(2)

    # stdin을 OS 로케일과 무관하게 UTF-8로 읽는다(Windows cp949 등 회피)
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
    if not text:
        print("ERROR: 보낼 메시지가 비어 있습니다(stdin).", file=sys.stderr)
        sys.exit(2)

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        with urllib.request.urlopen(url, data=data, timeout=20) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        print(f"텔레그램 발송 오류: {e}", file=sys.stderr)
        sys.exit(1)

    if body.get("ok"):
        print("텔레그램 발송 성공")
    else:
        print(f"텔레그램 발송 실패: {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
