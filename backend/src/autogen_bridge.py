"""
AutoGen ↔ Flask 流式桥接器

将 AutoGen 的 asyncio 异步事件流桥接到 Flask 的同步 NDJSON 流式生成器。
使用 threading.Queue 作为跨线程通信媒介。
"""

import asyncio
import logging
import threading
import queue
import json
from typing import Generator

logger = logging.getLogger(__name__)


class AutoGenStreamBridge:
    """
    将 AutoGen asyncio pipeline 的事件桥接为 Flask stream_with_context 可消费的同步生成器。

    用法：
        bridge = AutoGenStreamBridge()
        bridge.run_in_thread(run_autogen_pipeline(bridge, ...))
        yield from bridge.flask_generator()
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._SENTINEL = object()  # 用于标记流结束

    def run_in_thread(self, coroutine) -> threading.Thread:
        """
        在独立线程中启动一个新的 asyncio 事件循环来运行协程。
        协程结束（正常或异常）后，自动向队列发送 SENTINEL 信号。
        """
        def _runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coroutine)
            except Exception as e:
                logger.exception("Pipeline 内部错误（完整堆栈）")
                self.put_event({
                    'type': 'error',
                    'message': f'Pipeline 内部错误: {str(e)}'
                })
            finally:
                loop.close()
                self._queue.put(self._SENTINEL)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        return thread

    def put_event(self, event_dict: dict) -> None:
        """
        从异步上下文（AutoGen pipeline）中发送一个 NDJSON 事件到 Flask 生成器。
        线程安全，可在任意线程调用。
        """
        self._queue.put(json.dumps(event_dict, ensure_ascii=False) + '\n')

    def flask_generator(self) -> Generator[str, None, None]:
        """
        Flask stream_with_context 使用的同步生成器。
        阻塞等待队列中的事件，直到收到 SENTINEL 信号结束。
        """
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                break
            yield item
