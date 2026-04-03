"""
成语接龙游戏命令行接口
"""

import argparse
import sys
from src.idiom_game import IdiomGame, play_interactive_game


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="成语接龙游戏")
    parser.add_argument("--mode", choices=["interactive", "test"], default="interactive",
                       help="游戏模式：interactive（交互式）或 test（测试模式）")
    parser.add_argument("--start", type=str, help="指定起始成语")
    parser.add_argument("--version", action="version", version="成语接龙游戏 v1.0")
    
    args = parser.parse_args()
    
    if args.mode == "interactive":
        # 交互式游戏
        if args.start:
            game = IdiomGame()
            success, message = game.start_game(args.start)
            if not success:
                print(f"错误：{message}")
                sys.exit(1)
            
            print("=" * 50)
            print("成语接龙游戏")
            print("=" * 50)
            print(message)
            print()
            
            # 开始游戏循环
            while not game.game_over:
                if game.is_player_turn:
                    print(f"当前成语：{game.current_idiom}")
                    print(f"需要接的成语以 '{game.current_idiom[-1]}' 开头")
                    
                    while True:
                        user_input = input("请输入成语（输入 'hint' 获取提示，输入 'quit' 退出）：").strip()
                        
                        if user_input.lower() == 'quit':
                            print("游戏结束！")
                            return
                        
                        if user_input.lower() == 'hint':
                            success, hint = game.get_hint()
                            if success:
                                print(hint)
                            else:
                                print(hint)
                            continue
                        
                        if not user_input:
                            print("请输入成语！")
                            continue
                        
                        success, message = game.player_move(user_input)
                        if success:
                            print(message)
                            break
                        else:
                            print(f"错误：{message}")
                    
                    print()
                    
                    # AI回合
                    if not game.game_over:
                        success, message = game.ai_move()
                        if success:
                            print(message)
                            print()
                        else:
                            print(message)
                            print()
                            break
                
                # 显示游戏状态
                status = game.get_status()
                print(f"已使用成语数：{status['used_count']}")
                print()
            
            # 游戏结束，显示历史记录
            print("=" * 50)
            print("游戏结束！")
            print("=" * 50)
            print(f"获胜者：{game.winner}")
            print()
            print("游戏历史：")
            for i, (player, idiom) in enumerate(game.get_history(), 1):
                print(f"{i}. {player}: {idiom}")
        else:
            # 使用默认的交互式游戏
            play_interactive_game()
    
    elif args.mode == "test":
        # 测试模式
        print("测试模式")
        print("=" * 50)
        
        game = IdiomGame()
        
        # 测试1：开始游戏
        print("测试1：开始游戏")
        success, message = game.start_game()
        print(f"结果：{success}, 消息：{message}")
        print()
        
        # 测试2：玩家出牌
        print("测试2：玩家出牌")
        from src.idiom_data import get_idioms_by_first_char
        last_char = game.current_idiom[-1]
        candidates = get_idioms_by_first_char(last_char)
        if candidates:
            test_idiom = candidates[0]
            success, message = game.player_move(test_idiom)
            print(f"玩家尝试接：{test_idiom}")
            print(f"结果：{success}, 消息：{message}")
        else:
            print("没有可用的成语进行测试")
        print()
        
        # 测试3：AI出牌
        print("测试3：AI出牌")
        success, message = game.ai_move()
        print(f"结果：{success}, 消息：{message}")
        print()
        
        # 测试4：获取提示
        print("测试4：获取提示")
        success, message = game.get_hint()
        print(f"结果：{success}, 消息：{message}")
        print()
        
        # 测试5：获取游戏状态
        print("测试5：获取游戏状态")
        status = game.get_status()
        print(f"游戏状态：{status}")
        print()
        
        # 测试6：获取历史记录
        print("测试6：获取历史记录")
        history = game.get_history()
        print(f"历史记录：")
        for i, (player, idiom) in enumerate(history, 1):
            print(f"  {i}. {player}: {idiom}")
        print()
        
        # 测试7：验证无效成语
        print("测试7：验证无效成语")
        success, message = game.player_move("不是成语")
        print(f"结果：{success}, 消息：{message}")
        print()
        
        # 测试8：验证重复成语
        print("测试8：验证重复成语")
        if game.history:
            first_idiom = game.history[0][1]
            success, message = game.player_move(first_idiom)
            print(f"尝试重复成语：{first_idiom}")
            print(f"结果：{success}, 消息：{message}")
        else:
            print("没有历史记录可供测试")
        print()
        
        print("=" * 50)
        print("测试完成！")


if __name__ == "__main__":
    main()
