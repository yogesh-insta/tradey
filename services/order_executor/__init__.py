"""Order Executor — broker adapters, paper guards, execution service."""

from services.order_executor.config import ExecutorConfig
from services.order_executor.execution_service import ExecutionService
from services.order_executor.paper_guard import (
    PaperGuardResult,
    assert_paper_safe,
    check_paper_guard,
)
from services.order_executor.brokers import get_adapter

__all__ = [
    "ExecutorConfig",
    "ExecutionService",
    "PaperGuardResult",
    "assert_paper_safe",
    "check_paper_guard",
    "get_adapter",
]
