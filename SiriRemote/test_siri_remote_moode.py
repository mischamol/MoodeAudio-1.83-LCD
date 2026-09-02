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


class FakeOverlayWorker:
    def __init__(self):
        self.submissions = []
        self.sequences = []
        self.countdowns = []
        self.cancellations = []
        self.enabled = True

    def submit(self, text, duration=None, kind="command"):
        self.submissions.append((text, duration, kind))

    def submit_sequence(self, frames, kind="command"):
        self.sequences.append((frames, kind))

    def start_shutdown(self, seconds):
        self.countdowns.append(seconds)

    def cancel(self, kind=None):
        self.cancellations.append(kind)

class FakeRendererGuard:
    def __init__(self, allowed):
        self.allowed = allowed
        self.actions = []

    def allows(self, action_name):
        self.actions.append(action_name)
        return self.allowed


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

    def test_touch_navigation_waits_for_success_before_overlay(self):
        overlay = FakeOverlayWorker()
        mapper = remote.ButtonMapper(
            self.worker,
            shutdown_action=lambda: None,
            overlay_worker=overlay,
        )
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            touch_report(2500, remote.BUTTON_TOUCHPAD),
        )
        mapper.notification(remote.HANDLE_INPUT_VALUE, bytes((0, 0)))
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            touch_report(3600, remote.BUTTON_TOUCHPAD),
        )
        self.assertEqual(overlay.submissions, [])
        mapper.reset()

    def test_home_starts_and_release_cancels_shutdown_overlay(self):
        overlay = FakeOverlayWorker()
        mapper = remote.ButtonMapper(
            self.worker,
            shutdown_action=lambda: None,
            overlay_worker=overlay,
        )
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            bytes((0, mapper.home_mask)),
        )
        mapper.notification(remote.HANDLE_INPUT_VALUE, bytes((0, 0)))
        self.assertEqual(overlay.countdowns, [3.0])
        self.assertIn("shutdown", overlay.cancellations)
        mapper.reset()

    def test_microphone_click_shows_cached_battery(self):
        shown = []
        mapper = remote.ButtonMapper(
            self.worker,
            shutdown_action=lambda: None,
            battery_display_action=lambda: shown.append(True),
        )
        mapper.notification(
            remote.HANDLE_INPUT_VALUE,
            bytes((0, mapper.mic_mask)),
        )
        self.assertEqual(shown, [True])
        self.assertEqual(self.worker.actions, [])
        mapper.reset()


class RendererGuardTests(unittest.TestCase):
    def test_active_renderers_recognizes_all_enabled_flags(self):
        data = {flag: "0" for flag in remote.RendererGuard.ACTIVE_FLAGS}
        data.update({"aplactive": "1", "spotactive": 1, "rxactive": "1"})
        self.assertEqual(
            remote.RendererGuard.active_renderers(data),
            ("aplactive", "spotactive", "rxactive"),
        )

    def test_active_renderers_rejects_invalid_response(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            remote.RendererGuard.active_renderers([])

    def test_active_renderer_blocks_action_and_status_failure_fails_closed(self):
        blocked = []
        guard = remote.RendererGuard(
            "http://localhost/command/", 4, blocked_handler=blocked.append,
        )
        with mock.patch.object(guard, "check", return_value=("aplactive",)):
            self.assertFalse(guard.allows("Play/Pause"))
        with mock.patch.object(guard, "check", return_value=None):
            self.assertFalse(guard.allows("Volume +"))
        self.assertEqual(blocked, ["AirPlay", "Renderer"])

    def test_every_renderer_flag_has_a_display_name(self):
        self.assertEqual(set(remote.RendererGuard.ACTIVE_FLAGS), set(remote.RENDERER_NAMES))
        self.assertEqual(remote.RENDERER_NAMES["rxactive"], "Multiroom Receiver")

    def test_no_active_renderer_allows_action(self):
        blocked = []
        guard = remote.RendererGuard(
            "http://localhost/command/", 4, blocked_handler=blocked.append,
        )
        with mock.patch.object(guard, "check", return_value=()):
            self.assertTrue(guard.allows("Play/Pause"))
        self.assertEqual(blocked, [])


class MoodeWorkerTests(unittest.TestCase):
    def test_active_renderer_prevents_http_request(self):
        guard = FakeRendererGuard(False)
        worker = remote.MoodeWorker(
            "http://localhost/command/", 1, renderer_guard=guard,
        )
        with mock.patch.object(remote.urllib.request, "urlopen") as urlopen:
            worker.start()
            worker.submit("Play/Pause", "toggle_play_pause")
            worker.stop()
            worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        urlopen.assert_not_called()
        self.assertEqual(guard.actions, ["Play/Pause"])


class X11ClickWorkerTests(unittest.TestCase):
    def test_active_renderer_prevents_menu_click(self):
        guard = FakeRendererGuard(False)
        with mock.patch.dict(
            os.environ, {"SIRI_MENU_SCREEN_CLICK": "yes"}, clear=False
        ):
            clicker = remote.X11ClickWorker(guard)
        with mock.patch.object(clicker, "_ensure_playback"), \
                mock.patch.object(clicker, "_click") as click:
            clicker.start()
            clicker.submit()
            clicker.stop()
            clicker.thread.join(1)
        self.assertFalse(clicker.thread.is_alive())
        click.assert_not_called()
        self.assertEqual(guard.actions, ["Menu/Back"])

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


class X11OverlayTests(unittest.TestCase):
    def test_darken_pixel_preserves_channels(self):
        self.assertEqual(remote.X11Overlay._darken_pixel(0xCC8040, 0.5), 0x664020)

    def test_darken_black_stays_black(self):
        self.assertEqual(remote.X11Overlay._darken_pixel(0x000000), 0x000000)

    def test_blend_pixel_applies_tint_and_opacity(self):
        self.assertEqual(
            remote.X11Overlay._blend_pixel(0x000000, 0xC0C0C0, 0.5),
            0x606060,
        )

    def test_overlay_colors_match_moode_defaults(self):
        self.assertEqual(remote.X11Overlay.MOODE_GREY, 0x303030)
        self.assertEqual(remote.X11Overlay.MOODE_TEXT, (240 / 255.0,) * 3)
        self.assertEqual(remote.X11Overlay.OVERLAY_OPACITY, 0.60)
        self.assertEqual(remote.X11Overlay.OVERLAY_BORDER_OPACITY, 0.38)
        self.assertEqual(remote.X11Overlay.OVERLAY_BORDER_WIDTH, 0.004)
        self.assertEqual(remote.X11Overlay.REPAINT_SETTLE_SECONDS, 0.05)

    def test_shutdown_ring_position_is_shared_by_countdown_and_final_frame(self):
        self.assertEqual(remote.X11Overlay.SHUTDOWN_LABEL_CENTER_Y, 0.30)
        self.assertEqual(remote.X11Overlay.SHUTDOWN_RING_CENTER_Y, 0.64)

    def test_overlay_is_centered_inside_portrait_cover(self):
        size, left, top = remote.X11Overlay._overlay_geometry(720, 1280)
        self.assertEqual((size, left, top), (536, 92, 109))
        self.assertEqual(left + size // 2, 360)

    def test_play_is_a_vector_symbol(self):
        self.assertEqual(
            remote.X11Overlay.SYMBOLS,
            {"PLAY", "PAUSE", "NEXT", "PREVIOUS", "BATTERY"},
        )

    def test_text_layout_scales_and_normalizes(self):
        text, scale, width, height = remote.X11Overlay._text_layout("test!", 360)
        self.assertEqual(text, "TEST?")
        self.assertGreaterEqual(scale, 5)
        self.assertLess(width, 360)
        self.assertEqual(height, scale * 7)

    def test_disabled_overlay_has_every_required_glyph(self):
        self.assertTrue(set("DISABLED") <= remote.X11Overlay.FONT_5X7.keys())

    def test_short_text_scales_with_large_overlay(self):
        _text, small_scale, _width, _height = remote.X11Overlay._text_layout("TEST", 360)
        _text, large_scale, width, _height = remote.X11Overlay._text_layout("TEST", 696)
        self.assertGreater(large_scale, small_scale)
        self.assertLess(width, 696)


class OverlayWorkerTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {"SIRI_OVERLAY": "yes", "SIRI_OVERLAY_SECONDS": "1"},
            clear=False,
        )
        self.env.start()
        self.worker = remote.OverlayWorker()

    def tearDown(self):
        self.env.stop()

    def test_volume_response_uses_returned_percentage(self):
        self.worker.moode_result(
            "Volume +", "set_volume -up 5", b'{"volume":"60","muted":"no"}',
        )
        self.assertEqual(
            self.worker.latest,
            ("command", [("VOLUME:60%", 1.0)]),
        )

    def test_toggle_response_uses_resulting_state(self):
        self.worker.moode_result(
            "Play/Pause", "toggle_play_pause", b'{"state":"play"}',
        )
        self.assertEqual(self.worker.latest[1][0][0], "PLAY")
        self.worker.moode_result(
            "Play/Pause", "toggle_play_pause", b'{"state":"stop"}',
        )
        self.assertEqual(self.worker.latest[1][0][0], "PAUSE")

    def test_navigation_response_uses_button_and_accepts_non_json_body(self):
        self.worker.moode_result(
            "Touchpad left / Previous", "custom-previous", b"",
        )
        self.assertEqual(self.worker.latest[1][0][0], "PREVIOUS")
        self.worker.moode_result(
            "Touchpad right / Next", "custom-next", b"OK",
        )
        self.assertEqual(self.worker.latest[1][0][0], "NEXT")

    def test_latest_volume_replaces_stale_pending_volume(self):
        for volume in ("50", "55", "60"):
            self.worker.moode_result(
                "Volume +",
                "set_volume -up 5",
                ('{"volume":"%s"}' % volume).encode(),
            )
        self.assertEqual(self.worker.latest[1], [("VOLUME:60%", 1.0)])

    def test_shutdown_sequence_counts_down_without_fifo_items(self):
        self.worker.start_shutdown(3.0)
        self.assertEqual(
            self.worker.latest,
            (
                "shutdown",
                [
                    ("SHUTDOWN:3", 1.0),
                    ("SHUTDOWN:2", 1.0),
                    ("SHUTDOWN:1", 1.0),
                ],
            ),
        )

class BatteryMonitorTests(unittest.TestCase):
    def test_five_to_nine_shows_one_second_warning(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(
            overlay, threshold=10, critical_threshold=5,
        )
        self.assertTrue(monitor.update(9))
        self.assertEqual(
            overlay.submissions,
            [("BATTERY:9%", 1.0, "battery-low")],
        )

    def test_below_five_flashes_three_times(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(
            overlay, threshold=10, critical_threshold=5,
        )
        self.assertTrue(monitor.update(4))
        self.assertEqual(
            overlay.sequences,
            [
                (
                    [
                        ("BATTERY:4%", 0.45),
                        ("HIDE", 0.25),
                        ("BATTERY:4%", 0.45),
                        ("HIDE", 0.25),
                        ("BATTERY:4%", 0.45),
                    ],
                    "battery-critical",
                )
            ],
        )

    def test_ten_stops_periodic_warning(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(overlay, threshold=10)
        self.assertTrue(monitor.update(9))
        overlay.submissions.clear()
        self.assertFalse(monitor.update(10))
        self.assertEqual(overlay.submissions, [])
        self.assertEqual(overlay.sequences, [])

    def test_invalid_battery_level_is_ignored(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(overlay, threshold=10)
        self.assertFalse(monitor.update(101))
        self.assertEqual(overlay.submissions, [])
        self.assertEqual(overlay.sequences, [])

    def test_battery_click_displays_cached_percentage(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(overlay, threshold=10)
        monitor.update(80)
        monitor.show_current()
        self.assertEqual(overlay.submissions[-1][0], "BATTERY:80%")

    def test_early_battery_click_is_shown_after_initial_read(self):
        overlay = FakeOverlayWorker()
        monitor = remote.BatteryMonitor(overlay, threshold=10)
        monitor.show_current()
        self.assertEqual(overlay.submissions, [])
        monitor.update(83)
        self.assertEqual(overlay.submissions[-1][0], "BATTERY:83%")


class RawAttClientTests(unittest.TestCase):
    def test_battery_interval_tracks_latest_att_reading(self):
        client = remote.RawAttClient(
            "70:48:0F:F2:65:99", "public", "medium",
        )
        expected = ((80, 900), (10, 900), (9, 300), (5, 300), (4, 60))
        for level, interval in expected:
            with self.subTest(level=level):
                client.battery_level = level
                self.assertEqual(
                    client.battery_interval(900, 300, 60, 10, 5),
                    interval,
                )



if __name__ == "__main__":
    unittest.main()
