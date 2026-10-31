#!/usr/bin/env python3
"""Gra智能控制器V7 —— 基本读写示例。

运行前先配好 token（任选其一）：
    export MIIO_IP=192.168.31.194 MIIO_TOKEN=<32位hex> MIIO_DID=2031986608
    # 或把 .env.example 复制成 .miio-device-token 填好放在本项目目录

运行：
    .venv/bin/python examples/basic.py
"""

from __future__ import annotations

import sys

# 允许从仓库根直接 import gra_switch
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from gra_switch import (  # noqa: E402
    GraSwitch,
    GraSwitchAuthError,
    GraSwitchError,
    GraSwitchOfflineError,
    GraSwitchTimeoutError,
    GraSwitchVerifyError,
)


def main() -> int:
    try:
        # 1) 连接（配置走环境变量或默认查找链）
        with GraSwitch(timeout=5) as sw:
            print(f"已连接 {sw.ip}，token={sw.token[:4]}...{sw.token[-4:]}")

            # 2) 读状态 —— 走 7-1 真状态位
            #    超时会抛 GraSwitchTimeoutError，此时状态**未知**，
            #    绝不能把它当成 False（关）继续做决策。
            try:
                state = sw.status()
            except GraSwitchTimeoutError as exc:
                print(f"设备无应答，状态未知：{exc}", file=sys.stderr)
                print("提示：不要把它当作「关」，稍后重试或先检查设备在线", file=sys.stderr)
                return 4
            print(f"当前状态：{'开' if state else '关'}")

            # 3) 翻转并验证 —— 内部写 2-1，回读 7-1 轮询确认
            target = not state
            print(f"尝试切换到：{'开' if target else '关'} ...")
            ok = sw.on() if target else sw.off()

            if not ok:
                print("写入未通过回读验证", file=sys.stderr)
                return 1

            # 4) 再读一次确认
            actual = sw.status()
            print(f"写入后状态：{'开' if actual else '关'}")

            # 5) 恢复原状态（避免改变现场）
            print(f"恢复原状态：{'开' if state else '关'} ...")
            sw.on() if state else sw.off()
            print("已恢复：", "开" if sw.status() else "关")

            # 6) 设备信息
            print("--- 设备信息 ---")
            for k, v in sw.info().items():
                print(f"  {k}: {v}")

    except GraSwitchAuthError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        print("提示：设置 MIIO_IP / MIIO_TOKEN / MIIO_DID，或填写 .miio-device-token", file=sys.stderr)
        return 2
    except GraSwitchTimeoutError as exc:
        # 写操作 / 回读验证期间的超时：状态未知，不是失败也不是关
        print(f"设备无应答，状态未知：{exc}", file=sys.stderr)
        return 4
    except GraSwitchOfflineError as exc:
        print(f"设备不可达：{exc}", file=sys.stderr)
        return 5
    except GraSwitchVerifyError as exc:
        print(f"验证失败：{exc}", file=sys.stderr)
        return 3
    except GraSwitchError as exc:
        print(f"设备通信失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())