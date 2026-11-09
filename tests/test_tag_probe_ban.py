"""slave_ban 映射核查 (§40-①) 的回归测试。

锁定两件事,都在本次目标边界内 (src/env + .scratch):
1. src/env/action_space.py::compute_mask 保留 slave_ban 注记
   (真门控 op/op_stop,常规换人不写 ban);
2. .scratch/slave_ban_probe.json 四字段齐全,实战分布明确标为不可用
   (tag_probe 经掩码过滤,全 0 是构造保证,不能当证否依据)。

方法论: Tag 动作仅在 slave_ban==0 时可被采样(action_space.py:247 掩码),
故 probe 样本分布不可用于判断"逐帧恒 0";证否依据是静态赋值扫描。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class TestSlaveBanMapping(unittest.TestCase):
    def test_compute_mask_doc_mentions_mapping(self):
        src = (ROOT / "src" / "env" / "action_space.py").read_text(encoding="utf-8")
        self.assertIn("40", src)
        self.assertIn("slave_ban", src)
        self.assertIn("op_stop", src)

    def test_probe_json_contract(self):
        p = ROOT / ".scratch" / "slave_ban_probe.json"
        self.assertTrue(p.exists(), "探针 JSON 未落盘,先跑 .scratch/slave_ban_cooldown_probe.py")
        doc = json.loads(p.read_text(encoding="utf-8"))
        for field in ("samples", "mapping", "conclusion", "code_ref"):
            self.assertIn(field, doc)
        self.assertGreaterEqual(int(doc["samples"]), 50)
        self.assertIn("src/env/action_space.py", str(doc["code_ref"]))
        self.assertFalse(doc["mapping"].get("battle_dist_usable", True),
                         "实战分布必须标为不可用(掩码构造保证)")
        assigns = doc["mapping"].get("assignments", [])
        self.assertTrue(any(a.get("value") == "480" for a in assigns),
                        "应含紫苑封印 480 赋值")


if __name__ == "__main__":
    unittest.main()
