from codepilot.config import RepositoryConfig
from codepilot.tools.files import FileTools
from codepilot.tools.registry import ToolRegistry
from codepilot.tools.search import SearchTool
from codepilot.tools.workspace import Workspace


def create_read_only_registry(config: RepositoryConfig) -> ToolRegistry:
    workspace = Workspace(config)
    registry = ToolRegistry(config.max_output_chars)
    for tool in FileTools(workspace).tools():
        registry.register(tool)
    registry.register(SearchTool(workspace).tool())
    return registry
