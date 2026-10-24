# mini-alarm-routine

수도권(서울·경기·인천) 신규 분양과 줍줍(무순위·불법행위 재공급·임의공급) 공고를 **평일 아침 1회**
수집해 텔레그램으로 다이제스트를 보내는 Claude Code 예약 실행(routine)용 저장소.

## 동작 방식

routine이 평일 07:30(KST)에 이 저장소를 클론하고 `INSTRUCTIONS.md`대로 실행한다.

1. `python3 pipeline.py prepare` — 청약홈 API 수집, 발송 이력 대조, 같은 단지 묶기, 분석 캐시 조회.
   새로 분석할 단지만 `to_analyze`로 출력한다.
2. 에이전트가 `to_analyze` 단지만 웹 검색해 `work/analysis.json`에 정성 분석을 쓴다.
3. `python3 pipeline.py finish` — 별점 계산, 렌더, 텔레그램 발송, `state/` 커밋·푸시.

LLM은 2단계의 사실 판단(역세권·여론·화제성·한 줄 요약)만 하고, 나머지는 결정론적 스크립트가 처리한다.

## 파일

| 파일 | 역할 |
|---|---|
| `pipeline.py` | prepare / finish 진입점 |
| `collect.py` | 청약홈 OpenAPI 수집(분양·무순위·임의공급 + 평형별 분양가) |
| `digest.py` | 단지 묶기, 별점 산식, 실행 누락 감지, HTML 렌더(순수 함수) |
| `notify.py` | 텔레그램 발송(자동 분할, HTML 실패 시 일반 텍스트로 재시도) |
| `state.py` | `state/` 읽기·쓰기와 커밋·푸시 |
| `state/sent.json` | 발송한 공고 id(중복 방지) |
| `state/meta.json` | 마지막 실행일(실행 누락 감지) |
| `state/analysis_cache.json` | 단지별 분석 캐시(재공급 시 재사용) |

## 로컬 확인

```bash
python -m unittest discover tests
DATA_GO_KR_KEY=... python pipeline.py prepare
python pipeline.py finish --dry-run   # 발송·이력 저장 없이 렌더 결과만 출력
```

## 시크릿

코드와 routine 프롬프트에는 시크릿이 없다(공개 저장소). 값은 routine의 **환경변수**로만 주입한다:
`DATA_GO_KR_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`.
`state/` 푸시는 실행 환경의 GitHub App 인증을 쓴다. `GH_TOKEN`(PAT)은 레거시 대체 수단으로만 지원한다.
