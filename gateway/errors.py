"""统一异常定义。"""


class ADGError(Exception):
    """编排器基础异常。"""


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
