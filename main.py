"""萌萌好感度 — 全插件共用的好感度中枢（仓库骨架）。

本版本只建立工程骨架：数据表、事件模型、对外契约、迁移工具与对话注入
都还没有实现。enabled 默认 false，装载后不会改变宿主机任何对话行为。

规划中的三层模型（尚未落地，细节仍在讨论）：

- 关系层：以 (人格, 用户) 为一份，事件驱动，读时结算闲置衰减；
- 情绪层：以人格为一份的四轴心情，半衰期读时投影、不写库；
- 状态层：精力/困倦/忙碌等由世界时钟投影，只存锚点不存当前值。

跨插件契约：别的插件通过
``context.get_registered_star("astrbot_plugin_moe_affinity").star_cls.api``
取得本插件的 API 对象，并以 ``API_VERSION`` 做兼容判断。
"""

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_moe_affinity"
PLUGIN_VERSION = "v0.1.0"
API_VERSION = "affinity.v1"


class AffinityApi:
    """跨插件契约的占位实现。

    真正的读写方法在数据层定稿后再实现；在此之前所有入口一律返回
    ``status="unavailable"``，让调用方能够安全降级，而不是误以为分数为 0。
    """

    API_VERSION = API_VERSION

    def __init__(self, plugin: "MoeAffinityPlugin"):
        self._plugin = plugin

    def fingerprint(self) -> str:
        """契约指纹，用于调用方判断是否同一套实现（骨架阶段固定值）。"""
        return f"{API_VERSION}:skeleton"

    async def get(self, persona_id: str, user_id: str) -> dict:
        return {
            "status": "unavailable",
            "message": "好感度中枢尚未实现数据层。",
        }

    async def add_event(
        self,
        persona_id: str,
        user_id: str,
        *,
        source: str,
        code: str,
        delta: int | None = None,
        dedupe_key: str | None = None,
        evidence: str | None = None,
    ) -> dict:
        return {
            "status": "unavailable",
            "message": "好感度中枢尚未实现数据层。",
        }


@register(
    PLUGIN_NAME,
    "windExplorer",
    "萌萌好感度：全插件共用的好感度中枢（骨架版本）",
    PLUGIN_VERSION,
)
class MoeAffinityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self.api = AffinityApi(self)

        if not self.config.get("enabled", False):
            logger.info("[moe_affinity] 已装载（骨架版本），enabled=false，不接管任何行为。")
        else:
            logger.warning(
                "[moe_affinity] enabled=true，但当前为骨架版本，"
                "数据表与对外契约尚未实现，不会有任何实际行为。"
            )

    async def terminate(self):
        """插件卸载/重载时调用。骨架阶段无资源需要释放。"""
        return None
