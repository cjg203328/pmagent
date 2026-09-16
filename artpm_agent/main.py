"""
Main entry point for ArtPM Copilot
"""
import sys
from uuid import uuid4

from artpm_agent.runtime.factory import get_runtime_factory
from artpm_agent.utils.chat_intent import is_local_fast_intent


def _build_cli_services(factory):
    """Create the same request services used by the API and UI hosts."""
    from artpm_agent.tenancy import TenantContext

    tenant_context = TenantContext.local()
    conversation_store = factory.storage.conversation
    conversation = conversation_store.create_conversation(
        "CLI session",
        workspace_id=tenant_context.require_workspace(),
    )
    services = factory.turn_services(tenant_context)
    return conversation["id"], conversation_store, tenant_context, services


def print_banner():
    """Print welcome banner"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              ArtPM Copilot v1.0 (MVP)                       ║
║         游戏美术资产PM智能Agent系统                          ║
║                                                              ║
║  核心功能:                                                   ║
║  • 文档智能识别与分类                                        ║
║  • 报价测算与利润分析                                        ║
║  • 智能任务分配                                              ║
║  • 进度追踪与提醒                                            ║
║  • 历史数据检索                                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def print_help():
    """Print help message"""
    help_text = """
可用命令:

  /help          - 显示帮助信息
  /skills        - 列出所有可用技能
  /quit, /exit   - 退出程序

  chat <消息>    - 与Agent对话

示例:
  chat 你好，介绍一下你的功能
  /skills
"""
    print(help_text)


def main():
    """Main function"""
    print_banner()

    # Initialize agent
    try:
        print("[System] Initializing ArtPM Agent...")
        factory = get_runtime_factory()
        agent = factory.agent()
        (
            conversation_id,
            conversation_store,
            tenant_context,
            turn_services,
        ) = _build_cli_services(factory)
        runtime = factory.harness_runtime(tenant_context)
        print("[System] Agent ready!\n")
    except Exception as e:
        print(f"[Error] Failed to initialize agent: {e}")
        sys.exit(1)

    print("输入 /help 查看帮助信息")
    print("=" * 60)
    history = []

    # Main loop
    while True:
        try:
            user_input = input("\n你: ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                cmd = user_input.lower()

                if cmd in ["/quit", "/exit"]:
                    print("\n再见！")
                    break
                elif cmd == "/help":
                    print_help()
                elif cmd == "/skills":
                    skills = agent.list_skills()
                    print("\n可用技能列表:")
                    print("-" * 60)
                    for skill in skills:
                        print(f"• {skill['skill_name']}")
                        print(f"  描述: {skill['description']}")
                        print(f"  版本: {skill['version']}")
                        print()
                else:
                    print(f"未知命令: {user_input}")
                    print("输入 /help 查看可用命令")

            # Handle chat
            else:
                print("\nAgent: ", end="", flush=True)
                turn_context = runtime.build_turn_context(
                    user_input,
                    turn_id=f"turn-{uuid4().hex}",
                    conversation_id=conversation_id,
                    conversation_history=history[-16:],
                    extra={
                        "turn_mode": (
                            "fast" if is_local_fast_intent(user_input) else "standard"
                        ),
                    },
                    services=turn_services,
                )
                result = runtime.run_turn(turn_context)
                print(result.response)
                if not result.success:
                    print(
                        f"[Error code: {result.metadata.get('error_code', 'turn_failed')}]"
                    )
                conversation_store.add_message(
                    conversation_id,
                    "user",
                    user_input,
                    turn_id=turn_context.turn_id,
                    workspace_id=turn_context.scope.workspace_id,
                )
                conversation_store.add_message(
                    conversation_id,
                    "assistant",
                    result.response,
                    status="complete" if result.success else "error",
                    turn_id=turn_context.turn_id,
                    model_id=result.metadata.get("model"),
                    metadata=result.metadata,
                    workspace_id=turn_context.scope.workspace_id,
                )
                history.extend(
                    [
                        {"role": "user", "content": user_input},
                        {"role": "assistant", "content": result.response},
                    ]
                )

        except KeyboardInterrupt:
            print("\n\n中断，退出程序...")
            break
        except Exception as e:
            print(f"\n[Error] {e}")


if __name__ == "__main__":
    main()
