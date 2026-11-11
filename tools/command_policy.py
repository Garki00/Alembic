# tools/command_policy.py
"""命令策略：在执行前按文本识别高危命令。

这是审批之外的一层独立策略：

- ``hardline``：无论当前是什么权限模式（包括“完全权限”），都直接拒绝，
  不弹审批、也不能用“始终允许”绕过。只覆盖不可恢复的操作，例如格式化
  磁盘、清空系统盘、删除 WSL 发行版、破坏引导记录。
- ``elevated``：命令可以执行，但审批卡片会显示具体风险原因（递归删除、
  强制推送、结束进程、访问凭据文件等），让用户在点“允许”前知道自己在
  同意什么。

策略按命令原文匹配，会做小写化与空白折叠。它不是完整的 shell 解释器，
也无法阻止刻意混淆的命令；它的目标是拦住常见的高破坏性误操作，并给审批
提供可解释的理由，真正的隔离边界仍然应该由操作系统/容器提供。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["none", "elevated", "hardline"]


@dataclass(frozen=True)
class CommandRisk:
    level: RiskLevel
    rule: str = ""
    reason: str = ""

    @property
    def is_hardline(self) -> bool:
        return self.level == "hardline"

    @property
    def is_elevated(self) -> bool:
        return self.level == "elevated"


SAFE = CommandRisk("none")


def _compile(rule: str, pattern: str, reason: str) -> tuple[str, re.Pattern[str], str]:
    return rule, re.compile(pattern, re.IGNORECASE), reason


_HARDLINE_RULES = (
    _compile(
        "format_drive",
        r"\bformat(?:\.com)?\s+[a-z]:",
        "格式化磁盘分区是无法恢复的操作。",
    ),
    _compile(
        "diskpart",
        r"\bdiskpart(?:\.exe)?\b|\bclear-disk\b|\binitialize-disk\b|\bformat-volume\b",
        "磁盘/分区管理命令可能直接破坏磁盘数据。",
    ),
    _compile(
        "delete_root",
        r"\brm\s+-[a-z]*(?:rf|fr)[a-z]*\s+(?:--no-preserve-root\s+)?(?:/|/\*|~|\$home)(?:\s|$)",
        "递归删除根目录或用户主目录。",
    ),
    _compile(
        "delete_drive_root",
        r"\b(?:del|erase)\s+(?:/[a-z]\s+)+[a-z]:[\\/]?(?:\*|\s|$)"
        r"|\b(?:rd|rmdir)\s+(?:/[a-z]\s+)*/?[sq]\s+(?:/[a-z]\s+)*[a-z]:[\\/]?(?:\*|\s|$)"
        r"|\brm\s+-[a-z]*(?:rf|fr)[a-z]*\s+[a-z]:[\\/]?(?:\*|\s|$)",
        "删除整个磁盘分区的根目录。",
    ),
    _compile(
        "remove_item_root",
        r"\bremove-item\b[^\n]*(?:-recurse|-force)[^\n]*\b[a-z]:[\\/](?:\s|\*|$)"
        r"|\bremove-item\b[^\n]*\b[a-z]:[\\/](?:\s|\*|$)[^\n]*(?:-recurse|-force)",
        "递归强制删除磁盘根目录。",
    ),
    _compile(
        "mkfs",
        r"\bmkfs(?:\.[a-z0-9]+)?\b",
        "格式化文件系统会清空目标设备。",
    ),
    _compile(
        "dd_device",
        r"\bdd\b[^\n]*\bof\s*=\s*/dev/(?:sd|hd|nvme|vd|xvd)",
        "直接写入块设备会破坏磁盘数据。",
    ),
    _compile(
        "fork_bomb",
        r":\s*\(\s*\)\s*\{[^\n]*:\s*\|\s*:[^\n]*&[^\n]*\}\s*;\s*:",
        "Shell fork bomb 会耗尽系统进程资源。",
    ),
    _compile(
        "boot_config",
        r"\bbcdedit\b|\bbootrec\b|\bvssadmin\s+delete\s+shadows",
        "修改引导配置或删除卷影副本会导致系统无法恢复。",
    ),
    _compile(
        "registry_hive_delete",
        r"\breg(?:\.exe)?\s+delete\s+(?:hklm|hkcr)\b",
        "删除注册表根键会破坏系统配置。",
    ),
    _compile(
        "power_control",
        r"(?:^|[;&|]\s*|\b(?:sudo|call|start)\s+)(?:shutdown(?:\.exe)?|restart-computer|stop-computer)\b",
        "关机或重启会中断当前所有工作。",
    ),
    _compile(
        "account_tamper",
        r"\bnet\s+user\s+\S+\s+/delete\b|\bnet\s+localgroup\s+administrators\s+\S+\s+/add\b",
        "删除账户或把用户加入管理员组会改变系统权限。",
    ),
    _compile(
        "wipe_free_space",
        r"\bcipher(?:\.exe)?\s+/w\b",
        "擦除磁盘空闲空间是不可恢复的操作。",
    ),
    _compile(
        "unregister_wsl",
        r"\bwsl(?:\.exe)?\s+(?:-{1,2}unregister)\b",
        "注销 WSL 发行版会删除该发行版内的全部数据。",
    ),
    _compile(
        "take_over_root",
        r"\btakeown\s+/f\s+[a-z]:[\\/]?\s*(?:/r)?\s*$|\bicacls\s+[a-z]:[\\/]?\s+/grant\b",
        "接管磁盘根目录的所有权或权限会导致系统失去保护。",
    ),
    _compile(
        "encoded_payload",
        r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*\s-e(?:nc|ncodedcommand)?\s+[a-z0-9+/=]{40,}",
        "Base64 编码的命令无法审查真实内容，按恶意载荷处理。",
    ),
)

_ELEVATED_RULES = (
    _compile(
        "recursive_delete",
        r"\brm\s+-[a-z]*r[a-z]*\b|\bremove-item\b[^\n]*-recurse\b"
        r"|\b(?:rd|rmdir)\s+(?:/[a-z]\s+)*/?s\b|\bdel\s+(?:/[a-z]\s+)*/?[fsq]\b",
        "递归或强制删除会一次移除大量文件，请确认目标范围。",
    ),
    _compile(
        "git_rewrite",
        r"\bgit\s+push\b[^\n]*\s(?:-f|--force(?:-with-lease)?)\b"
        r"|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f",
        "该 Git 命令会重写历史或丢弃未提交改动。",
    ),
    _compile(
        "permission_change",
        r"\bchmod\s+(?:-r\s+)?[0-7]*7[0-7]{2}\b|\bicacls\b[^\n]*\/grant\b"
        r"|\bchown\s+-r\s+root\b|\bset-acl\b",
        "修改文件权限可能让其他账户获得访问权。",
    ),
    _compile(
        "process_kill",
        r"\btaskkill(?:\.exe)?\b|\bstop-process\b|\bpkill\b|\bkillall\b"
        r"|\bkill\s+-9\b",
        "结束进程可能导致未保存的数据丢失。",
    ),
    _compile(
        "remote_execute",
        r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z|k)?sh\b"
        r"|\b(?:irm|iwr|invoke-webrequest|invoke-restmethod)\b[^\n|]*\|\s*(?:iex|invoke-expression)\b"
        r"|\b(?:iex|invoke-expression)\b",
        "从网络下载并直接执行代码，或动态执行字符串，内容不可审查。",
    ),
    _compile(
        "persistence",
        r"\bschtasks(?:\.exe)?\s+/create\b|\bsc(?:\.exe)?\s+create\b|\bnew-service\b"
        r"|\breg(?:\.exe)?\s+add\b|\bnew-itemproperty\b[^\n]*hklm\b",
        "创建计划任务/服务/注册表启动项会留下持久化改动。",
    ),
    _compile(
        "security_setting",
        r"\bset-executionpolicy\b|\benable-psremoting\b|\bnetsh\s+advfirewall\b"
        r"|\bset-netfirewallprofile\b|\bbcdedit\b",
        "修改安全策略或防火墙会降低系统防护。",
    ),
    _compile(
        "publish_artifact",
        r"\bnpm\s+publish\b|\btwine\s+upload\b|\bgh\s+release\s+create\b"
        r"|\bpip\s+[^\n]*\bupload\b|\bdocker\s+push\b",
        "该命令会向外部仓库发布内容，请确认目标与凭据。",
    ),
    _compile(
        "credential_path",
        r"(?:^|[\\/\s\"'=])(?:\.ssh|\.aws|\.kube|\.gnupg|\.docker)[\\/]"
        r"|(?:^|[\\/\s\"'=])(?:id_rsa|id_ed25519|id_ecdsa|\.netrc|\.npmrc|\.pypirc)\b"
        r"|(?:^|[\\/\s\"'=])\.env(?:\.(?:local|production|development|test))?\b"
        r"|[\w.-]+\.(?:pem|key|p12|pfx)\b",
        "命令涉及凭据文件或私钥，执行前请确认不会被上传或打印。",
    ),
    _compile(
        "disk_image",
        r"\bdd\s+if=|\bwsl(?:\.exe)?\s+--export\b|\bdism\b[^\n]*\/apply-image\b",
        "镜像/磁盘写入类命令会覆盖大范围数据。",
    ),
    _compile(
        "mass_kill",
        r"\bkill\s+-9\s+-1\b|\bpkill\s+-9\b|\bkillall\s+-9\b",
        "信号会发送给系统中的大量进程。",
    ),
)


def normalize_command(command: str) -> str:
    """小写化并折叠空白，便于规则匹配。"""
    return re.sub(r"\s+", " ", str(command or "").strip()).lower()


def assess_command(command: str) -> CommandRisk:
    """评估一条命令；返回第一条命中的规则（hardline 优先）。"""
    text = normalize_command(command)
    if not text:
        return SAFE
    for rule, pattern, reason in _HARDLINE_RULES:
        if pattern.search(text):
            return CommandRisk("hardline", rule, reason)
    for rule, pattern, reason in _ELEVATED_RULES:
        if pattern.search(text):
            return CommandRisk("elevated", rule, reason)
    return SAFE
