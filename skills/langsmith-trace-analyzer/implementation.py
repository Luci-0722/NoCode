"""LangSmith追踪分析技能实现"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from enum import Enum
import statistics


class TraceMetric(Enum):
    """追踪指标类型"""
    EXECUTION_TIME = "execution_time"
    TOKEN_USAGE = "token_usage"
    ERROR_RATE = "error_rate"
    TOOL_CALLS = "tool_calls"
    RETRY_COUNT = "retry_count"
    MEMORY_USAGE = "memory_usage"
    COST = "cost"


@dataclass
class TraceEvent:
    """追踪事件"""
    id: str
    type: str
    timestamp: datetime
    duration: float
    metadata: Dict[str, Any]
    input: Optional[Dict[str, Any]]
    output: Optional[Dict[str, Any]]
    error: Optional[str]


@dataclass
class TraceAnalysis:
    """追踪分析结果"""
    trace_id: str
    total_duration: float
    total_tokens: int
    total_cost: float
    error_count: int
    tool_call_count: int
    retry_count: int
    metrics: Dict[str, float]
    recommendations: List[str]
    issues: List[str]


class LangSmithTraceAnalyzer:
    """LangSmith追踪分析器"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.cache = {}
    
    def analyze_trace(self, trace_data: Union[str, Dict]) -> TraceAnalysis:
        """分析追踪数据"""
        if isinstance(trace_data, str):
            trace_data = json.loads(trace_data)
        
        # 解析追踪事件
        events = self._parse_trace_events(trace_data)
        
        # 计算指标
        metrics = self._calculate_metrics(events)
        
        # 识别问题
        issues = self._identify_issues(events, metrics)
        
        # 生成建议
        recommendations = self._generate_recommendations(metrics, issues)
        
        return TraceAnalysis(
            trace_id=trace_data.get("trace_id", "unknown"),
            total_duration=metrics.get("total_duration", 0),
            total_tokens=metrics.get("total_tokens", 0),
            total_cost=metrics.get("total_cost", 0),
            error_count=metrics.get("error_count", 0),
            tool_call_count=metrics.get("tool_call_count", 0),
            retry_count=metrics.get("retry_count", 0),
            metrics=metrics,
            recommendations=recommendations,
            issues=issues
        )
    
    def analyze_traces_batch(self, traces_data: List[Union[str, Dict]]) -> List[TraceAnalysis]:
        """批量分析追踪数据"""
        analyses = []
        for trace_data in traces_data:
            analysis = self.analyze_trace(trace_data)
            analyses.append(analysis)
        
        # 计算整体统计
        batch_stats = self._calculate_batch_statistics(analyses)
        
        # 添加批量分析结果
        for analysis in analyses:
            analysis.batch_stats = batch_stats
        
        return analyses
    
    def _parse_trace_events(self, trace_data: Dict) -> List[TraceEvent]:
        """解析追踪事件"""
        events = []
        
        # 解析根事件
        root_event = trace_data.get("root", {})
        if root_event:
            event = TraceEvent(
                id=root_event.get("id", "root"),
                type=root_event.get("type", "root"),
                timestamp=datetime.fromisoformat(root_event.get("timestamp", datetime.now().isoformat())),
                duration=root_event.get("duration", 0),
                metadata=root_event.get("metadata", {}),
                input=root_event.get("input"),
                output=root_event.get("output"),
                error=root_event.get("error")
            )
            events.append(event)
        
        # 解析子事件
        for child_event in trace_data.get("children", []):
            event = TraceEvent(
                id=child_event.get("id"),
                type=child_event.get("type"),
                timestamp=datetime.fromisoformat(child_event.get("timestamp", datetime.now().isoformat())),
                duration=child_event.get("duration", 0),
                metadata=child_event.get("metadata", {}),
                input=child_event.get("input"),
                output=child_event.get("output"),
                error=child_event.get("error")
            )
            events.append(event)
        
        return events
    
    def _calculate_metrics(self, events: List[TraceEvent]) -> Dict[str, float]:
        """计算性能指标"""
        metrics = {}
        
        # 总执行时间
        metrics["total_duration"] = sum(event.duration for event in events)
        
        # 令牌使用
        total_tokens = 0
        for event in events:
            if event.input and "messages" in event.input:
                for message in event.input["messages"]:
                    if "content" in message:
                        total_tokens += len(str(message["content"]))
            if event.output and "content" in event.output:
                total_tokens += len(str(event.output["content"]))
        
        metrics["total_tokens"] = total_tokens
        
        # 成本估算（简化计算）
        metrics["total_cost"] = total_tokens * 0.00001  # 假设每token成本
        
        # 错误统计
        metrics["error_count"] = sum(1 for event in events if event.error)
        
        # 工具调用统计
        metrics["tool_call_count"] = sum(
            1 for event in events 
            if event.type in ["tool_call", "function_call"]
        )
        
        # 重试统计
        metrics["retry_count"] = sum(
            event.metadata.get("retry_count", 0) for event in events
        )
        
        # 平均执行时间
        if len(events) > 0:
            metrics["avg_duration"] = metrics["total_duration"] / len(events)
        else:
            metrics["avg_duration"] = 0
        
        # 错误率
        metrics["error_rate"] = (metrics["error_count"] / len(events)) * 100 if len(events) > 0 else 0
        
        # 每秒处理请求数
        if metrics["total_duration"] > 0:
            metrics["requests_per_second"] = len(events) / metrics["total_duration"]
        else:
            metrics["requests_per_second"] = 0
        
        return metrics
    
    def _identify_issues(self, events: List[TraceEvent], metrics: Dict[str, float]) -> List[str]:
        """识别性能问题"""
        issues = []
        
        # 检查执行时间过长
        if metrics["total_duration"] > 10:  # 超过10秒
            issues.append(f"总执行时间过长: {metrics['total_duration']:.2f}秒")
        
        # 检查错误率过高
        if metrics["error_rate"] > 5:  # 超过5%
            issues.append(f"错误率过高: {metrics['error_rate']:.2f}%")
        
        # 检查重试次数过多
        if metrics["retry_count"] > 3:  # 超过3次
            issues.append(f"重试次数过多: {metrics['retry_count']}次")
        
        # 检查工具调用异常
        tool_call_events = [e for e in events if e.type in ["tool_call", "function_call"]]
        for event in tool_call_events:
            if event.error:
                issues.append(f"工具调用失败: {event.error}")
        
        # 检查内存使用
        memory_usage = sum(event.metadata.get("memory_usage", 0) for event in events)
        if memory_usage > 1000:  # 超过1GB
            issues.append(f"内存使用过高: {memory_usage}MB")
        
        return issues
    
    def _generate_recommendations(self, metrics: Dict[str, float], issues: List[str]) -> List[str]:
        """生成优化建议"""
        recommendations = []
        
        # 基于执行时间的建议
        if metrics["total_duration"] > 5:
            recommendations.append("考虑使用并行处理来减少总执行时间")
        
        # 基于错误率的建议
        if metrics["error_rate"] > 2:
            recommendations.append("增加错误处理机制和重试逻辑")
        
        # 基于令牌使用的建议
        if metrics["total_tokens"] > 10000:
            recommendations.append("考虑压缩输入输出以减少令牌使用")
        
        # 基于工具调用的建议
        if metrics["tool_call_count"] > 10:
            recommendations.append("优化工具调用策略，减少不必要的调用")
        
        # 基于重试次数的建议
        if metrics["retry_count"] > 2:
            recommendations.append("检查输入数据质量，减少重试需求")
        
        # 通用建议
        if not issues:
            recommendations.append("系统运行良好，建议定期监控性能指标")
        
        return recommendations
    
    def _calculate_batch_statistics(self, analyses: List[TraceAnalysis]) -> Dict[str, float]:
        """计算批量统计信息"""
        if not analyses:
            return {}
        
        durations = [a.total_duration for a in analyses]
        tokens = [a.total_tokens for a in analyses]
        costs = [a.total_cost for a in analyses]
        error_rates = [a.metrics.get("error_rate", 0) for a in analyses]
        
        return {
            "avg_duration": statistics.mean(durations),
            "max_duration": max(durations),
            "min_duration": min(durations),
            "avg_tokens": statistics.mean(tokens),
            "avg_cost": statistics.mean(costs),
            "avg_error_rate": statistics.mean(error_rates),
            "total_traces": len(analyses),
            "total_errors": sum(a.error_count for a in analyses)
        }
    
    def generate_performance_report(self, analysis: TraceAnalysis) -> str:
        """生成性能报告"""
        report = []
        
        report.append("# LangSmith追踪分析报告")
        report.append(f"**追踪ID**: {analysis.trace_id}")
        report.append(f"**分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # 基本指标
        report.append("## 基本指标")
        report.append(f"- **总执行时间**: {analysis.total_duration:.2f}秒")
        report.append(f"- **总令牌数**: {analysis.total_tokens:,}")
        report.append(f"- **总成本**: ${analysis.total_cost:.4f}")
        report.append(f"- **错误数**: {analysis.error_count}")
        report.append(f"- **工具调用数**: {analysis.tool_call_count}")
        report.append(f"- **重试数**: {analysis.retry_count}")
        report.append("")
        
        # 详细指标
        report.append("## 详细指标")
        for metric_name, value in analysis.metrics.items():
            report.append(f"- **{metric_name}**: {value:.2f}")
        report.append("")
        
        # 问题分析
        if analysis.issues:
            report.append("## 发现的问题")
            for issue in analysis.issues:
                report.append(f"- ❌ {issue}")
            report.append("")
        
        # 优化建议
        if analysis.recommendations:
            report.append("## 优化建议")
            for i, recommendation in enumerate(analysis.recommendations, 1):
                report.append(f"- {i}. ✅ {recommendation}")
            report.append("")
        
        # 总结
        report.append("## 总结")
        if analysis.issues:
            report.append(f"发现 {len(analysis.issues)} 个问题，建议优先处理。")
        else:
            report.append("未发现明显问题，系统运行正常。")
        
        return "\\n".join(report)
    
    def compare_traces(self, analysis1: TraceAnalysis, analysis2: TraceAnalysis) -> Dict[str, Any]:
        """比较两个追踪分析结果"""
        comparison = {}
        
        # 比较各项指标
        metrics_to_compare = [
            "total_duration", "total_tokens", "total_cost", 
            "error_count", "tool_call_count", "retry_count"
        ]
        
        for metric in metrics_to_compare:
            value1 = getattr(analysis1, metric)
            value2 = getattr(analysis2, metric)
            change = value2 - value1
            change_percent = (change / value1 * 100) if value1 != 0 else 0
            
            comparison[metric] = {
                "before": value1,
                "after": value2,
                "change": change,
                "change_percent": change_percent
            }
        
        return comparison


# 使用示例
if __name__ == "__main__":
    # 创建分析器
    analyzer = LangSmithTraceAnalyzer()
    
    # 示例追踪数据
    trace_data = {
        "trace_id": "test-trace-001",
        "root": {
            "id": "root",
            "type": "agent",
            "timestamp": "2024-01-01T10:00:00",
            "duration": 5.2,
            "metadata": {"model": "gpt-4"},
            "input": {"messages": [{"role": "user", "content": "Hello"}]},
            "output": {"content": "Hi there!"}
        },
        "children": [
            {
                "id": "tool-1",
                "type": "tool_call",
                "timestamp": "2024-01-01T10:00:01",
                "duration": 1.5,
                "metadata": {"tool": "read_file"},
                "input": {"file_path": "test.txt"},
                "output": {"content": "File content"}
            },
            {
                "id": "tool-2",
                "type": "tool_call",
                "timestamp": "2024-01-01T10:00:03",
                "duration": 2.1,
                "metadata": {"tool": "write_file"},
                "input": {"content": "New content"},
                "output": {"status": "success"}
            }
        ]
    }
    
    # 分析追踪数据
    analysis = analyzer.analyze_trace(trace_data)
    
    # 生成报告
    report = analyzer.generate_performance_report(analysis)
    print(report)
'''