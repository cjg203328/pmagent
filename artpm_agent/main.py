"""
Main entry point for ArtPM Copilot
"""
import sys

from artpm_agent.agent import ArtPMAgent


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
        agent = ArtPMAgent()
        print("[System] Agent ready!\n")
    except Exception as e:
        print(f"[Error] Failed to initialize agent: {e}")
        sys.exit(1)

    print("输入 /help 查看帮助信息")
    print("=" * 60)

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
                response = agent.chat(user_input)
                print(response)

        except KeyboardInterrupt:
            print("\n\n中断，退出程序...")
            break
        except Exception as e:
            print(f"\n[Error] {e}")


if __name__ == "__main__":
    main()
