# Gra 智能控制器 V7 局域网控制模块

一句话：**在局域网内用 miIO 协议直连控制 Gra 智能控制器 V7 的开关**，不经过米家云。

---

## 1. 设备型号与协议说明

| 项 | 值 |
| --- | --- |
| 设备名 | Gra智能控制器V7 |
| 型号 model | `giot.plug.v7icw` |
| 厂商 | GranchipIoT（格兰芯）—— 米家生态**第三方白牌**，不是小米自研设备 |
| 芯片 | BL602 |
| 固件 | `1.0.3_0004` |
| 协议 | miIO over UDP，端口 `54321` |
| 参考 IP | `192.0.2.10`（示例，实际以你自己的为准） |
| 参考 did | `2031986608`（示例） |

**为什么不是小米设备却走 miIO？**
GranchipIoT 虽是白牌，但接入了米家生态，固件里实现了米家标准 miIO 协议栈，所以能直接用 `python-miio` 通过 UDP 54321 通信。设备在米家中注册，token 也由米家下发，因此 **token 必须从米家侧获取**。

---

## 2. 安装

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 里锁定了 `python-miio==0.5.12`——其他版本未经测试，属性表行为可能不同。

---

## 3. 配置 token（三种方式）

### 方式 A：环境变量（推荐用于临时/CI）

```bash
export MIIO_IP=192.0.2.10
export MIIO_TOKEN=<32位十六进制>
export MIIO_DID=2031986608
```

### 方式 B：配置文件

复制模板并填入真实值：

```bash
cp .env.example .miio-device-token
chmod 600 .miio-device-token
$EDITOR .miio-device-token
```

格式为每行 `KEY=VALUE`：

```
IP=192.0.2.10
TOKEN=<32位十六进制>
DID=2031986608
MODEL=giot.plug.v7icw
```

也可用 `--config /path/to/file` 显式指定任意路径。

### 方式 C：默认查找链

不指定任何东西时，按以下顺序找第一个存在的文件：

1. 项目目录下的 `.miio-device-token`
2. `~/.config/gra-switch/.miio-device-token`（历史路径，向后兼容）
3. `~/.config/gra-switch/config`

环境变量始终**覆盖**文件中的同名字段。

### token 从哪来

- **米家 App 备份**：安卓备份出 `.bak` 加密包，用 `miio-extract-tokens` 类工具解出
- **Home Assistant**：`/config/.storage/xiaomi_miot/…` 或 `core.config_entries` 里存有 token
- **已有的 miIO 工具**：如果设备曾在某个 HA/其他集成里配过，token 已存在其存储中

token 是 32 位十六进制字符串，**等于设备控制权**，拿到就能开关。

---

## 4. 快速开始

### CLI

```bash
# 读状态（读 7-1 真状态位）
python gra_switch.py status

# 开 / 关（写 2-1，自动回读 7-1 验证）
python gra_switch.py on
python gra_switch.py off

# 翻转
python gra_switch.py toggle

# 设备信息
python gra_switch.py info

# 指定配置文件
python gra_switch.py --config /etc/gra/config status
```

### Python API

```python
from gra_switch import GraSwitch

with GraSwitch() as sw:
    print("当前状态:", "开" if sw.status() else "关")
    sw.on()
    print("写入后:", "开" if sw.status() else "关")
```

---

## 5. ⚠️ 本型号的坑（重点）

这是本仓库最有价值的部分，别的 miIO 插件文档里没有。

### 5.1 `siid=2, piid=1` 只写不读

`2-1` 是**写通道**：`set_property_by(2, 1, True/False)` 能改状态。
但**读 `2-1` 永远返回 `True`**，无论设备实际是开还是关。
如果你按常规做法「写 2-1，再读 2-1 确认」，会得到一个永远成功的假象。

### 5.2 `siid=7, piid=1` 才是真状态位

`7-1` 是**只读的真状态位**：`True` = 开，`False` = 关。
读状态、验证写入，**一律走 `7-1`**。

### 5.3 正确姿势

```python
# 读：走 7-1
state = sw.raw_get(7, 1)

# 写：走 2-1
sw.raw_set(2, 1, True)

# 验证：回读 7-1，轮询直到一致或超时
```

本库的 `on()` / `off()` / `toggle()` **已经内置了写后回读验证**（轮询 `7-1`，默认 0.5s 间隔、10s 超时），返回的是**真实验证结果**，不是空的 ack。写入未生效时返回 `False` 或抛 `GraSwitchVerifyError`，不会骗你说成功。

### 5.4 did 必须是字符串

miIO 协议的 `raw_command` 里 did 若传 int 会出错，本库统一转成字符串。

---

## 6. 系统命令安装

`scripts/gra` 是系统级包装器，把 `gra` 命令接到本仓库：

```bash
# 按你的实际路径改一下，然后
sudo cp scripts/gra /usr/local/bin/gra
sudo chmod +x /usr/local/bin/gra

# 之后任意目录可用
gra status
gra on
```

卸载：`sudo rm /usr/local/bin/gra`。

---

## 7. 定时任务示例

见 `examples/cron_example.sh`，核心是：

```cron
# 每天 07:00 开，23:00 关
0 7  * * *  /path/to/gra on  >> /var/log/gra.log 2>&1
0 23 * * *  /path/to/gra off >> /var/log/gra.log 2>&1
```

---

## 8. 故障排查

| 现象 | 原因 | 解法 |
| --- | --- | --- |
| `缺少设备配置 ['IP', 'TOKEN']` | 找不到配置文件且没设环境变量 | 按第 3 节配 token，或用 `--config` 指定 |
| `TOKEN 格式非法：期望 32 位十六进制` | token 位数不对/含非法字符 | 重新从米家备份提取 |
| `error: 设备无应答，无法确定开关状态`（退出码 3） | 设备没应答，**状态未知**，不是 off | 检查 IP / 供电 / 是否同网段；`ping <ip>`；稍后重试。脚本里必须把 3 当未知处理 |
| `error: 设备不可达`（退出码 4） | 网络层不通 | `ping <ip>`；路由器里给设备绑静态 IP |
| 日志有 `user ack timeout` 但输出仍是 `on`/`off`（退出码 0） | 底层重试后成功，状态可信 | 无需处理 |
| 读状态永远是 `on` | 读了 `2-1`（只写通道） | 改用 `7-1`，本库已默认如此 |
| 写操作返回 `failed(未验证通过)` | 指令下发但设备没实际动作 | 确认设备通电、固件正常；查 `7-1` 实际值 |
| `ModuleNotFoundError: miio` | 用了系统 python 而非 venv | 用 `.venv/bin/python` 或激活 venv |

---

## 9. 安全提醒

- **token 等于设备控制权**，泄漏 = 别人能远程开关你的电器。
- **不要把 `.miio-device-token` 提交进 git**——本仓库的 `.gitignore` 已覆盖，但新建仓库时自己再确认一次。
- **不要把 token 贴进聊天、issue、截图**。真要贴，先打码（本库 `mask_token()` 可参考）。
- 配置文件权限建议 `chmod 600`。

---

## License

MIT © 红尘笑