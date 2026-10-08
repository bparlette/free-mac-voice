"""Long replies are spoken sentence by sentence so the first word does not wait for the whole reply to be synthesized."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import free_voice as fv  # noqa: E402

LONG = ("The meeting is at three in the afternoon so you have about two hours left. "
        "Start with the summary because everyone reads that first. "
        "Then check the numbers in the table against the spreadsheet. "
        "Send it to the team before the call starts.")


class TestSplit(unittest.TestCase):
    def test_splits_into_sentences_and_loses_nothing(self):
        chunks = fv._split_for_streaming(LONG)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), LONG)

    def test_short_fragments_are_joined_to_a_neighbour(self):
        chunks = fv._split_for_streaming("Yes. No. The quarterly numbers are finished and filed. Ok.")
        self.assertTrue(all(len(c) >= 10 for c in chunks))
        self.assertEqual(" ".join(chunks), "Yes. No. The quarterly numbers are finished and filed. Ok.")

    def test_single_sentence_is_one_chunk(self):
        self.assertEqual(fv._split_for_streaming("Just one sentence here."), ["Just one sentence here."])


class FakeProc:
    def __init__(self, release=None):
        self.release = release; self.terminated = False
    def wait(self):
        if self.release is not None:
            self.release.wait(5)
    def poll(self):
        return None if (self.release is not None and not self.release.is_set() and not self.terminated) else 0
    def terminate(self):
        self.terminated = True
        if self.release is not None:
            self.release.set()


class TestStreamedSay(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(fv, "DRY_RUN", False), mock.patch.object(fv, "VOICE_QUIET_MODE", False),
                        mock.patch.object(fv, "VOICE_TTS_ENGINE", "kokoro"), mock.patch.object(fv, "VOICE_TTS_STREAM", True),
                        mock.patch.object(fv, "_get_kokoro", return_value=object()), mock.patch.object(fv, "notify_hud", lambda *a, **k: None),
                        mock.patch.object(fv, "log", lambda *a, **k: None), mock.patch.object(fv.os, "remove", lambda p: None)):
            patcher.start(); self.addCleanup(patcher.stop)
        fv._say_proc = None
        self.addCleanup(setattr, fv, "_say_proc", None)

    def test_chunks_play_in_order_and_next_is_synthesized_while_playing(self):
        events = []
        def synth(text, voice):
            events.append(("synth", text[:12])); return "/tmp/" + text[:5]
        def popen(cmd, **k):
            events.append(("play", cmd[1])); return FakeProc()
        chunks = fv._split_for_streaming(LONG)
        with mock.patch.object(fv, "_kokoro_temp_wav", side_effect=synth), mock.patch.object(fv.subprocess, "Popen", side_effect=popen):
            fv.say(LONG, blocking=True)
        plays = [e[1] for e in events if e[0] == "play"]
        self.assertEqual(plays, ["/tmp/" + c[:5] for c in chunks])
        self.assertEqual(events[0][0], "synth")
        self.assertEqual(events[1][0], "play")        # first chunk starts playing before the second is synthesized
        self.assertEqual(events[2][0], "synth")
        self.assertFalse(fv.is_speaking())

    def test_a_new_say_cuts_off_the_streamed_reply(self):
        release = threading.Event(); played = []
        def popen(cmd, **k):
            if cmd[0] == "afplay":
                played.append(cmd[1])
            return FakeProc(release)
        with mock.patch.object(fv, "_kokoro_temp_wav", side_effect=lambda t, v: "/tmp/" + t[:5]), mock.patch.object(fv.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(fv, "_synthesize_kokoro", return_value=None):
            fv.say(LONG)                                   # starts speaking in the background
            for _ in range(100):
                if played: break
                threading.Event().wait(0.02)
            self.assertEqual(len(played), 1)
            first = fv._say_proc
            fv.say("Done.")                                # newer speech: terminates the current clip and stops the old reply
            self.assertTrue(first.terminated)
            for _ in range(100):
                if fv._say_streams_running == 0: break
                threading.Event().wait(0.02)
            self.assertEqual(fv._say_streams_running, 0)
            self.assertEqual(len(played), 1)               # no further chunk of the old reply was started

    def test_short_replies_and_the_off_switch_use_the_normal_path(self):
        with mock.patch.object(fv, "_synthesize_kokoro", return_value=None) as synth, mock.patch.object(fv.subprocess, "Popen") as popen, \
             mock.patch.object(fv, "_say_streamed") as streamed:
            fv.say("Volume 30", blocking=True)
            with mock.patch.object(fv, "VOICE_TTS_STREAM", False):
                fv.say(LONG, blocking=True)
        streamed.assert_not_called()
        self.assertEqual(synth.call_count, 2)


if __name__ == "__main__":
    unittest.main()
