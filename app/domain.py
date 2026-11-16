class DomainError(ValueError):
    """领域错误：携带稳定原因码，便于 API 层映射为 422 与页面提示。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)
