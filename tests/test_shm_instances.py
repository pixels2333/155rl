"""BridgeShm 多实例命名与锁步放行(用假 kernel32,不需要游戏)。"""
from __future__ import annotations

import ctypes
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from bridge import shm as shm_mod                    # noqa: E402


class _FakeK32:
    """只记录调用;映射/事件一律打不开,WaitForSingleObject 时模拟 DLL 发布下一帧。"""

    def __init__(self, shared=None):
        self.names = []
        self.events = []
        self.shared = shared

    def OpenFileMappingW(self, access, inherit, name):
        self.names.append(name)
        return 0

    def OpenEventW(self, access, inherit, name):
        self.events.append(name)
        return 7 if name.startswith(shm_mod.STATE_EVENT_PREFIX) else 0

    def WaitForSingleObject(self, h, ms):
        # DLL 侧:看到 ctl_step_seq >= ls_seq 就放行并发布下一帧边界。
        s = self.shared
        if s.ctl_lockstep and s.ctl_step_seq >= s.ls_seq:
            s.ls_seq += 1
            s.ls_frame += 1
        return 0

    def CloseHandle(self, h):
        return 1


class TestOpenByPid(unittest.TestCase):
    def test_explicit_pid_uses_suffixed_name(self):
        fake = _FakeK32()
        with mock.patch.object(shm_mod, "k32", fake):
            with self.assertRaises(TimeoutError):
                shm_mod.BridgeShm.open(1234, timeout=0.0)
        self.assertEqual(set(fake.names), {"Local\\th155_rl_bridge_v1_1234"})

    def test_no_pid_scans_every_instance(self):
        fake = _FakeK32()
        with mock.patch.object(shm_mod, "k32", fake), \
             mock.patch("th155.mem.find_pids", lambda name="th155.exe": [11, 22]):
            with self.assertRaises(TimeoutError):
                shm_mod.BridgeShm.open(timeout=0.0)
        self.assertIn("Local\\th155_rl_bridge_v1_11", fake.names)
        self.assertIn("Local\\th155_rl_bridge_v1_22", fake.names)


class TestLockstepAdvance(unittest.TestCase):
    def test_advance_releases_each_frame(self):
        buf = shm_mod.Shared()
        fake = _FakeK32(buf)
        obj = shm_mod.BridgeShm(pid=99)
        obj.s = buf
        with mock.patch.object(shm_mod, "k32", fake):
            obj.enable_lockstep(timeout_ms=50)
            self.assertEqual(buf.ctl_lockstep_timeout_ms, 50)
            self.assertTrue(obj.advance(3, timeout=0.5))
            self.assertEqual(buf.ls_seq, 3)
            self.assertEqual(buf.ctl_step_seq, 2)
            obj.disable_lockstep()
        self.assertEqual(buf.ctl_lockstep, 0)
        self.assertIn("Local\\th155_rl_state_99", fake.events)

    def test_advance_times_out_without_dll(self):
        buf = shm_mod.Shared()
        obj = shm_mod.BridgeShm(pid=99)
        obj.s = buf
        with mock.patch.object(shm_mod, "k32", _FakeK32(buf)):
            # 锁步未开:假 DLL 不发布新边界 -> 超时
            self.assertFalse(obj.advance(1, timeout=0.02))


if __name__ == "__main__":
    unittest.main()
