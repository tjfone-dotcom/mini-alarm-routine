# mini-alarm-routine

수도권(서울·경기·인천) 신규 분양 + 무순위 줍줍 정보를 **매일 1회** 수집·선별해
텔레그램으로 다이제스트를 보내는 Claude Code 예약 실행(routine)용 저장소.

- `INSTRUCTIONS.md` — 원격 예약 에이전트가 매일 따르는 절차서
- `notify.py` — 텔레그램 발송기 (표준 라이브러리만 사용, 토큰은 환경변수)

## 동작 방식

Claude Code 예약 routine이 매일 평일 09:00(KST)에 이 저장소를 클론하고
`INSTRUCTIONS.md`를 읽어 실행한다. 데이터는 공공데이터포털(청약홈 분양정보 +
국토부 실거래가) 무료 API를 사용한다.

## 시크릿

코드에는 시크릿이 없다(공개 저장소). 서비스키·텔레그램 토큰은 routine 프롬프트에서
환경변수(`DATA_GO_KR_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`)로만 주입된다.
