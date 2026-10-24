"""digest.py / notify.split_message / pipeline._clean 단위 테스트 (네트워크 없음)."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import digest  # noqa: E402
import notify  # noqa: E402
import pipeline  # noqa: E402


def item(**kw):
    base = {"id": "1_1", "유형": "분양", "세부유형": "APT", "단지명": "테스트 단지", "지역": "경기",
            "주소": "경기도 김포시 사우동 1", "주택구분": "민영", "접수시작": "2026-10-27",
            "접수종료": "2026-10-29", "공급세대수": 500, "입주예정월": "202807",
            "공고URL": "https://www.applyhome.co.kr/x?a=1&b=2", "평형분양가": []}
    base.update(kw)
    return base


class TestGrouping(unittest.TestCase):
    def test_round_no(self):
        self.assertEqual(digest.round_no("리아츠 더 인천(19차)"), 19)
        self.assertEqual(digest.round_no("힐스테이트 레이크 송도 4차(2차)"), 2)
        self.assertEqual(digest.round_no("청라 아크원 푸르지오"), 1)

    def test_same_complex_across_rounds(self):
        a = digest.complex_key(item(단지명="리아츠 더 인천(16차)", 지역="인천"))
        b = digest.complex_key(item(단지명="리아츠 더  인천(19차)", 지역="인천"))
        self.assertEqual(a, b)

    def test_phase_number_is_part_of_name(self):
        key = digest.complex_key(item(단지명="힐스테이트 레이크 송도 4차(2차)", 지역="인천"))
        self.assertIn("4차", key)
        self.assertNotEqual(key, digest.complex_key(item(단지명="힐스테이트 레이크 송도 3차", 지역="인천")))


class TestScore(unittest.TestCase):
    def test_capped_at_5(self):
        s, why = digest.score(item(유형="무순위", 지역="서울"), {"역세권": True, "여론": "긍정"}, 1)
        self.assertEqual(s, 5)
        self.assertEqual(why, ["서울+1", "줍줍+1", "역세권+1", "여론+1"])

    def test_repeat_supply_penalty_floor_1(self):
        s, why = digest.score(item(유형="임의공급"), {"여론": "부정"}, 19)
        self.assertEqual(s, 1)  # 3 +1(줍줍) -1(여론) -2(반복) = 1
        self.assertIn("반복재공급−2", why)

    def test_presale_size(self):
        self.assertIn("대단지+1", digest.score(item(공급세대수=1200), {}, 1)[1])
        self.assertIn("소규모−1", digest.score(item(공급세대수=61), {}, 1)[1])
        self.assertEqual(digest.score(item(공급세대수=500), {}, 1), (3, []))


class TestMissedRuns(unittest.TestCase):
    def test_weekend_is_not_missed(self):
        self.assertEqual(digest.missed_runs("2026-10-23", date(2026, 10, 26)), [])  # 금→월

    def test_skipped_weekday(self):
        self.assertEqual(digest.missed_runs("2026-10-20", date(2026, 10, 22)), [date(2026, 10, 21)])

    def test_first_run(self):
        self.assertEqual(digest.missed_runs(None, date(2026, 10, 26)), [])


class TestFormat(unittest.TestCase):
    def test_period(self):
        self.assertEqual(digest.period("2026-10-27", "2026-10-29"), "10/27~29")
        self.assertEqual(digest.period("2026-10-30", "2026-11-02"), "10/30~11/02")
        self.assertEqual(digest.period("2026-10-27", "2026-10-27"), "10/27")

    def test_prices(self):
        rows = [{"전용": 84, "분양가억": 7.2}, {"전용": 84, "분양가억": 7.46}, {"전용": 59, "분양가억": 5.78}]
        self.assertEqual(digest.prices(rows), "전용59 5.78억 · 전용84 7.20~7.46억")

    def test_label(self):
        self.assertEqual(digest.label(item(유형="무순위", 세부유형="불법행위 재공급")), "불법행위재공급")
        self.assertEqual(digest.label(item(세부유형="신혼희망타운")), "분양·신혼희망타운")


class TestRender(unittest.TestCase):
    def test_nothing_to_send(self):
        self.assertIsNone(digest.render("2026-10-26", [], [], [], []))

    def test_escapes_and_sections(self):
        g_fresh = {"key": "k1", "items": [item(단지명="A&B <타워>")], "cached": False,
                   "analysis": {"입지": "역 도보 5분 & 대로변", "여론": "긍정"}}
        g_reused = {"key": "k2", "items": [item(유형="임의공급", 단지명="리아츠 더 인천(19차)", 지역="인천")],
                    "cached": True, "analysis": {"여론": "부정"}, "analyzed_at": "2026-09-23"}
        text = digest.render("2026-10-26", [g_fresh, g_reused], [], [], [date(2026, 10, 23)])
        self.assertIn("A&amp;B &lt;타워&gt;", text)
        self.assertIn("도보 5분 &amp; 대로변", text)
        self.assertIn('href="https://www.applyhome.co.kr/x?a=1&amp;b=2"', text)
        self.assertIn("↻ 재공급·재등장", text)
        self.assertIn("19차", text)
        self.assertIn("9/23 평가", text)
        self.assertIn("실행 누락: 10/23(금)", text)
        self.assertNotIn("<타워>", text)


class TestSplit(unittest.TestCase):
    def test_short(self):
        self.assertEqual(notify.split_message("짧은 메시지"), ["짧은 메시지"])

    def test_blocks_kept_whole(self):
        blocks = [f"블록{i}\n" + "가" * 300 for i in range(40)]
        parts = notify.split_message("\n\n".join(blocks))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= notify.LIMIT for p in parts))
        self.assertEqual("\n\n".join(parts), "\n\n".join(blocks))  # 내용 손실 없음

    def test_huge_single_line(self):
        parts = notify.split_message("나" * 9000)
        self.assertTrue(all(len(p) <= notify.LIMIT for p in parts))
        self.assertEqual("".join(parts), "나" * 9000)


class TestClean(unittest.TestCase):
    def test_normalizes(self):
        a = pipeline._clean({"역세권": "예", "여론": "매우좋음", "화제성": "높음",
                             "입지": "  여러   공백\n줄바꿈 ", "총평": "x" * 200, "별점": 5})
        self.assertEqual(a["역세권"], True)
        self.assertNotIn("여론", a)        # 허용값 아님 → 버림
        self.assertEqual(a["화제성"], "높음")
        self.assertEqual(a["입지"], "여러 공백 줄바꿈")
        self.assertEqual(len(a["총평"]), 90)
        self.assertNotIn("별점", a)        # 별점은 스크립트가 계산

    def test_garbage(self):
        self.assertEqual(pipeline._clean("문자열"), {})


if __name__ == "__main__":
    unittest.main()
