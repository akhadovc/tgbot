"""Run from the bot folder:  python -m unittest discover -s tests"""
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
from ai import AIError, DiddyAI, build_input, cost_usd, format_history, parse_response  # noqa: E402
from config import Settings  # noqa: E402
from formatting import format_sources, split_message, to_telegram_html  # noqa: E402
from storage import HistoryLine, Storage  # noqa: E402


def settings(**overrides) -> Settings:
    base = dict(telegram_bot_token="t", openai_api_key="k", owner_ids=frozenset({1}),
                daily_user_limit=2, monthly_budget_usd=5.0)
    base.update(overrides)
    return Settings(**base)


class FormattingTests(unittest.TestCase):
    def test_markdown_becomes_safe_html(self):
        out = to_telegram_html("**RTX** <b>x</b> & `a<b` [OpenAI](https://openai.com)\n- punkt")
        self.assertIn("<b>RTX</b>", out)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", out)
        self.assertIn("<code>a&lt;b</code>", out)
        self.assertIn('<a href="https://openai.com">OpenAI</a>', out)
        self.assertIn("• punkt", out)

    def test_code_block_is_escaped_not_formatted(self):
        out = to_telegram_html("```py\nprint('**no**' < 2)\n```")
        self.assertEqual(out, "<pre>print('**no**' &lt; 2)</pre>")

    def test_math_star_is_not_italic(self):
        self.assertEqual(to_telegram_html("2 * 3 * 4 = 24"), "2 * 3 * 4 = 24")

    def test_split_respects_limit_and_keeps_text(self):
        text = ("abc " * 3000).strip()
        parts = split_message(text, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parts))
        self.assertEqual(" ".join(parts).split(), text.split())

    def test_sources_deduplicated(self):
        block = format_sources([("A", "https://a.uz"), ("A again", "https://a.uz"), ("", "https://b.uz")])
        self.assertEqual(block.count("https://a.uz"), 1)
        self.assertIn("2. [https://b.uz](https://b.uz)", block)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "t.db"))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_group_history_window_and_prune(self):
        now = time.time()
        self.store.add_group_message(-5, 1, "Ali", "eski", ts=now - 30 * 3600)
        self.store.add_group_message(-5, 2, "Vali", "yangi", ts=now - 60)
        self.store.add_group_message(-6, 3, "Boshqa", "boshqa guruh")
        self.assertEqual([l.text for l in self.store.group_history(-5, 24, 50)], ["yangi"])
        self.store.prune(24, 6)
        self.assertEqual(len(self.store.group_history(-5, 100, 50)), 1)

    def test_daily_limit_is_atomic_and_refundable(self):
        self.assertTrue(self.store.take_daily_request(7, 2))
        self.assertTrue(self.store.take_daily_request(7, 2))
        self.assertFalse(self.store.take_daily_request(7, 2))
        self.store.refund_daily_request(7)
        self.assertTrue(self.store.take_daily_request(7, 2))

    def test_private_memory_keeps_last_turns(self):
        for i in range(10):
            self.store.add_private_turn(9, "user", f"q{i}")
            self.store.add_private_turn(9, "assistant", f"a{i}")
        turns = self.store.private_turns(9, 2)
        self.assertEqual(turns, [("user", "q8"), ("assistant", "a8"), ("user", "q9"), ("assistant", "a9")])
        self.store.prune(24, 3)
        self.assertEqual(len(self.store.private_turns(9, 30)), 6)

    def test_persona_change_forgets_old_bot_replies_once(self):
        path = os.path.join(self.dir.name, "old.db")
        old = Storage(path)
        old.add_group_message(-1, 1, "Ali", "salom")
        old.add_group_message(-1, 2, "Diddy", "Albatta yordam beraman!", is_bot=True)
        old.add_private_turn(3, "assistant", "Ajoyib savol!")
        old._db.execute("PRAGMA user_version = 1")
        old.close()
        fresh = Storage(path)
        self.assertEqual([l.text for l in fresh.group_history(-1, 24, 10)], ["salom"])
        self.assertEqual(fresh.private_turns(3, 5), [])
        fresh.add_private_turn(3, "assistant", "yangi")
        fresh.close()
        again = Storage(path)
        self.assertEqual(again.private_turns(3, 5), [("assistant", "yangi")])
        again.close()

    def test_persona_prompt_is_not_lola(self):
        from ai import SYSTEM_PROMPT
        self.assertIn("Lola EMASSAN", SYSTEM_PROMPT)
        self.assertIn("roast", SYSTEM_PROMPT)

    def test_spend_accumulates(self):
        self.store.add_spend(0.5, 100, 20, 30, 1)
        self.store.add_spend(0.25, 10, 0, 5, 0)
        s = self.store.month_spend()
        self.assertAlmostEqual(s["usd"], 0.75)
        self.assertEqual((s["requests"], s["input_tokens"], s["searches"]), (2, 110, 1))


class TriggerTests(unittest.TestCase):
    def classify(self, text, reply=False):
        return bot.classify_group_message(text, words=("diddy",), bot_username="DiddyBot", reply_to_bot=reply)

    def test_trigger_rules(self):
        self.assertEqual(self.classify("salom hammaga"), "ignore")
        self.assertEqual(self.classify("diddyman emas"), "ignore")  # whole word only
        self.assertEqual(self.classify("Diddy"), "casual")
        self.assertEqual(self.classify("diddy nima gap?"), "casual")
        self.assertEqual(self.classify("Diddy, 5070 Ti uchun qaysi PSU kerak?"), "ask")
        self.assertEqual(self.classify("@DiddyBot bugun nima gaplashildi"), "ask")
        self.assertEqual(self.classify("ha", reply=True), "ask")


class AITests(unittest.TestCase):
    def test_history_keeps_newest_lines_within_budget(self):
        lines = [HistoryLine(i, time.time(), "Ali", f"xabar {i}", False) for i in range(50)]
        block = format_history(lines, 120)
        self.assertIn("xabar 49", block)
        self.assertNotIn("xabar 0", block)
        self.assertLessEqual(len(block), 120)

    def test_build_input_with_image_and_context(self):
        from ai import ImageInput
        items = build_input("bu nima?", history_block="[x] Ali: salom", quoted="Vali: rasm",
                            images=[ImageInput(b"\xff\xd8\xff", "image/jpeg")], author="Ali")
        content = items[-1]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertIn("faqat ma'lumot", content[0]["text"])
        self.assertTrue(content[1]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_parse_response_text_sources_and_searches(self):
        response = {"output": [
            {"type": "web_search_call", "action": {"type": "search"}},
            {"type": "web_search_call", "action": {"type": "open_page"}},
            {"type": "message", "content": [{"type": "output_text", "text": "Javob",
                                             "annotations": [{"type": "url_citation", "url": "https://x.uz",
                                                              "title": "X"}]}]},
        ]}
        self.assertEqual(parse_response(response), ("Javob", [("X", "https://x.uz")], 1))

    def test_cost(self):
        s = settings()
        self.assertAlmostEqual(cost_usd(s, 1_000_000, 0, 0, 0), 2.0)
        self.assertAlmostEqual(cost_usd(s, 1_000_000, 1_000_000, 100_000, 2), 0.1 + 1.0 + 0.02)


# ---------------------------------------------------------------- end-to-end with fakes

class FakeMessage:
    _next_id = 100

    def __init__(self, text, chat_id=-10, chat_type="supergroup", user_id=5, reply_to=None, photo=None):
        FakeMessage._next_id += 1
        self.message_id = FakeMessage._next_id
        self.text, self.caption, self.photo = text, None, photo or ()
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, is_bot=False, first_name=f"User{user_id}", username=None)
        self.reply_to_message = reply_to
        self.video = self.video_note = self.animation = self.document = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        sent = FakeMessage(text, self.chat.id, self.chat.type, user_id=999)
        sent.from_user.is_bot = True
        return sent


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.settings = settings(daily_user_limit=1)
        self.store = Storage(os.path.join(self.dir.name, "t.db"))
        self.create = AsyncMock(return_value={
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "**Ha**, bro",
                                                        "annotations": []}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 100,
                      "input_tokens_details": {"cached_tokens": 0}},
            "status": "completed"})
        client = SimpleNamespace(responses=SimpleNamespace(create=self.create))
        ai = DiddyAI(self.settings, client=client)
        app = SimpleNamespace(bot_data={"settings": self.settings, "storage": self.store, "ai": ai})
        self.context = SimpleNamespace(application=app, args=[],
                                       bot=SimpleNamespace(id=999, username="DiddyBot",
                                                           send_chat_action=AsyncMock()))

    async def asyncTearDown(self):
        self.store.close()
        self.dir.cleanup()

    def update(self, message):
        return SimpleNamespace(effective_message=message)

    async def test_group_question_uses_history_and_limits(self):
        await bot.on_message(self.update(FakeMessage("RTX 5070 Ti oldim")), self.context)
        ask = FakeMessage("diddy menga qaysi blok pitaniya kerak?")
        await bot.on_message(self.update(ask), self.context)
        self.assertEqual(ask.replies, ["<b>Ha</b>, bro"])
        sent_input = self.create.await_args.kwargs["input"][-1]["content"]
        self.assertIn("RTX 5070 Ti oldim", sent_input)
        self.assertEqual(sent_input.count("qaysi blok pitaniya"), 1)  # current message not duplicated
        self.assertEqual(self.create.await_args.kwargs["model"], "gpt-6.1-sol")
        self.assertEqual(self.store.month_spend()["requests"], 1)
        again = FakeMessage("diddy yana bitta uzun savol bor edi")
        await bot.on_message(self.update(again), self.context)
        self.assertIn("limit", again.replies[0])
        self.assertEqual(self.create.await_count, 1)

    async def test_casual_call_costs_nothing(self):
        msg = FakeMessage("Diddy")
        await bot.on_message(self.update(msg), self.context)
        self.assertIn(msg.replies[0], bot.DIDDY_REPLIES)
        self.create.assert_not_awaited()

    async def test_ignored_group_message_is_only_stored(self):
        await bot.on_message(self.update(FakeMessage("oddiy gap")), self.context)
        self.create.assert_not_awaited()
        self.assertEqual(len(self.store.group_history(-10, 24, 10)), 1)

    async def test_private_memory_and_owner_has_no_limit(self):
        for _ in range(3):
            await bot.on_message(self.update(FakeMessage("salom", chat_id=1, chat_type="private", user_id=1)),
                                 self.context)
        self.assertEqual(self.create.await_count, 3)
        roles = [item["role"] for item in self.create.await_args.kwargs["input"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant", "user"])

    async def test_ai_error_refunds_limit(self):
        import openai
        self.create.side_effect = openai.APIConnectionError(request=None) if _real_openai() else AIError("x")
        msg = FakeMessage("diddy bu savolga javob kerak edi")
        await bot.on_message(self.update(msg), self.context)
        self.assertTrue(msg.replies)
        self.assertEqual(self.store.daily_used(5), 0)

    async def test_budget_stops_non_owner(self):
        self.store.add_spend(10.0, 0, 0, 0, 0)
        msg = FakeMessage("diddy bu savolga javob kerak edi")
        await bot.on_message(self.update(msg), self.context)
        self.assertIn("byudjet", msg.replies[0])
        self.create.assert_not_awaited()


class BusinessTests(unittest.IsolatedAsyncioTestCase):
    """Telegram Business: the bot answers inside the owner's personal chats, as the owner."""

    OWNER = 1

    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Storage(os.path.join(self.dir.name, "t.db"))
        self.create = AsyncMock(return_value={
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Salom",
                                                        "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 10}})
        self.connection = SimpleNamespace(user=SimpleNamespace(id=self.OWNER), is_enabled=True,
                                          rights=SimpleNamespace(can_reply=True))
        self.bot = SimpleNamespace(id=999, username="DiddyBot", send_chat_action=AsyncMock(),
                                   send_message=AsyncMock(),
                                   get_business_connection=AsyncMock(return_value=self.connection))

    async def asyncTearDown(self):
        self.store.close()
        self.dir.cleanup()

    def context(self, **overrides):
        overrides.setdefault("business_debounce_seconds", 0)
        overrides.setdefault("daily_user_limit", 5)
        s = settings(owner_ids=frozenset({self.OWNER}), **overrides)
        ai = DiddyAI(s, client=SimpleNamespace(responses=SimpleNamespace(create=self.create)))
        app = SimpleNamespace(bot_data={"settings": s, "storage": self.store, "ai": ai})
        return SimpleNamespace(application=app, args=[], bot=self.bot)

    def msg(self, text, user_id=50, bot_sender=None):
        m = FakeMessage(text, chat_id=50, chat_type="private", user_id=user_id)
        m.business_connection_id = "bc-1"
        m.sender_business_bot = bot_sender
        return m

    async def send(self, message, ctx=None, **overrides):
        import asyncio
        ctx = ctx or self.context(**overrides)
        await bot.on_message(SimpleNamespace(effective_message=message), ctx)
        for _ in range(20):          # let a scheduled auto-reply run
            await asyncio.sleep(0.005)
        return ctx

    async def test_ordinary_personal_messages_are_left_alone(self):
        m = self.msg("salom aka, 5070 ti bormi?")
        await self.send(m)
        self.assertEqual(m.replies, [])
        self.create.assert_not_awaited()

    async def test_diddy_call_is_answered_softly_without_private_memory(self):
        import asyncio
        reply = self.create.return_value

        async def slow_create(**kwargs):
            await asyncio.sleep(0.05)  # long enough for the "typing..." indicator to fire
            return reply
        self.create.side_effect = slow_create
        m = self.msg("diddy 5070 ti uchun qaysi blok kerak?")
        await self.send(m)
        self.assertEqual(m.replies, ["Salom"])
        kwargs = self.create.await_args.kwargs
        self.assertIn("TELEGRAM BUSINESS", kwargs["instructions"])
        self.assertIn("roastni yumshat", kwargs["instructions"])
        self.assertEqual([i["role"] for i in kwargs["input"]], ["user"])
        self.assertEqual(self.store.private_turns(50, 5), [])
        self.bot.send_chat_action.assert_awaited()
        self.assertEqual(self.bot.send_chat_action.await_args.kwargs["business_connection_id"], "bc-1")

    async def test_owner_is_notified_who_got_an_answer(self):
        m = self.msg("diddy <b>narxi</b> qancha?", user_id=77)
        m.from_user.username = "ali_pc"
        await self.send(m)
        self.bot.send_message.assert_awaited_once()
        chat_id, text = self.bot.send_message.await_args.args
        self.assertEqual(chat_id, self.OWNER)
        self.assertIn("tg://user?id=77", text)
        self.assertIn("@ali_pc", text)
        self.assertIn("&lt;b&gt;narxi&lt;/b&gt;", text)   # user text is escaped, not rendered
        self.assertIn("Salom", text)

    async def test_no_notification_for_owner_himself_or_when_disabled(self):
        await self.send(self.msg("diddy menga bitta maslahat ber", user_id=self.OWNER))
        await self.send(self.msg("diddy savol bor edi senga"), business_notify=False)
        self.assertEqual(self.create.await_count, 2)
        self.bot.send_message.assert_not_awaited()

    async def test_casual_reply_is_also_reported(self):
        await self.send(self.msg("Diddy"))
        self.bot.send_message.assert_awaited_once()

    async def test_own_replies_never_loop(self):
        m = self.msg("Diddy band. Navbatga tur 😏", user_id=self.OWNER, bot_sender=SimpleNamespace(id=999))
        await self.send(m)
        self.assertEqual(m.replies, [])

    async def test_all_mode_answers_others_but_not_owner(self):
        customer = self.msg("narxi qancha?")
        await self.send(customer, business_mode="all")
        self.assertEqual(customer.replies, ["Salom"])
        owner_msg = self.msg("hozir javob beraman", user_id=self.OWNER)
        await self.send(owner_msg, business_mode="all")
        self.assertEqual(owner_msg.replies, [])

    async def test_off_mode_and_disabled_connection(self):
        m = self.msg("diddy savol bor edi senga")
        await self.send(m, business_mode="off")
        self.assertEqual(m.replies, [])
        self.connection.rights.can_reply = False
        m2 = self.msg("diddy savol bor edi senga")
        m2.business_connection_id = "bc-2"
        await self.send(m2)
        self.assertEqual(m2.replies, [])


class BusinessAutoReplyTests(BusinessTests):
    async def test_all_mode_burst_gets_one_combined_answer(self):
        import asyncio
        ctx = self.context(business_mode="all", business_debounce_seconds=0.1)
        first, second, third = self.msg("aka salom"), self.msg("bitta savol bor"), self.msg("5070 ti bormi?")
        for m in (first, second, third):
            await bot.on_message(SimpleNamespace(effective_message=m), ctx)
        await asyncio.sleep(0.3)
        self.assertEqual(self.create.await_count, 1)
        question = self.create.await_args.kwargs["input"][-1]["content"]
        self.assertIn("aka salom", question)
        self.assertIn("5070 ti bormi?", question)
        self.assertEqual((first.replies, third.replies), ([], ["Salom"]))

    async def test_owner_typing_himself_cancels_pending_reply(self):
        import asyncio
        ctx = self.context(business_mode="all", business_debounce_seconds=0.1)
        await bot.on_message(SimpleNamespace(effective_message=self.msg("aka qachon ochilasiz?")), ctx)
        await bot.on_message(SimpleNamespace(effective_message=self.msg("soat 10 da", user_id=self.OWNER)), ctx)
        await asyncio.sleep(0.3)
        self.create.assert_not_awaited()
        self.assertEqual(self.store.private_turns(-50, 5), [("assistant", "(egam o'zi yozdi) soat 10 da")])

    async def test_pause_and_resume_commands(self):
        ctx = self.context(business_mode="all")
        owner_dm = FakeMessage("/pauza", chat_id=self.OWNER, chat_type="private", user_id=self.OWNER)
        await bot.cmd_pause(SimpleNamespace(effective_message=owner_dm), ctx)
        self.assertIn("to'xtatildi", owner_dm.replies[0])
        paused = self.msg("salom")
        await self.send(paused, ctx)
        self.assertEqual(paused.replies, [])
        self.assertEqual(self.store.get_flag("business_paused"), "1")
        stranger = FakeMessage("/davom", chat_id=77, chat_type="private", user_id=77)
        await bot.cmd_resume(SimpleNamespace(effective_message=stranger), ctx)
        self.assertEqual(self.store.get_flag("business_paused"), "1")   # only the owner can resume
        await bot.cmd_resume(SimpleNamespace(effective_message=owner_dm), ctx)
        resumed = self.msg("salom")
        await self.send(resumed, ctx)
        self.assertEqual(resumed.replies, ["Salom"])

    async def test_chat_memory_is_kept_per_business_chat(self):
        ctx = self.context(business_mode="all")
        await self.send(self.msg("mening ismim Ali"), ctx)
        await self.send(self.msg("ismim nima edi?"), ctx)
        roles = [item["role"] for item in self.create.await_args.kwargs["input"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(self.store.private_turns(50, 5)[:0], [])        # DM memory untouched
        self.assertEqual(len(self.store.private_turns(-50, 5)), 4)

    async def test_customer_over_limit_gets_silence_not_a_message(self):
        ctx = self.context(business_mode="all", daily_user_limit=1)
        await self.send(self.msg("birinchi savol"), ctx)
        second = self.msg("ikkinchi savol")
        await self.send(second, ctx)
        self.assertEqual(second.replies, [])
        self.assertEqual(self.create.await_count, 1)

    async def test_video_without_call_is_not_watched(self):
        ctx = self.context(business_mode="all")
        from video_queue import VideoQueue
        queue = VideoQueue(AsyncMock(), interval=600, max_pending=5)
        ctx.application.bot_data["video_queue"] = queue
        video = self.msg(None)
        video.video = SimpleNamespace(file_id="F", file_unique_id="U", file_size=1000)
        await self.send(video, ctx)
        self.assertEqual(queue.pending_count(), 0)
        self.assertIn("ko'rmading", self.create.await_args.kwargs["input"][-1]["content"])


class ConflictTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_instance_is_reported_to_owner_once(self):
        from telegram.error import Conflict
        send = AsyncMock()
        app = SimpleNamespace(bot_data={"settings": settings(owner_ids=frozenset({42}))})
        context = SimpleNamespace(application=app, error=Conflict("terminated by other getUpdates request"),
                                  bot=SimpleNamespace(send_message=send))
        await bot.on_error(None, context)
        await bot.on_error(None, context)
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[0], 42)

    def test_question_carries_current_date(self):
        items = build_input("mw4 qachon chiqadi")
        self.assertIn("Hozirgi vaqt (Toshkent): 20", items[-1]["content"])


VIDEO_DIR = os.path.join(tempfile.gettempdir(), "diddy-test-media")


def make_test_video(name="clip.mp4", seconds=6, audio=True):
    """Small real video made with the same ffmpeg the bot uses."""
    import subprocess
    from video import ffmpeg_exe
    os.makedirs(VIDEO_DIR, exist_ok=True)
    path = os.path.join(VIDEO_DIR, name)
    if not os.path.exists(path):
        args = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", f"testsrc=size=640x360:rate=25:duration={seconds}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "aac", "-shortest"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
        subprocess.run(args, check=True)
    return path


class VideoSamplingTests(unittest.TestCase):
    def test_frames_and_audio_from_real_video(self):
        from pathlib import Path
        from video import sample_video
        path = make_test_video()
        with tempfile.TemporaryDirectory() as tmp:
            sample = sample_video(Path(path), Path(tmp), frames=5, max_audio_seconds=600)
            self.assertAlmostEqual(sample.duration, 6, delta=0.3)
            self.assertEqual(len(sample.frames), 5)
            self.assertTrue(all(data[:2] == b"\xff\xd8" for _, data in sample.frames))
            self.assertIsNotNone(sample.audio_path)

    def test_broken_file_gives_friendly_error(self):
        from pathlib import Path
        from video import VideoError, sample_video
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.mp4"
            bad.write_bytes(b"not a video")
            with self.assertRaises(VideoError):
                sample_video(bad, Path(tmp), frames=4, max_audio_seconds=60)


class VideoQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_runs_now_rest_wait_interval_in_order(self):
        import asyncio
        from video_queue import AlreadyQueued, QueueFull, VideoJob, VideoQueue
        now = [1000.0]
        started = []
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(round(seconds))
            now[0] += seconds

        async def process(job):
            started.append((job.file_unique_id, now[0]))

        queue = VideoQueue(process, interval=600, max_pending=5, clock=lambda: now[0], sleep=fake_sleep)
        positions = [queue.submit(VideoJob(f"f{i}", f"u{i}", 1, -1)) for i in range(5)]
        self.assertEqual([p[0] for p in positions], [0, 1, 2, 3, 4])
        self.assertEqual([round(p[1]) for p in positions], [0, 600, 1200, 1800, 2400])
        with self.assertRaises(QueueFull):
            queue.submit(VideoJob("f9", "u9", 1, -1))
        with self.assertRaises(AlreadyQueued):
            queue.submit(VideoJob("again", "u2", 2, -1))
        queue.start()
        for _ in range(50):
            await asyncio.sleep(0)
            if len(started) == 5:
                break
        await queue.stop()
        self.assertEqual([u for u, _ in started], ["u0", "u1", "u2", "u3", "u4"])
        times = [t - 1000 for _, t in started]
        self.assertEqual(times, [0, 600, 1200, 1800, 2400])

    async def test_failed_job_does_not_stop_queue(self):
        import asyncio
        from video_queue import VideoJob, VideoQueue
        done = []

        async def process(job):
            if job.file_unique_id == "bad":
                raise RuntimeError("boom")
            done.append(job.file_unique_id)

        queue = VideoQueue(process, interval=0, max_pending=5)
        queue.submit(VideoJob("1", "bad", 1, -1))
        queue.submit(VideoJob("2", "good", 1, -1))
        queue.start()
        for _ in range(50):
            await asyncio.sleep(0)
        await queue.stop()
        self.assertEqual(done, ["good"])


class VideoFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import shutil
        self.dir = tempfile.TemporaryDirectory()
        self.settings = settings(daily_user_limit=5, video_interval_seconds=600)
        self.store = Storage(os.path.join(self.dir.name, "t.db"))
        self.video_path = make_test_video()
        self.create = AsyncMock(return_value={
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Bu nima kino 💀",
                                                        "annotations": []}]}],
            "usage": {"input_tokens": 2000, "output_tokens": 80, "input_tokens_details": {"cached_tokens": 0}}})
        self.transcribe = AsyncMock(return_value={"text": "salom hammaga"})
        client = SimpleNamespace(responses=SimpleNamespace(create=self.create),
                                 audio=SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcribe)))
        ai = DiddyAI(self.settings, client=client)
        source = self.video_path

        async def download_to_drive(path):
            shutil.copy(source, path)

        bot_obj = SimpleNamespace(id=999, username="DiddyBot", send_chat_action=AsyncMock(),
                                  get_file=AsyncMock(return_value=SimpleNamespace(
                                      download_to_drive=download_to_drive)))
        self.app = SimpleNamespace(bot=bot_obj, bot_data={"settings": self.settings, "storage": self.store,
                                                          "ai": ai})
        from video_queue import VideoQueue
        self.processed = []

        async def process(job):
            await bot.process_video(self.app, job)
            self.processed.append(job)

        self.queue = VideoQueue(process, interval=600, max_pending=5)
        self.app.bot_data["video_queue"] = self.queue
        self.context = SimpleNamespace(application=self.app, args=[], bot=bot_obj)

    async def asyncTearDown(self):
        await self.queue.stop()
        self.store.close()
        self.dir.cleanup()

    def video_message(self, uid="vid1", user_id=7, size=500_000):
        msg = FakeMessage(None, user_id=user_id)
        msg.video = SimpleNamespace(file_id="F-" + uid, file_unique_id=uid, file_size=size)
        return msg

    async def run_queue(self, count=1):
        import asyncio
        self.queue.start()
        for _ in range(400):
            await asyncio.sleep(0.01)
            if len(self.processed) >= count:
                break

    async def test_reply_diddy_to_video_is_watched(self):
        video = self.video_message()
        ask = FakeMessage("diddy bu nima?", reply_to=video)
        await bot.on_message(SimpleNamespace(effective_message=ask), self.context)
        self.assertEqual(ask.replies[0], "Ko'ryapman 👀")
        await self.run_queue()
        self.assertEqual(ask.replies[-1], "Bu nima kino 💀")
        content = self.create.await_args.kwargs["input"][0]["content"]
        images = [c for c in content if c["type"] == "input_image"]
        self.assertEqual(len(images), self.settings.video_frames)
        self.assertIn("salom hammaga", content[0]["text"])
        self.transcribe.assert_awaited_once()
        self.assertGreater(self.store.month_spend()["usd"], 0)

    async def test_plain_video_and_video_without_reply_are_ignored(self):
        await bot.on_message(SimpleNamespace(effective_message=self.video_message()), self.context)
        captioned = self.video_message("vid2")
        captioned.caption = "diddy qara"
        await bot.on_message(SimpleNamespace(effective_message=captioned), self.context)
        self.assertEqual(self.queue.pending_count(), 0)
        self.assertIn("reply", captioned.replies[0])
        self.create.assert_not_awaited()

    async def test_video_sent_as_reply_to_bot_is_watched(self):
        bot_msg = FakeMessage("oldingi javob", user_id=999)
        video = self.video_message()
        video.reply_to_message = bot_msg
        await bot.on_message(SimpleNamespace(effective_message=video), self.context)
        self.assertEqual(self.queue.pending_count(), 1)

    async def test_many_videos_queue_in_order_with_interval_message(self):
        replies = []
        for i in range(3):
            ask = FakeMessage("diddy qara", reply_to=self.video_message(f"v{i}", user_id=10 + i), user_id=10 + i)
            await bot.on_message(SimpleNamespace(effective_message=ask), self.context)
            replies.append(ask.replies[0])
        self.assertEqual(replies[0], "Ko'ryapman 👀")
        self.assertIn("oldingda 1 ta", replies[1])
        self.assertIn("10 daqiqa", replies[1])
        self.assertIn("oldingda 2 ta", replies[2])
        self.assertIn("20 daqiqa", replies[2])

    async def test_too_big_video_is_rejected_without_cost(self):
        ask = FakeMessage("diddy qara", reply_to=self.video_message(size=50 * 1024 * 1024))
        await bot.on_message(SimpleNamespace(effective_message=ask), self.context)
        self.assertIn("MB", ask.replies[0])
        self.assertEqual(self.queue.pending_count(), 0)
        self.assertEqual(self.store.daily_used(5), 0)


class UsbWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_stops_when_its_files_disappear(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "bot.py"
            marker.write_text("x")
            stopped = []
            import asyncio
            task = asyncio.create_task(bot.watch_usb(marker, interval=0.01, on_lost=lambda: stopped.append(1)))
            await asyncio.sleep(0.05)
            self.assertEqual(stopped, [])          # USB still there
            marker.unlink()                        # "unplug"
            await asyncio.wait_for(task, 1)
            self.assertEqual(stopped, [1])


def _real_openai() -> bool:
    import openai
    return not getattr(openai, "__stub__", False)


if __name__ == "__main__":
    unittest.main()
