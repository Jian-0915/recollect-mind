"""NeuroMem - 一个从空白出发、会记、会忘、会联想的成长型记忆系统（MVP-1）。"""

__version__ = "0.1.0"


def __getattr__(name):
    """惰性导出 Memory SDK，避免包初始化时触发子模块循环导入。

    支持 README 中声明的 `from neuromem import Memory`；
    原来的 `from neuromem.memory import Memory` 仍然可用。
    """
    if name == "Memory":
        from .memory import Memory
        return Memory
    if name == "Brain":
        from .brain import Brain
        return Brain
    if name == "ContextPack":
        from .brain import ContextPack
        return ContextPack
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


__all__ = ["Memory", "Brain", "ContextPack", "__version__"]