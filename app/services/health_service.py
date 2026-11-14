"""应用健康状态服务。"""


class HealthService:
    def get_status(self) -> str:
        """返回应用存活状态。"""
        return "ok"
