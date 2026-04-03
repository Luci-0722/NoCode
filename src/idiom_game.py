"""
成语接龙游戏核心逻辑
"""

import random
from typing import Optional, Tuple
from src.idiom_data import get_idioms_by_first_char, is_valid_idiom, get_all_idioms


class IdiomGame:
    """成语接龙游戏类"""
    
    def __init__(self):
        """初始化游戏"""
        self.used_idioms = set()  # 已使用的成语
        self.history = []  # 游戏历史记录
        self.current_idiom = None  # 当前成语
        self.is_player_turn = True  # 是否为玩家回合
        self.game_over = False  # 游戏是否结束
        self.winner = None  # 获胜者
        
    def start_game(self, first_idiom: Optional[str] = None) -> Tuple[bool, str]:
        """
        开始游戏
        
        Args:
            first_idiom: 第一个成语，如果为None则随机选择
            
        Returns:
            (是否成功, 消息)
        """
        self.used_idioms.clear()
        self.history.clear()
        self.game_over = False
        self.winner = None
        self.is_player_turn = True
        
        if first_idiom:
            if not is_valid_idiom(first_idiom):
                return False, f"'{first_idiom}' 不是有效的成语"
            self.current_idiom = first_idiom
        else:
            # 随机选择一个成语开始
            all_idioms = get_all_idioms()
            self.current_idiom = random.choice(all_idioms)
        
        self.used_idioms.add(self.current_idiom)
        self.history.append(("系统", self.current_idiom))
        
        return True, f"游戏开始！第一个成语是：{self.current_idiom}"
    
    def check_chain_rule(self, prev_idiom: str, curr_idiom: str) -> bool:
        """
        检查接龙规则
        
        Args:
            prev_idiom: 上一个成语
            curr_idiom: 当前成语
            
        Returns:
            是否符合接龙规则
        """
        if not prev_idiom or not curr_idiom:
            return False
        
        # 检查当前成语的最后一个字是否与上一个成语的第一个字相同
        return prev_idiom[-1] == curr_idiom[0]
    
    def player_move(self, idiom: str) -> Tuple[bool, str]:
        """
        玩家出牌
        
        Args:
            idiom: 玩家输入的成语
            
        Returns:
            (是否成功, 消息)
        """
        if self.game_over:
            return False, "游戏已经结束"
        
        if not self.is_player_turn:
            return False, "现在不是你的回合"
        
        # 检查成语是否有效
        if not is_valid_idiom(idiom):
            return False, f"'{idiom}' 不是有效的成语"
        
        # 检查成语是否已被使用
        if idiom in self.used_idioms:
            return False, f"'{idiom}' 已经被使用过了"
        
        # 检查接龙规则
        if self.current_idiom and not self.check_chain_rule(self.current_idiom, idiom):
            return False, f"'{idiom}' 不能接 '{self.current_idiom}'，需要以 '{self.current_idiom[-1]}' 开头"
        
        # 玩家出牌成功
        self.current_idiom = idiom
        self.used_idioms.add(idiom)
        self.history.append(("玩家", idiom))
        self.is_player_turn = False
        
        return True, f"你接了：{idiom}"
    
    def ai_move(self) -> Tuple[bool, str]:
        """
        AI出牌
        
        Returns:
            (是否成功, 消息)
        """
        if self.game_over:
            return False, "游戏已经结束"
        
        if self.is_player_turn:
            return False, "现在是玩家的回合"
        
        # 获取当前成语的最后一个字
        last_char = self.current_idiom[-1]
        
        # 查找可以接的成语
        candidates = get_idioms_by_first_char(last_char)
        
        # 过滤掉已使用的成语
        available = [idiom for idiom in candidates if idiom not in self.used_idioms]
        
        if not available:
            # AI无法接出，玩家获胜
            self.game_over = True
            self.winner = "玩家"
            return False, f"AI 无法接出以 '{last_char}' 开头的成语，玩家获胜！"
        
        # AI随机选择一个成语
        ai_idiom = random.choice(available)
        
        # AI出牌成功
        self.current_idiom = ai_idiom
        self.used_idioms.add(ai_idiom)
        self.history.append(("AI", ai_idiom))
        self.is_player_turn = True
        
        return True, f"AI 接了：{ai_idiom}"
    
    def get_hint(self) -> Tuple[bool, str]:
        """
        获取提示
        
        Returns:
            (是否成功, 提示信息)
        """
        if self.game_over:
            return False, "游戏已经结束"
        
        if not self.current_idiom:
            return False, "游戏尚未开始"
        
        # 获取当前成语的最后一个字
        last_char = self.current_idiom[-1]
        
        # 查找可以接的成语
        candidates = get_idioms_by_first_char(last_char)
        
        # 过滤掉已使用的成语
        available = [idiom for idiom in candidates if idiom not in self.used_idioms]
        
        if not available:
            return False, f"没有可以接的成语了，以 '{last_char}' 开头的成语都已被使用"
        
        # 随机选择3个成语作为提示
        hints = random.sample(available, min(3, len(available)))
        return True, f"可以接的成语（以 '{last_char}' 开头）：{', '.join(hints)}"
    
    def get_history(self) -> list[Tuple[str, str]]:
        """获取游戏历史记录"""
        return self.history.copy()
    
    def get_status(self) -> dict:
        """
        获取游戏状态
        
        Returns:
            游戏状态字典
        """
        return {
            "current_idiom": self.current_idiom,
            "is_player_turn": self.is_player_turn,
            "game_over": self.game_over,
            "winner": self.winner,
            "used_count": len(self.used_idioms),
            "history_count": len(self.history)
        }
    
    def reset(self) -> None:
        """重置游戏"""
        self.used_idioms.clear()
        self.history.clear()
        self.current_idiom = None
        self.is_player_turn = True
        self.game_over = False
        self.winner = None


def play_interactive_game() -> None:
    """交互式游戏"""
    game = IdiomGame()
    
    print("=" * 50)
    print("成语接龙游戏")
    print("=" * 50)
    print("游戏规则：")
    print("1. 系统或AI给出一个成语")
    print("2. 玩家需要接一个以该成语最后一个字开头的成语")
    print("3. 成语不能重复使用")
    print("4. 如果接不出，对方获胜")
    print("=" * 50)
    print()
    
    # 开始游戏
    success, message = game.start_game()
    print(message)
    print()
    
    while not game.game_over:
        # 玩家回合
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


if __name__ == "__main__":
    play_interactive_game()
