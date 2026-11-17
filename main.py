# main.py
"""Agent CLI entry point: multi-turn context, session persistence, and context compaction.

Usage:
    python main.py "What time is it in Shenzhen?"    # single question, continuing the current session
    python main.py                                   # interactive multi-turn chat
    python main.py --debug "What time is it?"        # print this turn's message trace
    python main.py --session work "Weather tomorrow?"  # use a named session (history survives restarts)
    python main.py --new                             # clear the current session history, then start
    python main.py --list-sessions                   # list saved sessions
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from agents.agent import build_agent, build_cli_approval, build_model, cli_tool_context
from agents.approval import ApprovalPolicy
from agents.context import ChatSession, SessionStore, TurnResult, message_text
from config.settings import settings
from config.storage import ensure_storage
from tools.context import SessionRef
from tools.processes import ProcessManager
from tools.questions import console_asker
from tools.sandbox import create_sandbox
from tools.sandbox.base import SandboxPolicy
from tools.sandbox.config import SandboxConfigStore
from tools.todo import TodoStore, render_todos

HELP_TEXT = """可用命令：
  /help            显示这份帮助
  /new [会话ID]    开启新会话（清空历史；不填则按时间生成 ID）
  /session <会话ID> 切换到已有会话，继续之前的历史
  /sessions        列出已保存的会话
  /history [n]     查看当前会话最近 n 条消息（默认 10）
  /todos           查看当前会话的任务清单
  /approvals       查看本会话的「始终允许」；加 clear 清除
  /compact         立即压缩上下文（把较早的对话生成摘要）
  /status          查看当前会话状态（轮次、消息数、token 估算）
  /debug           切换调试模式（打印消息轨迹）
  /exit            退出（/quit 等价）"""

_ROLE_NAMES = {
    "human": "用户",
    "ai": "助手",
    "tool": "工具",
    "system": "系统",
}


def _truncate(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


class ChatCLI:
    """Wire command-line arguments, sessions, and interactive commands together."""

    def __init__(
        self,
        agent,
        summarizer=None,
        *,
        session_id: str | None = None,
        debug: bool = False,
        store: SessionStore | None = None,
        approval: ApprovalPolicy | None = None,
        session_ref: SessionRef | None = None,
        todo_store: TodoStore | None = None,
    ) -> None:
        self.agent = agent
        self.summarizer = summarizer
        self.store = store or SessionStore()
        self.debug = debug
        self.approval = approval
        self.session_ref = session_ref or SessionRef(session_id or settings.session_id)
        self.session_ref.session_id = session_id or settings.session_id
        self.todo_store = todo_store or TodoStore()
        self.session = ChatSession(
            agent,
            self.session_ref.session_id,
            store=self.store,
            summarizer=summarizer,
        )

    # ---- 对外入口 ----
    def ask_once(self, question: str) -> None:
        try:
            self._ask(question)
        except KeyboardInterrupt:
            print("\n[已中断本轮]")
        except Exception as exc:
            print(f"[错误] 本轮调用失败: {exc}", file=sys.stderr)

    def interactive(self) -> None:
        print("交互模式已启动。输入问题回车发送，/help 查看命令，/exit 退出。")
        print(self._status_line())
        while True:
            try:
                line = input(f"你[{self.session.session_id}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self._handle_command(line):
                    break
                continue
            self.ask_once(line)

    # ---- 内部实现 ----
    def _ask(self, question: str) -> None:
        result = self.session.ask(question)
        if self.debug:
            self._print_trace(result)
        print(result.text)
        if result.compacted:
            print("[提示] 上下文已自动压缩，较早的对话已折叠为摘要。")

    def _status_line(self) -> str:
        stats = self.session.stats()
        source = "API 用量" if stats["token_source"] == "provider" else "本地估算"
        return (
            f"[会话 {stats['session_id']}] 已完成 {stats['turns']} 轮，"
            f"历史 {stats['messages']} 条消息，约 {stats['tokens']} tokens（{source}）；"
            f"压缩阈值 {stats['max_tokens']} tokens / {stats['max_messages']} 条"
            f"（窗口 {stats['model_context_window']}，预留 {stats['reserve_tokens']}）"
        )

    def _print_trace(self, result: TurnResult) -> None:
        print("\n--- 本轮消息轨迹 ---")
        for message in result.new_messages:
            role = _ROLE_NAMES.get(getattr(message, "type", ""), message.__class__.__name__)
            print(f"[{role}] {_truncate(message_text(message) or '<空>', 500)}")
        print(f"--- 轨迹结束（上下文约 {result.context_tokens} tokens）---\n")

    def _handle_command(self, line: str) -> bool:
        """Handle a command starting with ``/``; return False to exit the program."""
        parts = line.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command in {"/exit", "/quit", "q"}:
            print("再见。")
            return False
        if command == "/help":
            print(HELP_TEXT)
        elif command == "/new":
            self._new_session(argument)
        elif command == "/session":
            self._switch_session(argument)
        elif command == "/sessions":
            self._list_sessions()
        elif command == "/history":
            self._show_history(argument)
        elif command == "/todos":
            self._show_todos()
        elif command == "/approvals":
            self._show_approvals(argument)
        elif command == "/compact":
            self._compact()
        elif command == "/status":
            print(self._status_line())
        elif command == "/debug":
            self.debug = not self.debug
            print(f"调试模式：{'开' if self.debug else '关'}")
        else:
            print(f"未知命令：{command}。输入 /help 查看可用命令。")
        return True

    def _new_session(self, session_id: str) -> None:
        new_id = session_id or f"session-{datetime.now():%Y%m%d-%H%M%S}"
        self.session = ChatSession(
            self.agent,
            new_id,
            store=self.store,
            summarizer=self.summarizer,
        )
        self.session.reset()
        self.session_ref.session_id = new_id
        self.todo_store.clear(new_id)
        self._reset_approvals()
        print(f"已开启新会话：{new_id}")

    def _switch_session(self, session_id: str) -> None:
        if not session_id:
            print("用法：/session <会话ID>")
            return
        self.session = ChatSession(
            self.agent,
            session_id,
            store=self.store,
            summarizer=self.summarizer,
        )
        self.session_ref.session_id = session_id
        self._reset_approvals()
        print(f"已切换到会话 {session_id}。{self._status_line()}")

    def _reset_approvals(self) -> None:
        """Always-allow grants are remembered per session and dropped when the session changes."""
        if self.approval is not None:
            self.approval.clear()

    def _show_approvals(self, argument: str) -> None:
        if self.approval is None:
            print("审批未启用（APPROVAL_ENABLED=0）。")
            return
        if argument.strip().lower() in {"clear", "reset", "清除"}:
            print(f"已清除 {self.approval.clear()} 条本会话授权。")
            return
        items = self.approval.items()
        if not items:
            print("本会话还没有「始终允许」的授权。")
            return
        print(f"本会话已记住 {len(items)} 条「始终允许」：")
        for item in items:
            print(f"  - {item['summary']}")
        print("用 /approvals clear 全部清除。")

    def _list_sessions(self) -> None:
        sessions = self.store.list_sessions()
        if not sessions:
            print("还没有保存的会话。")
            return
        print("已保存的会话（* 为当前会话）：")
        for meta in sessions:
            marker = "*" if meta.session_id == self.session.session_id else " "
            print(
                f"{marker} {meta.session_id}\t{meta.turns} 轮 / {meta.message_count} 条"
                f"\t更新于 {meta.updated_at or '未知'}"
            )

    def _show_history(self, argument: str) -> None:
        limit = 10
        if argument:
            try:
                limit = max(1, int(argument))
            except ValueError:
                print("用法：/history [条数]")
                return
        messages = self.session.recent(limit)
        if not messages:
            print("当前会话还没有历史消息。")
            return
        print(f"最近 {len(messages)} 条消息：")
        for message in messages:
            role = _ROLE_NAMES.get(getattr(message, "type", ""), message.__class__.__name__)
            print(f"  [{role}] {_truncate(message_text(message) or '<空>', 120)}")

    def _show_todos(self) -> None:
        items = self.todo_store.load(self.session.session_id)
        print(render_todos(items))

    def _compact(self) -> None:
        if self.summarizer is None:
            print("当前会话没有可用的摘要模型，无法压缩。")
            return
        try:
            changed = self.session.compact()
        except Exception as exc:
            print(f"[错误] 压缩失败: {exc}", file=sys.stderr)
            return
        if changed:
            print(f"上下文已压缩。{self._status_line()}")
        else:
            print("当前没有可压缩的历史（消息太少）。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangChain Agent 命令行入口（带上下文与会话）")
    parser.add_argument("question", nargs="?", help="要问的问题；不提供则进入交互模式")
    parser.add_argument("--debug", action="store_true", help="打印本轮消息轨迹")
    parser.add_argument("--session", default=settings.session_id, help="会话 ID，默认 default")
    parser.add_argument("--new", action="store_true", help="清空当前会话的历史")
    parser.add_argument("--list-sessions", action="store_true", help="列出已保存的会话")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    # 用户数据目录：首次运行会把仓库里的 data/ memory/ 迁过去
    storage = ensure_storage()
    for name in storage["migrated"]:
        print(f"已迁移历史数据：{name} → {storage['home']}")

    if args.list_sessions:
        sessions = SessionStore().list_sessions()
        if not sessions:
            print("还没有保存的会话。")
            return
        for meta in sessions:
            print(
                f"{meta.session_id}\t{meta.turns} 轮 / {meta.message_count} 条"
                f"\t更新于 {meta.updated_at or '未知'}"
            )
        return

    gate = build_cli_approval()
    session_ref = SessionRef(args.session)
    todo_store = TodoStore(SessionStore().directory / "todos")
    process_manager = ProcessManager(Path(settings.app_home) / "logs" / "processes")
    sandbox_config = SandboxConfigStore(Path(settings.data_dir) / "sandbox.json")
    sandbox = create_sandbox(config=sandbox_config)
    workspace = Path(settings.workspace_dir or Path.cwd()).expanduser().resolve()
    policy = SandboxPolicy(
        workspace=workspace,
        session_id=args.session,
        network=str(sandbox_config.get().get("network") or "offline"),
        allowed_domains=tuple(sandbox_config.get().get("allowed_domains") or ()),
        allowed_ports=tuple(int(item) for item in (sandbox_config.get().get("allowed_ports") or (443, 80))),
    )
    ctx = cli_tool_context(
        session_ref=session_ref,
        asker=console_asker,
        todo_store=todo_store,
        process_manager=process_manager,
        sandbox=sandbox,
        sandbox_policy=policy,
    )
    try:
        model = build_model()
        agent = build_agent(model=model, approval=gate, ctx=ctx)
    except RuntimeError as exc:  # 缺少 API Key 等配置问题
        print(f"[配置错误] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    cli = ChatCLI(
        agent,
        model,
        session_id=args.session,
        debug=args.debug,
        approval=gate.policy if gate else None,
        session_ref=session_ref,
        todo_store=todo_store,
    )
    if args.new:
        cli.session.reset()
        print(f"已清空会话 {cli.session.session_id} 的历史。")

    if args.question:
        cli.ask_once(args.question)
    else:
        cli.interactive()


if __name__ == "__main__":
    main()
