#!/usr/bin/env python3
import os
import unittest
from unittest import mock

import siri_remote_moode as remote


class FakeWorker:
    def __init__(self):
        self.actions = []

    def submit(self, name, command):
        self.actions.append((name, command))


class FakeClicker:
    def __init__(self):
        self.clicks = 0

    def submit(self):
        self.clicks += 1


def touch_report(x, buttons=0, pressure=40):
    payload = bytearray(13)
    payload[0] = 1
    payload[1] = buttons
    payload[2] = remote.TOUCH_EVENT_MARKER
    raw_x = x & 0x0FFF
    payload[6] = raw_x & 0xFF
    payload[7] = (raw_x >> 8) & 0x0F
    payload[10] = pressure
    payload[11] = pressure
    return bytes(payload)


class ButtonMapperTests(unittest.TestCase):
    def setUp(self):
        env = {
            "SIRI_TOUCH_X_SPLIT": "3096",
            "SIRI_TOUCH_DEAD_ZONE": "60",
            "SIRI_TOUCH_MAX_AGE_SECONDS": "1.5",
            "MOODE_PREVIOUS_CMD": "previous",
            "MOODE_NEXT_CMD": "next",
        }
        self.env = mock.patch.dict(os.environ, env, clear=False)
        self.env.start()
        self.worker = FakeWorker()
        self.mapper = remote.ButtonMapper(self.worker, shutdown_action=lambda: None)

    def tearDown(self):
        self.mapper.reset()
        self.env.stop()

    def notify(self, payload):
        self.mapper.notification(remote.HANDLE_INPUT_VALUE, payload)

    def test_touch_decode_uses_gen1_wrapped_12_bit_range(self):
        self.assertEqual(self.mapper.decode_touch_x(touch_report(2500)), 2500)
        # A raw value below 0x800 wraps into the upper 12-bit range.
        self.assertEqual(self.mapper.decode_touch_x(touch_report(0x020)), 0x1020)

    def test_left_position_followed_by_click_is_previous_once(self):
        self.notify(touch_report(2500))
        self.notify(bytes((0, remote.BUTTON_TOUCHPAD)))
        self.notify(bytes((0, remote.BUTTON_TOUCHPAD)))
        self.assertEqual(
            self.worker.actions,
            [("Touchpad left / Previous", "previous")],
        )

    def test_right_click_report_is_next_once(self):
        self.notify(touch_report(3600, remote.BUTTON_TOUCHPAD))
        self.notify(touch_report(3700, remote.BUTTON_TOUCHPAD))
        self.assertEqual(
            self.worker.actions,
            [("Touchpad right / Next", "next")],
        )

    def test_click_can_arrive_before_its_touch_position(self):
        self.notify(bytes((0, remote.BUTTON_TOUCHPAD)))
        self.assertEqual(self.worker.actions, [])
        self.notify(touch_report(2500, remote.BUTTON_TOUCHPAD))
        self.assertEqual(
            self.worker.actions,
            [("Touchpad left / Previous", "previous")],
        )

    def test_release_allows_the_next_click(self):
        self.notify(touch_report(2500, remote.BUTTON_TOUCHPAD))
        self.notify(bytes((0, 0)))
        self.notify(touch_report(3600, remote.BUTTON_TOUCHPAD))
        self.assertEqual([action[1] for action in self.worker.actions], ["previous", "next"])

    def test_center_dead_zone_is_ignored(self):
        self.notify(touch_report(3096, remote.BUTTON_TOUCHPAD))
        self.notify(touch_report(2500, remote.BUTTON_TOUCHPAD))
        self.assertEqual(self.worker.actions, [])

    def test_touch_without_physical_click_does_nothing(self):
        self.notify(touch_report(2500))
        self.notify(touch_report(3600))
        self.assertEqual(self.worker.actions, [])

    def test_play_pause_still_works_after_each_release(self):
        for _ in range(2):
            self.notify(bytes((0, remote.BUTTON_PLAY_PAUSE)))
            self.notify(bytes((0, 0)))
        self.assertEqual(
            [action[1] for action in self.worker.actions],
            ["toggle_play_pause", "toggle_play_pause"],
        )

    def test_menu_button_submits_one_screen_click_per_press(self):
        clicker = FakeClicker()
        mapper = remote.ButtonMapper(
            self.worker,
            shutdown_action=lambda: None,
            screen_clicker=clicker,
        )
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            bytes((0, remote.BUTTON_MENU)),
        )
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            bytes((0, remote.BUTTON_MENU)),
        )
        mapper.notification(remote.HANDLE_INPUT_VALUE, bytes((0, 0)))
        self.assertEqual(clicker.clicks, 1)
        self.assertEqual(self.worker.actions, [])
        mapper.reset()


class X11ClickWorkerTests(unittest.TestCase):
    def test_menu_uses_actual_moode_view_for_both_directions(self):
        env = {
            "SIRI_MENU_SCREEN_CLICK": "yes",
            "SIRI_MENU_CLICK_X": "360",
            "SIRI_MENU_CLICK_Y": "960",
            "SIRI_PLAYBACK_CLICK_X": "220",
            "SIRI_PLAYBACK_CLICK_Y": "1135",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            clicker = remote.X11ClickWorker()
        with mock.patch.object(clicker, "_emit_click") as emit, \
                mock.patch.object(clicker, "_find_coverart", return_value=(360, 320)), \
                mock.patch.object(
                    clicker, "_get_current_view", side_effect=["playback,album", "album"]
                ):
            clicker._click()
            clicker._click()
        self.assertEqual(
            emit.call_args_list,
            [mock.call(360, 320), mock.call(220, 1135)],
        )

    def test_menu_supports_every_library_origin_in_both_directions(self):
        for origin in ("radio", "album", "folder", "tag", "playlist"):
            with self.subTest(origin=origin):
                with mock.patch.dict(
                    os.environ, {"SIRI_MENU_SCREEN_CLICK": "yes"}, clear=False
                ):
                    clicker = remote.X11ClickWorker()
                with mock.patch.object(clicker, "_emit_click") as emit, \
                        mock.patch.object(
                            clicker, "_find_coverart", return_value=(360, 320)
                        ), mock.patch.object(
                            clicker,
                            "_get_current_view",
                            side_effect=[f"playback,{origin}", origin],
                        ):
                    clicker._click()
                    clicker._click()
                self.assertEqual(
                    emit.call_args_list,
                    [mock.call(360, 320), mock.call(220, 1135)],
                )

    def test_startup_sync_uses_playback_coordinate(self):
        env = {
            "SIRI_MENU_SCREEN_CLICK": "yes",
            "SIRI_PLAYBACK_CLICK_X": "220",
            "SIRI_PLAYBACK_CLICK_Y": "1135",
            "SIRI_X_READY_TIMEOUT": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            clicker = remote.X11ClickWorker()
        with mock.patch.object(clicker, "_emit_click") as emit:
            clicker._ensure_playback()
        emit.assert_called_once_with(220, 1135)

    def test_cover_target_scales_with_display(self):
        self.assertEqual(remote.X11ClickWorker._coverart_target(720, 1280), (360, 320))
        self.assertEqual(remote.X11ClickWorker._coverart_target(1080, 1920), (540, 480))

    def test_cover_target_refuses_invalid_display(self):
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            remote.X11ClickWorker._coverart_target(0, 1280)


if __name__ == "__main__":
    unittest.main()
