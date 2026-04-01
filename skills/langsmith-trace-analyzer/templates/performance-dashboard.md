# 性能仪表板设计

## 仪表板布局

```
┌─────────────────────────────────────────────────────────────┐
│                        性能概览                              │
├─────────────────────────────────────────────────────────────┤
│  总执行时间: 5.2s  │  总令牌数: 1,234  │  总成本: $0.0123 │
│  错误率: 0%       │  工具调用: 15     │  重试次数: 0     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                        时间线图表                            │
├─────────────────────────────────────────────────────────────┤
│  [时间线图表显示各个事件的执行时间]                          │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                        工具调用统计                          │
├─────────────────────────────────────────────────────────────┤
│  read_file: 5次 (平均1.2s)                                │
│  write_file: 3次 (平均0.8s)                               │
│  execute_command: 7次 (平均2.3s)                          │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                        错误分析                              │
├─────────────────────────────────────────────────────────────┤
│  无错误                                                        │
└─────────────────────────────────────────────────────────────┘
```

## 关键指标说明

### 1. 执行时间指标
- **总执行时间**: 从开始到结束的总时间
- **平均执行时间**: 所有事件的平均执行时间
- **最长执行时间**: 单个事件的最长执行时间
- **执行时间分布**: 时间分布统计

### 2. 令牌使用指标
- **输入令牌数**: 所有输入的令牌总数
- **输出令牌数**: 所有输出的令牌总数
- **总令牌数**: 输入输出令牌总和
- **令牌效率**: 每秒处理的令牌数

### 3. 成本指标
- **总成本**: 所有操作的总成本
- **平均成本**: 每次操作的平均成本
- **成本分布**: 不同操作的成本分布

### 4. 错误指标
- **错误数量**: 总错误数量
- **错误率**: 错误占总操作的比例
- **错误类型**: 不同类型的错误统计
- **错误趋势**: 错误随时间的变化

### 5. 工具调用指标
- **调用次数**: 各工具的调用次数
- **平均执行时间**: 各工具的平均执行时间
- **成功率**: 各工具的成功率
- **调用频率**: 每秒的调用次数

## 可视化组件

### 1. 时间线图
```python
def generate_timeline_chart(analysis: TraceAnalysis):
    """生成时间线图表"""
    events = analysis.events
    timestamps = [e.timestamp for e in events]
    durations = [e.duration for e in events]
    
    # 使用matplotlib生成时间线图
    plt.figure(figsize=(12, 6))
    plt.bar(range(len(events)), durations)
    plt.xlabel('事件序号')
    plt.ylabel('执行时间 (秒)')
    plt.title('执行时间分布')
    plt.show()
```

### 2. 饼图
```python
def generate_pie_chart(analysis: TraceAnalysis):
    """生成工具调用分布饼图"""
    tool_stats = {}
    for event in analysis.events:
        if event.type == "tool_call":
            tool_name = event.metadata.get("tool", "unknown")
            tool_stats[tool_name] = tool_stats.get(tool_name, 0) + 1
    
    plt.figure(figsize=(8, 8))
    plt.pie(tool_stats.values(), labels=tool_stats.keys(), autopct='%1.1f%%')
    plt.title('工具调用分布')
    plt.show()
```

### 3. 趋势图
```python
def generate_trend_chart(analyses: List[TraceAnalysis]):
    """生成趋势图"""
    timestamps = [a.timestamp for a in analyses]
    durations = [a.total_duration for a in analyses]
    
    plt.figure(figsize=(12, 6))
    plt.plot(timestamps, durations, marker='o')
    plt.xlabel('时间')
    plt.ylabel('执行时间 (秒)')
    plt.title('执行时间趋势')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()
```

## 性能阈值配置

```python
# 性能阈值配置
PERFORMANCE_THRESHOLDS = {
    "execution_time": {
        "warning": 10.0,    # 警告阈值（秒）
        "critical": 30.0    # 严重阈值（秒）
    },
    "error_rate": {
        "warning": 5.0,     # 警告阈值（%）
        "critical": 15.0    # 严重阈值（%）
    },
    "token_usage": {
        "warning": 10000,   # 警告阈值
        "critical": 50000   # 严重阈值
    },
    "memory_usage": {
        "warning": 500,     # 警告阈值（MB）
        "critical": 1000    # 严重阈值（MB）
    }
}
```

## 报警规则

```python
def check_performance_thresholds(analysis: TraceAnalysis, thresholds: Dict):
    """检查性能阈值"""
    alerts = []
    
    # 检查执行时间
    if analysis.total_duration > thresholds["execution_time"]["critical"]:
        alerts.append("严重: 执行时间超过临界值")
    elif analysis.total_duration > thresholds["execution_time"]["warning"]:
        alerts.append("警告: 执行时间超过警告值")
    
    # 检查错误率
    if analysis.metrics.get("error_rate", 0) > thresholds["error_rate"]["critical"]:
        alerts.append("严重: 错误率超过临界值")
    elif analysis.metrics.get("error_rate", 0) > thresholds["error_rate"]["warning"]:
        alerts.append("警告: 错误率超过警告值")
    
    return alerts
```