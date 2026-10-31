# Gra智能控制器V7（giot.plug.v7icw）使用手册

> 模块路径：`/root/test/gra_switch.py`
> venv：`/root/test/.venv-miio`（python-miio 0.5.12）
> token 文件：`/root/test/.miio-device-token`（600 权限，4 行 `KEY=VALUE`）

---

## 0. 依赖

venv 里已有 python-miio 0.5.12，无需再装：

```bash
/root/test/.venv-miio/bin/python -c "import miio; print(miio.__version__)"
# 0.5.12
```

如果要重新建环境：

```bash
python3 -m venv /root/test/.venv-miio
/root/test/.venv-miio/bin/pip install "python-miio==0.5.12"
```

---

## 1. 三个最常见用法

### 1.1 读状态

命令行：

```bash
cd /root/test && /root/test/.venv-miio/bin/python gra_switch.py status
# on
```

代码里：

```python
from gra_switch import GraSwitch

with GraSwitch() as sw:
    print(sw.status())          # True / False
    print(sw.raw_get(2, 1))     # 直接读 siid=2, piid=1
```

### 1.2 定时开关（crontab，不引额外依赖）

每天早上 7 点开、晚上 11 点关：

```cron
# crontab -e
0 7  * * * cd /root/test && /root/test/.venv-miio/bin/python gra_switch.py on  >> /var/log/gra_switch.log 2>&1
0 23 * * * cd /root/test && /root/test/.venv-miio/bin/python gra_switch.py off >> /var/log/gra_switch.log 2>&1
```

安装方式：

```bash
( crontab -l 2>/dev/null; \
  echo '0 7  * * * cd /root/test && /root/test/.venv-miio/bin/python gra_switch.py on  >> /var/log/gra_switch.log 2>&1'; \
  echo '0 23 * * * cd /root/test && /root/test/.venv-miio/bin/python gra_switch.py off >> /var/log/gra_switch.log 2>&1' \
) | crontab -
crontab -l   # 核对
```

> **重要**：cron 环境下不会加载你的 shell 配置，务必用 venv 里的**绝对路径** python，并且 `cd` 到模块目录。

### 1.3 接入自己的项目

把 `gra_switch.py` 放到项目里（或 `sys.path.append` 指向 `/root/test`），然后：

```python
import logging
from gra_switch import GraSwitch, GraSwitchError

logging.basicConfig(level=logging.WARNING)

def ensure_on() -> bool:
    """确保开关是开的，返回是否本次真的执行了操作。"""
    try:
        with GraSwitch(timeout=5) as sw:
            if sw.status():
                return False
            sw.on()
            return True
    except GraSwitchError as exc:
        logging.error("控制失败：%s", exc)
        return False
```

**要点**：用 `with` 语句，退出时自动 `close()`，不会残留 UDP 连接。

---

## 2. 「状态变化时做点什么」

### 2.1 结论先说：**没有 push，用轮询**

python-miio 0.5.12 确实带了 `miio.push_server.PushServer`，但它是**异步**（asyncio）实现，而且需要**预先知道事件参数**：

```python
from miio.push_server import PushServer, EventInfo
# EventInfo(action="alarm_triggering", extra="[1,19,1,111,[0,1],2,0]", trigger_token=device.token)
```

那串 `extra` 是从米家 App 抓包得来的，**不同型号、不同事件都不一样**，而且需要设备支持往推送服务器回连。`giot.plug.v7icw` 属于米家第三方设备，这套私有推送协议的兼容性没有保证，**实测成本高、稳定性未知**。

**建议**：用轮询，简单可靠。

### 2.2 轮询示例

```python
#!/usr/bin/env python3
"""状态变化回调示例：开关状态一变就打印（可替换成你的业务动作）。"""
import logging
import time

from gra_switch import GraSwitch, GraSwitchError

POLL_INTERVAL = 3  # 秒


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    last = None

    while True:
        try:
            with GraSwitch(timeout=5) as sw:
                current = sw.status()
            if current != last:
                print(f"[{time.strftime('%F %T')}] 状态变化 -> {'on' if current else 'off'}")
                # TODO: 在这里放你的业务动作
                last = current
        except GraSwitchError as exc:
            # 设备离线/超时：记录但不退出，下次重试
            print(f"[{time.strftime('%F %T')}] 轮询失败：{exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
```

**注意**：

- 每次循环**新建一个 GraSwitch**，用完 `close()`。长连接反而容易在设备空闲时被踢。
- 轮询间隔别小于 2 秒，米家设备对高频请求不友好（会回 `user ack timeout`）。

---

## 3. CLI 完整用法

```bash
P=/root/test/.venv-miio/bin/python
M=/root/test/gra_switch.py

$P $M status            # 读状态：on / off
$P $M info              # 设备信息：model / fw / hw / mac / ip
$P $M on                # 打开
$P $M off               # 关闭
$P $M toggle            # 翻转
$P $M raw-get 2 1       # 读任意属性
$P $M raw-set 2 1 false --yes   # 写任意属性（必须 --yes）
$P $M action 2 1        # 触发 siid=2 aiid=1 的 action
$P -v $M status         # 打开调试日志
$P $M -t 10 status      # 超时改 10 秒（注意 -t 要放在子命令前）
```

---

## 4. 关键坑位（血泪记录）

### 4.1 `did` 必须是字符串

调 `raw_command` 时，`did` 传 int 会返回**空 did**。模块里已经全部用字符串。

### 4.2 写操作返回 `code: 0` 但状态没变

**这是本设备的已知现象**。实测：

```
set_properties [{'did':'set-2-1','siid':2,'piid':1,'value':False}]
-> [{'did': 'set-2-1', 'siid': 2, 'piid': 1, 'code': 0}]
状态：仍然 True（等 6 秒也不变）

action call-2-1 (toggle)
-> {'code': 0, 'out': []}
状态：仍然 True
```

> ⚠️ **本节旧结论已被实测推翻（2026-11-01）**

**当时的原因推测**：以为固件只做 ack、真正执行走私有通道，python-miio「能读不能写」。

**真实原因**：写其实**是通的**。上面「状态仍然 True」是因为**读错了字段**——当时用 `get_property_by(2, 1)` 回读，而该字段**永远返回 True**，与真实状态无关。也就是说：继电器其实已经动作了，只是回读的字段是坏的，看起来像「没生效」。

**更正后的结论**：
- 写：`set_property_by(2, 1, True/False)` **真实有效**，继电器会动
- 读：必须用 `get_property_by(7, 1)`，这才是真状态位
- 写后验证：轮询 `7-1` 直到等于期望值（有 1~3 秒延迟）

详细说明见文末「⚠️ 本型号的坑（giot.plug.v7icw）」一节。当前代码的 `on()/off()/toggle()` 已经按此修正，返回真实结果。

### 4.3 `user ack timeout` 噪音

退出时发 `miIO.bye`，设备经常不回，日志里会出现：

```
ERROR miio.miioprotocol: Got error when receiving: {'code': -9999, 'message': 'user ack timeout'}
```

**不影响结果**，只是噪音。`close()` 里已经 try/except 吞掉了。

### 4.4 `mapping` 警告

```
WARNING miio.miot_device: Neither the class nor the parameter defines the mapping
```

因为没给 `MiotDevice` 传 mapping。**无害**，本模块用的是 `raw_get` / `raw_set`，不走 mapping 路径。

---

## 5. 安全提醒

- token 存在 `/root/test/.miio-device-token`，权限 600，**不要贴到聊天、日志、git**。
- 模块里 `mask_token()` 只显示首尾 4 位，打印时用这个。
- `raw-set` 子命令强制 `--yes` 才执行，防止手滑。

---

## ⚠️ 本型号的坑（giot.plug.v7icw）

这一节是**实测结论**，不是推测。改代码前必读。

### 1. `siid=2, piid=1` 只写不读，读永远返回 True

`set_property_by(2, 1, True/False)` 真实有效，能开关继电器。
但用 `get_property_by(2, 1)` 去读，**无论设备实际是开还是关，永远返回 True**。

这不是延迟问题，是固件把该字段的读路径做成了常量。**绝对不能用来判断状态。**

### 2. `siid=7, piid=1` 才是真状态位

`get_property_by(7, 1)` 返回 True=开 / False=关，是唯一可信的状态来源。

实测同一时刻两个字段互相矛盾（设备物理关闭时）：

```
status:     False
raw 7-1:    False    <-- 真实状态
raw 2-1:    True     <-- 坏字段，恒 True
```

### 3. 写操作后用 `7-1` 验证，别信 `2-1` 的回读

`on()` / `off()` / `toggle()` 内部都会写 `2-1` 之后调用 `verify_write()`，
轮询 `7-1` 直到等于期望值或超时（默认 10 秒），**返回的是真实结果**。

```python
with GraSwitch() as sw:
    if sw.off():                 # 写 2-1=False，再用 7-1 轮询确认
        print("确认已关闭")
    else:
        print("指令已下发，但回读未确认（检查设备/网络）")
```

如果自己写脚本，千万不要这样：

```python
sw.raw_set(2, 1, True)          # 只代表指令被 ack
print(sw.raw_get(2, 1))         # 永远是 True，毫无意义
```

### 4. `did` 必须传字符串

miIO 协议要求 `did` 为字符串。token 文件里的 `DID=2031986608` 读出来就是 str，
本模块已在 `raw_get` / `raw_set` / `action` 里统一用 f-string 构造，不会出错。
但如果你绕过本模块直接拼 payload，注意别传 int。

### 5. 状态变更到可读之间有延迟，需要轮询

写完 `2-1` 之后，`7-1` 不会立刻变。实测延迟约 1~3 秒，偶发更久。
所以：

- **不要写完后立刻读一次就下结论**
- 用 `verify_write(expected, timeout=10.0)` 轮询，间隔 0.5 秒
- 超时返回 False 表示「指令发了但没确认」，不是「指令失败」

### 6. 常量速查表

| 常量 | 值 | 用途 |
|---|---|---|
| `SWITCH_SIID` / `SWITCH_PIID` | 2 / 1 | **写通道**，`set_property_by` |
| `STATE_SIID` / `STATE_PIID` | 7 / 1 | **读通道**，`get_property_by`，唯一真状态 |
| `VERIFY_INTERVAL` | 0.5 | 写后验证轮询间隔（秒） |
| `VERIFY_TIMEOUT` | 10.0 | 写后验证总超时（秒） |

---

## 做成系统命令（`gra`）

### 1. 已安装

已创建 `/usr/local/bin/gra`：

```sh
#!/bin/sh
exec /root/test/.venv-miio/bin/python /root/test/gra_switch.py "$@"
```

`gra_switch.py` 的 shebang 也已改为 `#!/root/test/.venv-miio/bin/python`（原来是
`#!/usr/bin/env python3`，系统 python3 没有 python-miio，直接执行会 ImportError）。

现在**任何目录、任何 shell** 都能直接用：

```bash
gra status      # 读 7-1 真状态
gra on          # 开（写 2-1，回读 7-1 验证）
gra off         # 关
gra toggle
gra info
gra --help
```

安全用法（先读再写）：

```bash
gra status && gra on     # status 为 off 才 on
```

### 2. 如果 PATH 里没有 /usr/local/bin

极少见，正常 root / 普通用户都带。确认：

```bash
echo "$PATH" | tr ':' '\n' | grep -x /usr/local/bin
```

没有就临时补（当前 shell）：

```bash
export PATH="/usr/local/bin:$PATH"
```

永久补（写 `~/.profile`，不污染 non-interactive shell）：

```bash
grep -qxF 'export PATH="/usr/local/bin:$PATH"' ~/.profile 2>/dev/null \
  || echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.profile
```

### 3. 卸载

```bash
rm /usr/local/bin/gra
```

脚本本体仍在 `/root/test/gra_switch.py`，venv 不受影响。

### 4. 换 venv 或脚本路径时改哪里

只改两处，**不用碰 gra_switch.py 的逻辑**：

| 位置 | 改什么 |
|---|---|
| `/usr/local/bin/gra` 第 3 行 | `exec <新venv>/bin/python <新脚本路径> "$@"` |
| `gra_switch.py` 第 1 行 shebang | `#!/<新venv>/bin/python` |

改完必须验：

```bash
sh -n /usr/local/bin/gra && gra --help >/dev/null && echo OK
```

### 5. 已知提示（非故障）

`gra --help` / `gra` 无参数时会先打出一条 FutureWarning：

```
miio/miot_device.py:23: FutureWarning: functools.partial will be a method descriptor ...
```

来自 python-miio 自身兼容性提示，走 stderr，退出码不受影响（`--help` 仍是 0）。
嫌吵就 `2>/dev/null`，但建议留着，方便发现 miio 后续真出问题时。

`user ack timeout` 是 python-miio 的**重试中间日志**：底层会自动重试 3 次，
只要最终成功，命令行仍会正常输出 `on` / `off`，退出码 0，此时状态是可信的。

**但如果 3 次重试全部失败**，本库会明确报错（见下一节），绝不会把「没应答」
伪装成 `off`。

---

## 6. 错误处理（重要）

### 6.1 核心原则：无应答 ≠ 关

设备没应答时，我们**不知道**它是开还是关。把这种情况当成 `off` 在自动化里是致命的：
定时脚本拿到假 `off`，可能重复开灯、或误判「已经关了」而不去关。

本库的处理：

| 情况 | 行为 | 退出码 |
| --- | --- | --- |
| 真读到 `code:0` + `value:false` | 输出 `off` | 0 |
| 真读到 `code:0` + `value:true` | 输出 `on` | 0 |
| 超时 / 设备无应答 | stderr 输出 `error: 设备无应答，无法确定开关状态` | 3 |
| 设备不可达（网络层） | stderr 输出 `error: 设备不可达` | 4 |
| 设备返回异常响应（非超时） | stderr 输出 `error: ...` | 1 |
| 配置 / token 错误 | stderr 输出 `error: ...` | 1 |

**退出码 3 是「状态未知」的信号**，脚本里必须单独处理，不能和 0 混为一谈。

### 6.2 脚本里怎么判断

```bash
gra status
case $? in
  0) ;;                                   # 正常，stdout 是 on/off
  3) echo '设备无应答，状态未知' >&2; exit 1 ;;   # 关键：不要当作 off
  *) echo '读取失败' >&2; exit 1 ;;
esac
```

Python 里：

```python
from gra_switch import GraSwitch, GraSwitchTimeoutError

with GraSwitch(timeout=5) as sw:
    try:
        state = sw.status()          # True/False，保证是真读到的
    except GraSwitchTimeoutError:
        state = None                 # 未知，和 False 严格区分
        log.warning('设备无应答，状态未知，跳过本次决策')
```

### 6.3 异常层次

```
GraSwitchError                 # 基类
├── GraSwitchAuthError         # 配置 / token 问题
├── GraSwitchTimeoutError      # 超时 / 无应答 —— 状态未知！
├── GraSwitchOfflineError      # 网络不可达
├── GraSwitchResponseError     # 设备返回了非预期响应
└── GraSwitchVerifyError       # 写后回读未达期望
```

`on()` / `off()` / `toggle()` 遇到超时同样抛 `GraSwitchTimeoutError`，不会静默失败。
`verify_write()` 如果在整个轮询窗口内一次都没读到值，也抛 `GraSwitchTimeoutError`；
只有在**读到了真实值、但迟迟不等于期望值**时才返回 `False`。
这两个含义不同，不要混用。
