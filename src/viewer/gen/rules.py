# -*- coding: utf-8 -*-
"""驱动规则加载 — 读 drivers/{vocab,devices}.yaml → DriverRules

设备匹配优先级: valve 类先于 motor 类 (保持原 _match_device(VALVE) 先于 MOTOR 的行为).
匹配语义: 描述含任一 include 词, 且不含任何排除词. motor 排除 = motor_exclude_common.
"""
from pathlib import Path
from typing import List, Optional

import yaml


class DriverRules:
    def __init__(self, vocab: dict, devices: list, motor_exclude_common: list):
        self.vocab = vocab
        self.devices = devices                       # 已按 valve→motor 排序
        self.motor_exclude_common = motor_exclude_common

    def _excludes_for(self, dev: dict) -> List[str]:
        ex = list(dev.get("exclude_extra") or [])
        if dev.get("type") == "motor":
            ex = list(self.motor_exclude_common) + ex
        return ex

    def match_device(self, desc: str) -> Optional[dict]:
        if not desc:
            return None
        # valve 类先匹配, 再 motor 类 (与原代码先 VALVE_DEVICES 后 MOTOR_DEVICES 一致)
        for want_type in ("valve", "motor"):
            for dev in self.devices:
                if dev.get("type") != want_type:
                    continue
                if any(w in desc for w in dev["include"]):
                    if not any(w in desc for w in self._excludes_for(dev)):
                        return dev
        return None


def load_rules(project_paths) -> DriverRules:
    d = project_paths.drivers_dir
    vocab = yaml.safe_load((Path(d) / "vocab.yaml").read_text(encoding="utf-8")) or {}
    dev_doc = yaml.safe_load((Path(d) / "devices.yaml").read_text(encoding="utf-8")) or {}
    devices = dev_doc.get("devices") or []
    motor_exclude = dev_doc.get("motor_exclude_common") or []
    return DriverRules(vocab, devices, motor_exclude)
