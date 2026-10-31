#!/bin/sh
# ============================================================================
# Gra 智能控制器 —— crontab 定时开关示例
#
# 安装方法：
#   1. 把本脚本复制到固定位置（比如 /root/gra-cron.sh）
#   2. chmod +x /root/gra-cron.sh
#   3. crontab -e 加入下面几行（按注释说明改时间）
#   4. 用 `crontab -l` 确认，用 `tail -f /var/log/gra.log` 观察
#
# 注意：cron 环境里没有 PATH 和 venv 激活，所以下面全用绝对路径。
# ============================================================================

# ---- 按你的实际路径改这两行 ----------------------------------------------
VENV_PYTHON="$HOME/.venv-miio/bin/python"   # venv 里的 python
GRA_SCRIPT="$HOME/gra-switch/gra_switch.py"             # 本仓库的脚本

# ---- 可选：指定配置文件（不设则走默认查找链）-----------------------------
# CONFIG_FILE="$HOME/.config/gra-switch/.miio-device-token"
CONFIG_FILE=""

# ---- 参数处理：cron 里传 on / off / status 即可 --------------------------
ACTION="${1:-status}"

if [ -n "$CONFIG_FILE" ]; then
    CONFIG_ARG="--config $CONFIG_FILE"
else
    CONFIG_ARG=""
fi

# 执行并记录时间戳
echo "[$(date '+%Y-%m-%d %H:%M:%S')] gra $ACTION"
# shellcheck disable=SC2086
"$VENV_PYTHON" "$GRA_SCRIPT" $CONFIG_ARG "$ACTION"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] exit=$?"

# ============================================================================
# crontab 示例（crontab -e 里加）：
#
# # 每天 07:00 自动打开
# 0 7 * * *   /root/gra-cron.sh on   >> /var/log/gra.log 2>&1
#
# # 每天 23:00 自动关闭
# 0 23 * * *  /root/gra-cron.sh off  >> /var/log/gra.log 2>&1
#
# # 工作日（周一至周五）08:30 开
# 30 8 * * 1-5 /root/gra-cron.sh on  >> /var/log/gra.log 2>&1
#
# # 每 10 分钟记录一次状态，便于排查是否被手动改动
# */10 * * * * /root/gra-cron.sh status >> /var/log/gra.log 2>&1
#
# # 开机后 1 分钟确保关闭（@reboot 用法）
# @reboot sleep 60 && /root/gra-cron.sh off >> /var/log/gra.log 2>&1
#
# 说明：
#   - 日志重定向 >> /var/log/gra.log 2>&1 把 stdout 和 stderr 都写进日志
#   - 如果 /var/log 写不进去，改用 /tmp/gra.log 或用户家目录
#   - 脚本内部已带时间戳输出，cron 自带邮件通知时也能看清
# ============================================================================