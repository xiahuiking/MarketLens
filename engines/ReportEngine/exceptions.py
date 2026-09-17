"""
ReportEngine 专用异常。

ReportCancelledError 用于协作式取消：当外部（app 服务层）请求取消正在生成的报告时，
流式回调抛出该异常，节点层据此中断当前章节并向上传播，最终终止 LangGraph 执行。
"""


class ReportCancelledError(Exception):
    """报告生成被协作式取消。"""

    def __init__(self, message: str = "报告生成已取消"):
        super().__init__(message)
        self.message = message
