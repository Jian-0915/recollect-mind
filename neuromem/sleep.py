"""定时代谢（sleep）：周期性跑 evolve（巩固/再抽象/遗忘/快照）。"""
import time

from . import evolve


def run(conn, interval=60, cycles=None, verbose=True):
    """interval 秒一轮；cycles 为 None 时一直跑。返回已运行轮数。"""
    count = 0
    while True:
        r = evolve.tick(conn)
        count += 1
        if verbose:
            print("[sleep] 第%d轮: 新概念%d 升层%d 归档%d"
                  % (count, len(r["new_concepts"]), len(r["abstracted"]),
                     r["decay"]["newly_archived"]))
        if cycles is not None and count >= cycles:
            break
        time.sleep(interval)
    return count