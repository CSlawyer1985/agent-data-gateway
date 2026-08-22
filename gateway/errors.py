"""统一异常定义。"""


class ADGError(Exception):
    """编排器基础异常。"""


class ContractViolation(ADGError, ValueError):
    """公开协议或持久化 Schema 不满足约定。"""


class DataIntegrityError(ContractViolation):
    """持久化数据的身份、引用或 payload 哈希校验失败。"""


class ConcurrentWriteError(ADGError):
    """同一输出目录存在并发写入或写入期间状态已变化。"""


class SecurityViolation(ADGError):
    """安全策略拦截（路径穿越、符号链接逃逸、超限等）。"""


class WorkerUnavailable(ADGError):
    """worker 不可用（未安装、硬件不满足、后端缺失）。"""


class WorkerFailed(ADGError):
    """worker 执行失败（崩溃、超时、返回错误）。"""

    def __init__(self, message: str, *, kind: str = "crash"):
        super().__init__(message)
        self.kind = kind


class UnsupportedFormat(ADGError):
    """明确不支持的格式。"""


class EncryptedFile(ADGError):
    """文件加密，无法在无密码情况下读取。"""
