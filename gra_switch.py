#!/root/test/.venv-miio/bin/python
"""Gra智能控制器V7（giot.plug.v7icw）开关控制库。

⚠️ 本型号的坑（务必先读）
==========================

1. ``siid=2, piid=1`` —— **写通道，只写不读**。
   ``set_property_by(2, 1, True/False)`` 真实有效，能开关继电器；
   但用 ``get_property_by(2, 1)`` 去读，**永远返回 True**，与实际状态无关。
   绝对不能用它判断开关状态。

2. ``siid=7, piid=1`` —— **读通道，唯一真状态位**。
   ``get_property_by(7, 1)`` 返回 True=开 / False=关，是真实状态。

3. 写操作之后要等设备状态同步，**用 7-1 轮询验证**，不要信 2-1 的回读。
   同步有延迟（通常 1~3 秒），所以 ``on()`` / ``off()`` / ``toggle()``
   内部都会调用 :meth:`GraSwitch.verify_write` 轮询确认真实状态。

4. ``did`` 必须传**字符串**（miIO 协议要求），本库已统一处理。

5. 本模块所有写操作都会回读验证，返回的是**真实结果**而不是「指令已下发」。

用法
----
.. code-block:: python

    from gra_switch import GraSwitch

    with GraSwitch() as sw:
        print(sw.status())   # True / False，读的是 7-1
        sw.off()             # 写 2-1=False，再用 7-1 验证
        sw.toggle()          # 先 status() 再取反

命令行
------
.. code-block:: shell

    python gra_switch.py status
    python gra_switch.py on
    python gra_switch.py off
    python gra_switch.py toggle
    python gra_switch.py info
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    from miio import MiotDevice  # type: ignore
except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出明确提示
    raise SystemExit(
        "缺少依赖 python-miio，请用 /root/test/.venv-miio/bin/python 运行，"
        "或执行：pip install python-miio"
    ) from exc


LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
#: 旧的硬编码路径，仅作向后兼容的查找候选（不推荐使用）
LEGACY_TOKEN_FILE = Path("/root/test/.miio-device-token")

#: 项目目录下的默认配置文件名
PROJECT_CONFIG_NAME = ".miio-device-token"

#: 用户级默认配置路径
USER_CONFIG_FILE = Path.home() / ".config" / "gra-switch" / "config"

#: 兼容旧代码的别名
DEFAULT_TOKEN_FILE = LEGACY_TOKEN_FILE

#: 写通道：set_property_by(2, 1, bool)。**只写不读**，读永远返回 True。
SWITCH_SIID = 2
SWITCH_PIID = 1

#: 读通道：get_property_by(7, 1)。**唯一真状态位**，True=开 False=关。
STATE_SIID = 7
STATE_PIID = 1

#: 写后验证的轮询间隔（秒）
VERIFY_INTERVAL = 0.5
#: 写后验证的总超时（秒）
VERIFY_TIMEOUT = 10.0

#: 设备型号
MODEL = "giot.plug.v7icw"


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------
class GraSwitchError(Exception):
    """本库所有异常的基类。"""


class GraSwitchAuthError(GraSwitchError):
    """配置/token 相关错误。"""


class GraSwitchTimeoutError(GraSwitchError):
    """通信或写后验证超时。"""


class GraSwitchOfflineError(GraSwitchError):
    """设备不可达。"""


class GraSwitchResponseError(GraSwitchError):
    """设备返回了非预期响应。"""


class GraSwitchVerifyError(GraSwitchError):
    """写操作已下发，但回读验证未达到期望状态。"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def _discover_config_file() -> Optional[Path]:
    """按优先级查找配置文件，返回第一个存在的路径；都不存在返回 None。

    查找链：
        1. 项目目录（本文件所在目录）下的 ``.miio-device-token``
        2. 旧路径 ``/root/test/.miio-device-token``
        3. ``~/.config/gra-switch/config``
    """
    candidates = [
        Path(__file__).resolve().parent / PROJECT_CONFIG_NAME,
        LEGACY_TOKEN_FILE,
        USER_CONFIG_FILE,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def load_device_config(path: Optional[str | Path] = None) -> dict[str, str]:
    """读取设备配置。

    优先级：环境变量 ``MIIO_IP`` / ``MIIO_TOKEN`` / ``MIIO_DID`` 高于文件。

    文件格式（每行一个，``KEY=VALUE``）::

        IP=192.168.31.194
        TOKEN=<32位hex>
        DID=2031986608
        MODEL=giot.plug.v7icw

    查找顺序（前面的优先，找到第一个存在的文件即用）：
        1. 显式传入的 ``path`` / ``--config`` 参数（指定了就不再找别的）
        2. 项目目录（本文件所在目录）下的 ``.miio-device-token``
        3. 旧路径 ``/root/test/.miio-device-token``（向后兼容）
        4. ``~/.config/gra-switch/config``
    环境变量 ``MIIO_IP`` / ``MIIO_TOKEN`` / ``MIIO_DID`` 始终覆盖文件中的同名字段。

    :param path: 显式指定的配置文件路径；为 None 时走上面的自动查找链
    :return: 含 IP / TOKEN / DID / MODEL 的字典
    :raises GraSwitchAuthError: 所有候选都不存在，或字段非法
    """
    token_file = Path(path).expanduser() if path else _discover_config_file()
    cfg: dict[str, str] = {}

    if token_file is not None and token_file.exists():
        try:
            for line in token_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                cfg[key.strip().upper()] = value.strip()
        except OSError as exc:
            raise GraSwitchAuthError(f"读取配置文件失败 {token_file}: {exc}") from exc

    # 环境变量覆盖
    for key, env_name in (("IP", "MIIO_IP"), ("TOKEN", "MIIO_TOKEN"), ("DID", "MIIO_DID")):
        env_val = os.environ.get(env_name)
        if env_val:
            cfg[key] = env_val.strip()

    missing = [k for k in ("IP", "TOKEN") if not cfg.get(k)]
    if missing:
        where = str(token_file) if token_file else "（未找到任何配置文件）"
        raise GraSwitchAuthError(
            f"缺少设备配置 {missing}；已查找：{where}；"
            "或设置环境变量 MIIO_IP / MIIO_TOKEN / MIIO_DID"
        )

    token = cfg["TOKEN"]
    if len(token) != 32 or any(c not in "0123456789abcdefABCDEF" for c in token):
        raise GraSwitchAuthError(
            f"TOKEN 格式非法：期望 32 位十六进制，实际长度 {len(token)}"
        )

    cfg["TOKEN"] = token.lower()
    return cfg


def mask_token(token: str | None) -> str:
    """对 token 打码，只保留首尾各 4 位。"""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


# --------------------------------------------------------------------------
# 主类
# --------------------------------------------------------------------------
class GraSwitch:
    """Gra智能控制器V7 开关控制。

    支持 context manager，退出时自动 ``close()``。

    本类的核心约定：

    * 读状态一律走 ``7-1``（真状态位）
    * 写状态一律走 ``2-1``（只写通道）
    * 写完必须用 ``7-1`` 回读验证
    """

    def __init__(
        self,
        ip: Optional[str] = None,
        token: Optional[str] = None,
        *,
        did: Optional[str] = None,
        timeout: int = 5,
        config_file: Optional[str | Path] = None,
        lazy_discover: bool = False,
    ) -> None:
        """初始化。

        :param ip: 设备 IP，None 时从配置文件/环境变量读取
        :param token: 设备 token（32 位 hex），None 时同上
        :param did: 设备 did，raw_command 需要；None 时同上
        :param timeout: 单次请求超时秒数，默认 5
        :param config_file: 自定义 token 文件路径
        :param lazy_discover: 是否延迟握手，默认 False（首次通信即握手）
        """
        self._cfg = load_device_config(config_file)
        self.ip: str = ip or self._cfg["IP"]
        self.token: str = token or self._cfg["TOKEN"]
        # did 必须是字符串，miIO 协议不接受 int
        self.did: str = did or self._cfg.get("DID", "")
        self.timeout = timeout

        self._device = MiotDevice(
            ip=self.ip,
            token=self.token,
            timeout=timeout,
            lazy_discover=lazy_discover,
            model=MODEL,
        )
        LOGGER.debug("GraSwitch 初始化 ip=%s token=%s", self.ip, mask_token(self.token))

    # ---------------- context manager ----------------
    def __enter__(self) -> "GraSwitch":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """释放底层连接（发送 bye 命令，忽略失败）。"""
        try:
            self._send("miIO.bye")
        except Exception:  # noqa: BLE001 - 释放阶段不抛
            LOGGER.debug("close() 发送 bye 失败，已忽略", exc_info=True)

    # ---------------- 底层通信 ----------------
    def _send(self, method: str, params: Any = None) -> Any:
        """发送一条 miIO 命令，统一做异常翻译。

        :param method: miIO 方法名
        :param params: 参数
        :return: 设备原始响应
        """
        try:
            return self._device.send(method, params)
        except TimeoutError as exc:
            raise GraSwitchTimeoutError(f"{method} 超时（{self.timeout}s）：{exc}") from exc
        except OSError as exc:
            raise GraSwitchOfflineError(f"{method} 网络不可达：{exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 兜底翻译
            raise GraSwitchResponseError(f"{method} 失败：{exc}") from exc

    @staticmethod
    def _check_result(result: Any, what: str) -> bool:
        """校验 get_properties/set_properties 返回的 code 字段。"""
        if not isinstance(result, list) or not result:
            raise GraSwitchResponseError(f"{what} 响应为空或格式异常：{result!r}")
        first = result[0]
        if not isinstance(first, dict):
            raise GraSwitchResponseError(f"{what} 响应格式异常：{first!r}")
        code = first.get("code")
        if code != 0:
            raise GraSwitchResponseError(f"{what} 失败，code={code}，详情={first!r}")
        return True

    # ---------------- 业务 API ----------------
    def status(self) -> bool:
        """读取当前开关状态。

        读的是 ``siid=7, piid=1`` —— 唯一真状态位。
        **不要**用 ``siid=2, piid=1`` 读，那个字段永远返回 True。

        :return: True=开，False=关
        """
        return bool(self.raw_get(STATE_SIID, STATE_PIID))

    def verify_write(self, expected: bool, timeout: float = VERIFY_TIMEOUT) -> bool:
        """写后回读验证：轮询 ``7-1`` 直到等于 expected 或超时。

        :param expected: 期望的真实状态
        :param timeout: 总超时秒数，默认 10
        :return: True=已同步到期望状态；False=超时仍不一致
        """
        deadline = time.monotonic() + timeout
        expected = bool(expected)
        last: Any = None
        last_err: Optional[Exception] = None

        while True:
            try:
                last = self.status()
                last_err = None
                if bool(last) == expected:
                    return True
            except GraSwitchError as exc:
                # 设备刚写完时偶发读失败，继续重试直到超时
                last_err = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(VERIFY_INTERVAL)

        LOGGER.warning(
            "verify_write 超时：期望 %s，最后读到 %r（error=%r）", expected, last, last_err
        )
        return False

    def on(self) -> bool:
        """打开开关。

        流程：写 ``2-1=True`` → 用 ``7-1`` 轮询验证。

        :return: True=确认已开；False=指令下发但回读未确认
        """
        self.raw_set(SWITCH_SIID, SWITCH_PIID, True)
        return self.verify_write(True)

    def off(self) -> bool:
        """关闭开关。

        流程：写 ``2-1=False`` → 用 ``7-1`` 轮询验证。

        :return: True=确认已关；False=指令下发但回读未确认
        """
        self.raw_set(SWITCH_SIID, SWITCH_PIID, False)
        return self.verify_write(False)

    def toggle(self) -> bool:
        """翻转开关。

        先读真实状态，再调用相反的 ``on()`` / ``off()``。

        :return: True=确认翻转成功；False=回读未确认
        """
        current = self.status()
        LOGGER.debug("toggle: 当前状态 %s，将切换到 %s", current, not current)
        return self.off() if current else self.on()

    def info(self) -> dict[str, Any]:
        """设备信息：model / fw / hw / mac / ip。"""
        dev_info = self._device.info()
        raw = dev_info.raw if hasattr(dev_info, "raw") else {}
        return {
            "model": dev_info.model,
            "firmware_version": dev_info.firmware_version,
            "hardware_version": dev_info.hardware_version,
            "mac": dev_info.mac_address,
            "ip": dev_info.ip_address or self.ip,
            "did": self.did,
            "raw_keys": sorted(raw.keys()) if isinstance(raw, dict) else [],
        }

    def raw_get(self, siid: int, piid: int) -> Any:
        """读取任意属性。

        :param siid: 服务 ID
        :param piid: 属性 ID
        :return: 属性值（bool/int/str）
        """
        payload = [{"did": f"get-{siid}-{piid}", "siid": siid, "piid": piid}]
        result = self._send("get_properties", payload)
        self._check_result(result, f"get_properties({siid},{piid})")
        if "value" not in result[0]:
            raise GraSwitchResponseError(f"响应缺少 value 字段：{result[0]!r}")
        return result[0]["value"]

    def raw_set(self, siid: int, piid: int, value: Any) -> bool:
        """写入任意属性。

        :param siid: 服务 ID
        :param piid: 属性 ID
        :param value: 目标值
        :return: True 表示设备确认收到指令（不代表状态已生效，需自行回读验证）
        """
        payload = [{"did": f"set-{siid}-{piid}", "siid": siid, "piid": piid, "value": value}]
        result = self._send("set_properties", payload)
        return self._check_result(result, f"set_properties({siid},{piid})={value!r}")

    def action(self, siid: int, aiid: int, params: Optional[list] = None) -> bool:
        """触发 action。

        :param siid: 服务 ID
        :param aiid: action ID
        :param params: 入参列表，默认空
        :return: True 表示设备确认成功
        """
        # 注意：miio 的 action 的 did 用 call-<siid>-<aiid>，且必须为字符串
        payload = {
            "did": f"call-{siid}-{aiid}",
            "siid": siid,
            "aiid": aiid,
            "in": params or [],
        }
        result = self._send("action", payload)
        return self._check_result(result, f"action({siid},{aiid})")

    def raw_command(self, method: str, params: Any = None) -> Any:
        """底层透传，方便以后扩展。

        :param method: miIO 方法名，如 ``get_properties``
        :param params: 参数
        """
        return self._send(method, params)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gra智能控制器V7 控制工具")
    p.add_argument("-t", "--timeout", type=int, default=5, help="请求超时秒数，默认 5")
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    p.add_argument("-c", "--config", metavar="PATH", default=None,
                   help="指定配置文件路径（默认按查找链自动发现）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="读取开关状态（读 7-1 真状态位）")
    sub.add_parser("on", help="打开开关（写 2-1，回读 7-1 验证）")
    sub.add_parser("off", help="关闭开关（写 2-1，回读 7-1 验证）")
    sub.add_parser("toggle", help="翻转开关（先读 7-1 再取反）")
    sub.add_parser("info", help="读取设备信息")

    rg = sub.add_parser("raw-get", help="读取任意属性")
    rg.add_argument("siid", type=int)
    rg.add_argument("piid", type=int)

    rs = sub.add_parser("raw-set", help="写入任意属性（危险，需 --yes）")
    rs.add_argument("siid", type=int)
    rs.add_argument("piid", type=int)
    rs.add_argument("value")
    rs.add_argument("--yes", action="store_true", help="确认执行写操作")

    ac = sub.add_parser("action", help="触发 action")
    ac.add_argument("siid", type=int)
    ac.add_argument("aiid", type=int)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口，返回进程退出码。"""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.cmd == "raw-set" and not args.yes:
        print("拒绝执行：raw-set 会改变设备状态，请加 --yes 确认", file=sys.stderr)
        return 2

    try:
        with GraSwitch(timeout=args.timeout, config_file=args.config) as sw:
            if args.cmd == "status":
                print("on" if sw.status() else "off")
            elif args.cmd == "on":
                print("ok" if sw.on() else "failed(未验证通过)")
            elif args.cmd == "off":
                print("ok" if sw.off() else "failed(未验证通过)")
            elif args.cmd == "toggle":
                print("ok" if sw.toggle() else "failed(未验证通过)")
            elif args.cmd == "info":
                for k, v in sw.info().items():
                    print(f"{k}: {v}")
            elif args.cmd == "raw-get":
                print(sw.raw_get(args.siid, args.piid))
            elif args.cmd == "raw-set":
                print("ok" if sw.raw_set(args.siid, args.piid, args.value) else "failed")
            elif args.cmd == "action":
                print("ok" if sw.action(args.siid, args.aiid) else "failed")
    except GraSwitchError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())