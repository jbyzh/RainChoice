import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from core import PLATFORMS, Pipeline, RainClient, parse_course_input
from test_rainchoice import question


class CourseLinkTests(unittest.TestCase):
    def test_number_and_links_for_each_platform(self):
        for platform, info in PLATFORMS.items():
            with self.subTest(platform=platform):
                self.assertEqual(parse_course_input(' 12345678 ', platform), '12345678')
                for suffix in ['/v2/web/studentLog/12345678',
                               '/v2/web/studentLog/12345678?classroom_id=12345678&platform_id=3',
                               '/v2/web/example?classroom_id=12345678']:
                    self.assertEqual(parse_course_input(info['origin']+suffix, platform), '12345678')

    def test_wrong_platform_unknown_host_and_invalid_ids_rejected(self):
        for value in ['', '课程名称', '１２３', 'https://changjiang.yuketang.cn/v2/web/studentLog/123',
                      'https://www.yuketang.cn.evil.example/v2/web/studentLog/123',
                      'https://www.yuketang.cn@evil.example/?classroom_id=123',
                      'http://www.yuketang.cn/v2/web/studentLog/123',
                      'https://www.yuketang.cn/v2/web/index?platform_id=3&lessonId=123',
                      'https://www.yuketang.cn/v2/web/studentLog/123?classroom_id=456',
                      'https://www.yuketang.cn/v2/web/index?classroom_id=1&classroom_id=2',
                      'https://www.yuketang.cn/v2/web/index?classroom_id=']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_course_input(value)


class PlatformClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_uses_selected_platform_for_ws_and_http(self):
        for platform, info in PLATFORMS.items():
            with self.subTest(platform=platform):
                messages = [{'qrcode':'https://weixin.qq.com/test','expire_seconds':90},
                            {'UserID':123,'Auth':'fake-login-token'}]
                class Socket:
                    async def __aenter__(self): return self
                    async def __aexit__(self,*args): pass
                    async def send(self,value): pass
                    async def recv(self): return json.dumps(messages.pop(0))
                client = RainClient(lambda *args:None, platform)
                response = Mock(status_code=200, headers={})
                response.json.return_value = {'code':0}
                client.session.request = Mock(return_value=response)
                with patch('core.connect',return_value=Socket()) as connect:
                    await client.login(threading.Event())
                self.assertTrue(client.logged_in)
                self.assertEqual(connect.call_args.args[0],info['origin'].replace('https:','wss:')+'/wsapp/')
                self.assertEqual([call.args[1] for call in client.session.request.call_args_list],
                                 [info['origin']+'/pc/web_login',info['origin']+'/api/v3/user/basic-info'])
                self.assertTrue(client.session.headers['Referer'].startswith(info['origin']+'/'))
                client.session.close()

    async def test_monitor_rejects_platform_mismatch_before_network(self):
        client = RainClient(lambda *args:None, 'changjiang')
        client.logged_in = True
        client.target_course = Mock()
        with self.assertRaises(ValueError):
            await client.monitor({'platform':'standard'},lambda *args:None,threading.Event(),None)
        client.target_course.assert_not_called()
        client.session.close()

    async def test_history_isolated_and_legacy_history_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            normal = RainClient(lambda *args:None)
            river = RainClient(lambda *args:None,'changjiang')
            self.assertEqual(normal.history_path(directory),Path(directory)/'data/history.jsonl')
            submit = Mock(return_value={'msg':'OK'})
            for client in [normal, river, normal, river]:
                pipe = Pipeline({'mode':'auto'},lambda *args:None,threading.Event(),client.history_path(directory))
                await pipe.handle(question(),lambda *args:(['A'],''),submit)
            # Same IDs on different platforms each submit once; reopening either never repeats.
            self.assertEqual(submit.call_count,2)
            normal.session.close(); river.session.close()

    async def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ValueError): RainClient(lambda *args:None,'https://evil.example')


class PlatformGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name)/'settings.json'
        self.config_patch = patch('app.CONFIG',self.config)
        self.config_patch.start()
        self.root = tk.Tk(); self.root.withdraw()
        self.ui = app.App(self.root)

    def tearDown(self):
        for timer in self.root.tk.call('after','info'):
            self.root.after_cancel(timer)
        self.ui.client.session.close()
        self.ui.close()
        self.ui.loop_thread.join(timeout=2)
        self.ui.loop.close()
        self.config_patch.stop(); self.temp.cleanup()

    def test_switch_discards_login_cookies_course_and_auto_mode(self):
        old = self.ui.client
        old.logged_in = True
        old.session.cookies.set('sessionid','fake-private-cookie')
        old.auth['123'] = 'fake-token'
        self.ui.course.set('123')
        self.ui.mode.set('AI 自动答题并提交')
        self.ui.platform.set('长江雨课堂'); self.ui.change_platform()
        self.assertIsNot(old,self.ui.client)
        self.assertFalse(self.ui.client.logged_in)
        self.assertEqual(len(self.ui.client.session.cookies),0)
        self.assertEqual(self.ui.client.auth,{})
        self.assertEqual(self.ui.course.get(),'')
        self.assertEqual(self.ui.mode.get(),'只提醒')
        self.assertEqual(self.ui.client.platform,'changjiang')
        self.ui.platform.set('普通雨课堂'); self.ui.change_platform()
        self.assertFalse(self.ui.client.logged_in)

    def test_switch_disabled_while_worker_active(self):
        old = self.ui.client
        self.ui.set_busy(True)
        self.assertEqual(str(self.ui.platform_box.cget('state')),'disabled')
        self.ui.platform.set('长江雨课堂'); self.ui.change_platform()
        self.assertIs(self.ui.client,old)
        self.assertEqual(self.ui.platform.get(),'普通雨课堂')
        self.ui.set_busy(False)
        self.assertEqual(str(self.ui.platform_box.cget('state')),'readonly')

    def test_config_persists_platform_and_parsed_id_without_key(self):
        self.ui.platform.set('长江雨课堂'); self.ui.change_platform()
        self.ui.course.set('https://changjiang.yuketang.cn/v2/web/studentLog/12345678')
        self.ui.key.set('fake-key')
        self.ui.save_config()
        stored = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(stored['platform'],'changjiang')
        self.assertEqual(stored['course_id'],'12345678')
        self.assertNotIn('api_key',stored); self.assertNotIn('mode',stored)
        self.ui.platform.set('普通雨课堂'); self.ui.change_platform()
        self.ui.load_config()
        self.assertEqual(self.ui.client.platform,'changjiang')
        self.assertEqual(self.ui.course.get(),'12345678')
        self.assertFalse(self.ui.client.logged_in)

    def test_old_config_defaults_to_ordinary_platform(self):
        self.config.write_text(json.dumps({'course_id':'12345678','submit_delay':5}),encoding='utf-8')
        self.ui.load_config()
        self.assertEqual(self.ui.client.platform,'standard')
        self.assertEqual(self.ui.course.get(),'12345678')

    def test_course_help_available_in_ui(self):
        with patch('app.messagebox.showinfo') as show:
            self.ui.course_help_btn.invoke()
        self.assertIn('classroom_id=',show.call_args.args[1])
        self.assertIn('studentLog/',show.call_args.args[1])


if __name__ == '__main__': unittest.main()
