import asyncio
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core import Pipeline, Problem, RainClient, api_endpoint, demo, parse_answer, solve


def question(tp=1):
    return Problem("lesson", "p1", tp, "选择正确答案", [
        {"key": "A", "value": "选项一"}, {"key": "B", "value": "选项二"}])


class AnswerTests(unittest.TestCase):
    def test_reject_bad_answers(self):
        for value in [[], ["Z"], ["A", "A"], ["A", "B"], [1], "A", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_answer(json.dumps({"answer": value}), question())

    def test_multi_and_fenced_json(self):
        self.assertEqual(parse_answer('```json\n{"answer":["A","B"]}\n```', question(2))[0], ["A", "B"])

    def test_no_regex_guess_from_prose(self):
        with self.assertRaises(ValueError):
            parse_answer('I think A. {"answer":["A"]}', question())

    def test_endpoint_and_no_plaintext_or_query(self):
        self.assertEqual(api_endpoint("https://example.com/v1/"), "https://example.com/v1/chat/completions")
        self.assertEqual(api_endpoint("https://example.com/v1/chat/completions"), "https://example.com/v1/chat/completions")
        for value in ["http://example.com/v1", "https://key@example.com", "https://example.com?key=secret"]:
            with self.assertRaises(ValueError):
                api_endpoint(value)

    @patch("core.requests.post")
    def test_model_receives_problem_not_cookies(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"choices": [{"message": {"content": '{"answer":["B"],"reason":"示例"}'}}]}
        settings = {"api_key":"test-key", "model":"test-model", "base_url":"https://example.com/v1"}
        self.assertEqual(solve(question(), settings)[0], ["B"])
        args = post.call_args.kwargs
        self.assertFalse(args["allow_redirects"])
        self.assertNotIn("cookie", json.dumps(args).lower())

    @patch("core.requests.post")
    def test_image_question_skipped_with_text_model(self, post):
        p = question(); p.needs_image = True
        with self.assertRaises(ValueError):
            solve(p, {"api_key":"test", "model":"test"})
        post.assert_not_called()


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.stop = threading.Event()
        self.pipe = Pipeline({"mode":"auto"}, lambda k,v:self.events.append((k,v)), self.stop)
        self.submit = Mock(return_value={"msg":"OK"})
        self.solver = Mock(return_value=(["B"], "reason"))

    def status(self):
        return [v["status"] for k,v in self.events if k=="problem"][-1]

    async def test_choice_and_dedup(self):
        await self.pipe.handle(question(), self.solver, self.submit)
        await self.pipe.handle(question(), self.solver, self.submit)
        self.submit.assert_called_once()
        self.assertEqual(self.status(), "已提交")

    async def test_delay_before_submit_and_countdown(self):
        self.pipe.settings["submit_delay"] = 0.2
        started = time.monotonic()
        submitted_at = []
        self.submit.side_effect = lambda *args: submitted_at.append(time.monotonic()) or {"msg":"OK"}
        await self.pipe.handle(question(), self.solver, self.submit)
        self.assertGreaterEqual(submitted_at[0] - started, 0.2)
        self.assertTrue(any(k == "countdown" for k,v in self.events))
        self.assertIn("等待提交", [v["status"] for k,v in self.events if k == "problem"])
        self.assertEqual(self.status(), "已提交")

    async def test_stop_during_delay_cancels(self):
        def emit(kind, value):
            self.events.append((kind,value))
            if kind == "countdown": self.stop.set()
        self.pipe = Pipeline({"mode":"auto", "submit_delay":5}, emit, self.stop)
        await self.pipe.handle(question(), self.solver, self.submit)
        self.submit.assert_not_called()
        self.assertEqual(self.status(), "已取消")

    async def test_question_closes_during_delay(self):
        active = [True]
        def emit(kind, value):
            self.events.append((kind,value))
            if kind == "countdown": active[0] = False
        self.pipe = Pipeline({"mode":"auto", "submit_delay":5}, emit, self.stop)
        await self.pipe.handle(question(), self.solver, self.submit, lambda: active[0])
        self.submit.assert_not_called()
        self.assertEqual(self.status(), "已取消")

    async def test_invalid_delay_never_submits(self):
        for delay in [-1,31,float("nan"),float("inf"),"bad",None]:
            with self.subTest(delay=delay):
                pipe = Pipeline({"mode":"auto", "submit_delay":delay}, lambda k,v:self.events.append((k,v)), self.stop)
                await pipe.handle(question(), self.solver, self.submit)
                self.assertEqual(self.status(), "已跳过")
        self.submit.assert_not_called()

    async def test_zero_delay_has_no_countdown(self):
        self.pipe.settings["submit_delay"] = 0
        await self.pipe.handle(question(), self.solver, self.submit)
        self.submit.assert_called_once()
        self.assertFalse(any(k == "countdown" for k,v in self.events))

    async def test_delay_ignored_for_suggestions_and_reminders(self):
        for mode in ["suggest","remind"]:
            pipe = Pipeline({"mode":mode,"submit_delay":30}, lambda k,v:self.events.append((k,v)), self.stop)
            await asyncio.wait_for(pipe.handle(question(), self.solver, self.submit), 2)
        self.submit.assert_not_called()
        self.assertFalse(any(k == "countdown" for k,v in self.events))

    async def test_nonchoice_never_solves(self):
        await self.pipe.handle(question(4), self.solver, self.submit)
        self.solver.assert_not_called(); self.submit.assert_not_called()

    async def test_missing_answer_never_defaults(self):
        self.solver.return_value = ([], "")
        await self.pipe.handle(question(), self.solver, self.submit)
        self.submit.assert_not_called(); self.assertEqual(self.status(), "已跳过")

    async def test_stop_while_solving_prevents_submit(self):
        def solve_and_stop(p,s):
            self.stop.set(); return ["A"], ""
        await self.pipe.handle(question(), solve_and_stop, self.submit)
        self.submit.assert_not_called(); self.assertEqual(self.status(), "已取消")

    async def test_closed_question_prevents_submit(self):
        await self.pipe.handle(question(), self.solver, self.submit, lambda: False)
        self.submit.assert_not_called()

    async def test_suggestion_and_reminder_modes(self):
        for mode in ["suggest", "remind"]:
            pipe = Pipeline({"mode":mode}, lambda *args:None, self.stop)
            solver = Mock(return_value=(["A"], ""))
            await pipe.handle(question(), solver, self.submit)
            if mode == "remind": solver.assert_not_called()
        self.submit.assert_not_called()

    async def test_uncertain_request_is_not_success_or_retried(self):
        self.submit.side_effect = OSError("timeout")
        await self.pipe.handle(question(), self.solver, self.submit)
        await self.pipe.handle(question(), self.solver, self.submit)
        self.assertEqual(self.status(), "结果待核对")
        self.submit.assert_called_once()

    async def test_reject_server_error(self):
        self.submit.return_value = {"msg":"CLOSED"}
        await self.pipe.handle(question(), self.solver, self.submit)
        self.assertEqual(self.status(), "提交失败")

    async def test_dedup_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            file = pathlib.Path(d)/"history.jsonl"
            a = Pipeline({"mode":"auto"}, lambda *args:None, self.stop, file)
            await a.handle(question(), self.solver, self.submit)
            b = Pipeline({"mode":"auto"}, lambda *args:None, self.stop, file)
            await b.handle(question(), self.solver, self.submit)
            self.submit.assert_called_once()

    async def test_demo_has_no_network(self):
        with patch("core.requests.post", side_effect=AssertionError("Network in demo")), patch("core.requests.Session.request", side_effect=AssertionError("Network in demo")):
            await demo(lambda k,v:self.events.append((k,v)), self.stop)
        final = {}
        for k,v in self.events:
            if k == "problem": final[v["key"]] = v["status"]
        self.assertEqual(list(final.values()).count("已提交"), 2)
        self.assertEqual(list(final.values()).count("已跳过"), 2)

    async def test_demo_stop_during_configured_delay(self):
        def emit(kind, value):
            self.events.append((kind,value))
            if kind == "countdown": self.stop.set()
        await demo(emit, self.stop, submit_delay=5)
        self.assertEqual(self.status(), "已取消")
        self.assertIn("演示已停止", self.events[-1][1])


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = RainClient(lambda *args:None)

    def test_filter_course_by_id_not_other_course(self):
        self.client.request = Mock(return_value={"data":{"onLessonClassrooms":[
            {"classroomId":2,"classroomName":"Other","lessonId":999},
            {"classroomId":12345678,"classroomName":"Target","lessonId":123}]}})
        self.assertEqual(self.client.active_lesson("12345678", "Target", True), 123)
        self.assertIsNone(self.client.active_lesson("111", "Target", True))

    def test_ambiguous_name_not_selected(self):
        self.client.request = Mock(return_value={"data":{"onLessonClassrooms":[{"classroomName":"Target","lessonId":123}]}})
        self.assertIsNone(self.client.active_lesson("12345678", "Target", False))

    def test_unknown_course_fails(self):
        self.client.request = Mock(return_value={"data":{"list":[{"classroom_id":5,"name":"Other"}]}})
        with self.assertRaises(ValueError): self.client.target_course("12345678")

    def test_parse_realistic_slide_keep_wire_id_and_image_need(self):
        self.client.request = Mock(return_value={"data":{"slides":[{"id":456,"cover":"https://example.com/p.png","problem":{"problemType":1,"body":"","options":[{"key":"A","value":""},{"key":"B","value":""}]}}]}})
        p = self.client.problem(123, 55, 456)
        self.assertEqual(p.id, 456); self.assertEqual(p.lesson, 123)
        self.assertTrue(p.needs_image)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_classroom_events_through_submit(self):
        messages = [
            {"op":"hello", "presentation":789, "unlockedproblem":[888]},
            {"op":"unlockproblem", "problem":{"pres":789,"prob":456}},
            {"op":"extendtime", "problem":{"pres":789,"prob":456}},
            {"op":"lessonfinished"},
        ]
        class FakeSocket:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def send(self, raw):
                d = json.loads(raw)
                assert d['lessonid'] == 123
            async def recv(self):
                await asyncio.sleep(0.1)
                return json.dumps(messages.pop(0))
        events=[]
        client = RainClient(lambda k,v:events.append((k,v)))
        client.enter = Mock(return_value=("mock-lesson-token", 999))
        def request(method,path,lesson,**kw):
            if method == 'GET':
                return {"data":{"slides":[{"id":456,"problem":{"problemType":1,"body":"测试题", "options":[{"key":"A","value":"甲"},{"key":"B","value":"乙"}]}}]}}
            assert kw['json']['problemId'] == 456
            assert kw['json']['result'] == ['B']
            return {'msg':'OK'}
        client.request = Mock(side_effect=request)
        stop=threading.Event()
        pipe=Pipeline({'mode':'auto'},lambda k,v:events.append((k,v)),stop)
        with patch('core.connect',return_value=FakeSocket()), patch('core.solve',return_value=(['B'],'mock')):
            await client.watch_lesson(123,{'mode':'auto'},pipe,stop)
        self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),1)
        rows=[v for k,v in events if k=='problem']
        self.assertEqual(rows[-1]['status'],'已提交')
        self.assertEqual({v['key'] for v in rows},{'123:456'})


if __name__ == "__main__":
    unittest.main(verbosity=2)
